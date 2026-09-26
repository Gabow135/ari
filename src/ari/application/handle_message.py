import asyncio
import logging
from datetime import datetime, timezone

from ari.application.schedule.schedule_actions import extract_actions
from ari.application.tools.tool_policy import no_turn
from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.memory.memory_port import MemoryPort
from ari.domain.ports.embeddings_port import EmbeddingsPort
from ari.domain.ports.gateway_port import IncomingMessage, OutgoingMessage
from ari.domain.ports.llm_port import LLMPort, LLMTimeoutError
from ari.domain.tools.ari_permissions import CHAT, TASK

log = logging.getLogger("ari.handle_message")
BLANK_REPLY = "Mándame un mensaje de texto y con gusto te ayudo."
TIMEOUT_REPLY = ("Me tardé demasiado con las herramientas; intenta con algo más "
                 "acotado. Si me pediste agendar algo, revisa /recordatorios antes "
                 "de repetirlo.")


class HandleMessage:
    def __init__(self, memory: MemoryPort, llm: LLMPort,
                 embeddings: EmbeddingsPort, agent: AgentService,
                 working_memory_size: int = 20, recall_top_k: int = 5,
                 maintainer=None, scheduler=None, soul=None, is_owner=None,
                 actions=None, tools=None, turn_log=None, after_turn=None):
        self._memory = memory
        self._llm = llm
        self._embeddings = embeddings
        self._agent = agent
        self._n = working_memory_size
        self._k = recall_top_k
        self._maintainer = maintainer
        self._schedule = scheduler or (lambda coro: asyncio.create_task(coro))
        self._soul = soul or (lambda: None)  # () -> SOUL.md text | None
        self._is_owner = is_owner or (lambda _uid: False)
        self._actions = actions  # ScheduleActions | None
        self._tools = tools  # ToolPolicy | None
        self._turn_log = turn_log  # SqliteTurnLog | None
        self._after_turn = after_turn  # async () -> None, e.g. OutboxFlusher

    async def __call__(self, incoming: IncomingMessage,
                       allow_actions: bool = True) -> OutgoingMessage:
        text = incoming.text.strip()
        if not text:
            return OutgoingMessage(incoming.chat_id, BLANK_REPLY)

        now = datetime.now(timezone.utc)
        user_msg = Message(incoming.user_id, "user", text, now)
        await self._memory.append_message(user_msg)

        history = await self._memory.recent_messages(incoming.user_id, self._n)
        facts = await self._safe(self._memory.get_facts(incoming.user_id), [])
        summary = await self._safe(self._memory.get_summary(incoming.user_id), None)
        recalls = await self._retrieve(incoming.user_id, text)

        context = CHAT if allow_actions else TASK
        is_owner = self._is_owner(incoming.user_id)
        extra = (await self._safe(self._actions.context(incoming.user_id, context, is_owner),
                                  None)
                 if self._actions else None)

        turn_cm = (self._tools.turn(incoming.user_id, context, incoming.display_name,
                                    incoming.chat_id) if self._tools else no_turn())
        timed_out = False
        error: Exception | None = None
        try:
            async with turn_cm as turn:
                system = self._agent.build_prompt(
                    facts, summary, recalls, soul=self._soul(),
                    is_owner=is_owner, extra=extra,
                    tools=turn.view if turn else None)
                try:
                    reply = await self._llm.complete(
                        system, history,
                        **({"toolset": turn.toolset} if turn is not None else {}))
                except LLMTimeoutError:
                    log.warning("chat call timed out for %s", incoming.user_id)
                    if not allow_actions:
                        # Scheduled task run: let RunDueItems count it as a failure.
                        raise
                    reply, timed_out = TIMEOUT_REPLY, True
                except Exception as exc:
                    if not allow_actions:
                        # Scheduled task run: let RunDueItems count it as a failure.
                        raise
                    log.warning("chat call failed for %s: %s", incoming.user_id, exc)
                    error = exc
            if error is not None:
                # A tool may have run before the LLM call blew up: show what stuck.
                receipts = await self._receipts(turn)
                if receipts:
                    return OutgoingMessage(
                        incoming.chat_id,
                        "Tuve un problema al terminar la respuesta, pero esto sí quedó "
                        "hecho:\n\n" + "\n".join(receipts))
                raise error
            reply, _legacy = extract_actions(reply)  # stray legacy blocks stay hidden
            receipts = await self._receipts(turn)
            if receipts:
                reply = "\n\n".join(p for p in (reply, "\n".join(receipts)) if p)
            if timed_out:
                return OutgoingMessage(incoming.chat_id, reply)
        finally:
            if self._after_turn is not None:
                try:
                    await self._after_turn()
                except Exception:
                    log.exception("after-turn flush failed")

        await self._memory.append_message(
            Message(incoming.user_id, "assistant", reply, datetime.now(timezone.utc)))
        await self._store_recall(incoming.user_id, text, reply)

        if self._maintainer is not None:
            self._schedule(self._maintainer.extract_facts(incoming.user_id, text))
            self._schedule(self._maintainer.maybe_summarize(incoming.user_id))

        return OutgoingMessage(incoming.chat_id, reply)

    async def _receipts(self, turn) -> list[str]:
        if turn is None or self._turn_log is None:
            return []
        return await self._safe(self._turn_log.receipts(turn.turn_id), [])

    async def _retrieve(self, user_id, text):
        try:
            vec = (await self._embeddings.embed([text]))[0]
            return await self._memory.retrieve_recalls(user_id, vec, self._k)
        except Exception:  # graceful degradation
            log.exception("recall retrieval failed; answering from working memory")
            return []

    async def _store_recall(self, user_id, text, reply):
        try:
            content = f"User: {text}\nAri: {reply}"
            vec = (await self._embeddings.embed([content]))[0]
            await self._memory.store_recall(user_id, content, vec, {})
        except Exception:
            log.exception("storing recall failed")

    @staticmethod
    async def _safe(awaitable, default):
        try:
            return await awaitable
        except Exception:
            log.exception("memory read failed; using default")
            return default
