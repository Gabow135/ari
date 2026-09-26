import json
import logging
from datetime import datetime, timedelta

from ari.application.schedule.schedule_actions import extract_actions
from ari.domain.agent.message import Message
from ari.domain.schedule.quiet_hours import is_quiet
from ari.domain.schedule.timefmt import fmt_long, fmt_short

log = logging.getLogger("ari.heartbeat")

NADA = "NADA"
_LAST = "heartbeat.last_at"
_INSTRUCTION = ("(Latido automático: nadie te escribió.) Revisa según tus instrucciones "
                "de latido. Si no hay nada que realmente valga la pena, responde "
                "exactamente NADA.")


class Heartbeat:
    """Owner-only initiative: every ``interval_minutes`` (outside quiet hours) Ari
    decides whether it has something worth saying; ``NADA`` means stay silent."""

    def __init__(self, *, llm, memory, store, agent, soul, checklist, owners, send,
                 tz, quiet, interval_minutes: int, clock, daily_max: int = 3, tools=None):
        self._llm, self._memory, self._store, self._agent = llm, memory, store, agent
        self._soul, self._checklist, self._owners = soul, checklist, sorted(owners)
        self._send, self._tz, self._quiet, self._clock = send, tz, quiet, clock
        self._interval = timedelta(minutes=interval_minutes)
        self._daily_max = daily_max
        self._tools = tools  # ToolPolicy | None

    async def __call__(self) -> None:
        if self._interval <= timedelta(0) or not self._owners:
            return
        now = self._clock()
        if is_quiet(now.astimezone(self._tz), self._quiet):
            return
        last = await self._store.kv_get(_LAST)
        if last is None:  # first ever run: start counting from now
            await self._store.kv_set(_LAST, now.isoformat())
            return
        if now - datetime.fromisoformat(last) < self._interval:
            return
        await self._store.kv_set(_LAST, now.isoformat())
        for owner in self._owners:
            try:
                await self._beat(owner, now)
            except Exception:  # noqa: BLE001 — retried next beat
                log.exception("heartbeat failed for %s", owner)

    async def _beat(self, owner: str, now: datetime) -> None:
        sent_key = f"heartbeat.sent:{owner}:{now.astimezone(self._tz).date().isoformat()}"
        sent = int(await self._store.kv_get(sent_key) or 0)
        if sent >= self._daily_max:
            return
        recent_key = f"heartbeat.recent:{owner}"
        recent = json.loads(await self._store.kv_get(recent_key) or "[]")
        view = self._tools.view(owner) if self._tools else None
        toolset = self._tools.for_user(owner) if self._tools else None
        system = self._agent.build_prompt(
            await self._memory.get_facts(owner), await self._memory.get_summary(owner), [],
            soul=self._soul(), is_owner=True, extra=await self._section(owner, now, recent),
            tools=view)
        history = await self._memory.recent_messages(owner, 10)
        raw = await self._llm.complete(
            system, [*history, Message(owner, "user", _INSTRUCTION, now)],
            **({"toolset": toolset} if toolset is not None else {}))
        reply, _ignored = extract_actions(raw)  # heartbeat may suggest, never schedule
        if not reply or reply.strip().strip(".!¡ ").upper() == NADA:
            return
        text = f"💡 {reply}"
        await self._send(owner, text)
        await self._memory.append_message(Message(owner, "assistant", text, now))
        await self._store.kv_set(sent_key, str(sent + 1))
        await self._store.kv_set(recent_key, json.dumps([*recent, reply][-5:], ensure_ascii=False))

    async def _section(self, owner: str, now: datetime, recent: list[str]) -> str:
        items = await self._store.upcoming(owner, now + timedelta(hours=24))
        upcoming = "\n".join(f"- #{i.id} · {fmt_short(i.next_run_at, self._tz)} · {i.text}"
                             for i in items) or "Nada agendado."
        said = "\n".join(f"- {r}" for r in recent) or "Nada todavía."
        checklist = (self._checklist() or "").strip() or "Escribe solo si aporta valor real."
        return (f"## Latido (iniciativa propia)\nAhora: {fmt_long(now, self._tz)}. "
                f"Esto es un latido automático: nadie te escribió.\n\n"
                f"### Qué revisar\n{checklist}\n\n"
                f"### Próximas 24 h\n{upcoming}\n\n"
                f"### Lo último que dijiste por iniciativa propia (no lo repitas)\n{said}\n\n"
                f"Si no hay nada que realmente valga la pena, responde exactamente: {NADA}")
