import asyncio
import logging
import re
import time

from ari.domain.ports.progress_port import TEXT, THINKING, TOOL, ProgressEvent, current_progress

log = logging.getLogger("ari.telegram")

_MAX_STEPS = 8
_MAX_TEXT = 2500  # tail of the partial answer shown; keeps us under 4096
# Action blocks (<ari-action>{...}</ari-action>) are plumbing for
# ari.application.schedule.schedule_actions, never meant for the user's eyes.
# Kept as a small local regex rather than importing extract_actions from the
# application layer: infrastructure has no existing dependency on application
# elsewhere in this codebase, and this presentation-only concern (hide partial
# JSON while it streams) doesn't warrant introducing that coupling.
_ACTION_BLOCK = re.compile(r"<ari-action>.*?</ari-action>", re.S | re.I)
_ACTION_DANGLING = re.compile(r"<ari-action>.*\Z", re.S | re.I)


def _hide_actions(text: str) -> str:
    return _ACTION_DANGLING.sub("", _ACTION_BLOCK.sub("", text))
_TOOL_LABELS = {
    "Read": "📖 Leyendo", "Edit": "✏️ Editando", "Write": "✏️ Escribiendo",
    "MultiEdit": "✏️ Editando", "Bash": "▶️ Ejecutando", "Grep": "🔎 Buscando",
    "Glob": "🔎 Buscando", "WebFetch": "🌐 Abriendo", "WebSearch": "🌐 Buscando",
    "Agent": "🤖 Consultando a un subagente", "Task": "🤖 Consultando a un subagente",
    "TodoWrite": "📝 Organizando tareas", "ExitPlanMode": "📋 Cerrando el plan",
}
# Internal plumbing that means nothing to the user.
_HIDDEN_TOOLS = {"ToolSearch"}


def _step(event: ProgressEvent) -> str | None:
    if event.kind == THINKING:
        return "🤔 Pensando…"
    if event.name in _HIDDEN_TOOLS:
        return None
    label = _TOOL_LABELS.get(event.name)
    if label is None:
        return f"🔧 {event.name}"
    if event.name in ("Agent", "Task", "TodoWrite", "ExitPlanMode"):
        return label  # their input is not a short, readable target
    return f"{label} {event.detail}".rstrip()


class ProgressMessage:
    """Telegram presentation of one turn: keeps "typing…" alive and shows a single
    progress message (steps + partial answer) that is deleted on exit, so only
    the final reply remains. All Telegram errors are swallowed: progress is
    cosmetic and must never break the reply."""

    def __init__(self, bot, chat_id, min_interval: float = 1.5,
                 typing_interval: float = 4.0, clock=time.monotonic):
        self._bot, self._chat = bot, chat_id
        self._min_interval, self._typing_interval = min_interval, typing_interval
        self._clock = clock
        self._steps: list[str] = []
        self._text = ""
        self._shown = ""
        self._message_id = None
        self._last_flush = float("-inf")
        self._flusher: asyncio.Task | None = None
        self._typing: asyncio.Task | None = None
        self._send_task: asyncio.Future | None = None
        self._closed = False
        self._token = None

    async def __aenter__(self):
        self._token = current_progress.set(self)
        self._typing = asyncio.ensure_future(self._keep_typing())
        return self

    async def __aexit__(self, *exc):
        self._closed = True
        current_progress.reset(self._token)
        for task in (self._typing, self._flusher):
            if task is not None:
                task.cancel()
        if self._message_id is None and self._send_task is not None:
            # The first send may still be in flight: wait for it, or the message
            # would arrive after we are gone and never be deleted.
            msg = await self._send_task
            self._message_id = getattr(msg, "message_id", None)
        if self._message_id is not None:
            await self._quietly(lambda: self._bot.delete_message(
                chat_id=self._chat, message_id=self._message_id))
        return False

    def emit(self, event: ProgressEvent) -> None:
        if self._closed:
            return
        if event.kind == TEXT:
            self._text += event.detail
        else:
            step = _step(event)
            if step is None:
                return
            if not self._steps or self._steps[-1] != step:
                self._steps.append(step)
            if event.kind == TOOL:
                self._text = ""  # narration before a tool call is superseded
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.ensure_future(self._flush())

    def _render(self) -> str:
        lines = self._steps[-_MAX_STEPS:]
        text = _hide_actions(self._text).strip()
        if text:
            if len(text) > _MAX_TEXT:
                text = "…" + text[-_MAX_TEXT:]
            lines = [*lines, "", f"✍️ {text}"]
        return "\n".join(lines).strip()

    async def _flush(self) -> None:
        wait = self._last_flush + self._min_interval - self._clock()
        if wait > 0:
            await asyncio.sleep(wait)
        while not self._closed:
            content = self._render()
            if not content or content == self._shown:
                return
            self._last_flush = self._clock()
            self._shown = content
            if self._message_id is None:
                self._send_task = asyncio.ensure_future(self._quietly(
                    lambda c=content: self._bot.send_message(chat_id=self._chat, text=c)))
                msg = await asyncio.shield(self._send_task)  # survives our cancel
                self._message_id = getattr(msg, "message_id", None)
            else:
                await self._quietly(lambda c=content: self._bot.edit_message_text(
                    c, chat_id=self._chat, message_id=self._message_id))
            await asyncio.sleep(self._min_interval)

    async def _keep_typing(self) -> None:
        while not self._closed:
            await self._quietly(
                lambda: self._bot.send_chat_action(chat_id=self._chat, action="typing"))
            await asyncio.sleep(self._typing_interval)

    @staticmethod
    async def _quietly(call):
        """Await ``call()``; errors (even building the call) are logged, not raised."""
        try:
            return await call()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — progress is best-effort
            log.debug("progress update failed: %s", exc)
            return None
