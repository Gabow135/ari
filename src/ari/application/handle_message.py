import asyncio
import logging
from datetime import datetime, timezone

from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.memory.memory_port import MemoryPort
from ari.domain.ports.embeddings_port import EmbeddingsPort
from ari.domain.ports.gateway_port import IncomingMessage, OutgoingMessage
from ari.domain.ports.llm_port import LLMPort

log = logging.getLogger("ari.handle_message")
BLANK_REPLY = "Mandame un mensaje de texto y con gusto te ayudo."


class HandleMessage:
    def __init__(self, memory: MemoryPort, llm: LLMPort,
                 embeddings: EmbeddingsPort, agent: AgentService,
                 working_memory_size: int = 20, recall_top_k: int = 5,
                 maintainer=None, scheduler=None):
        self._memory = memory
        self._llm = llm
        self._embeddings = embeddings
        self._agent = agent
        self._n = working_memory_size
        self._k = recall_top_k
        self._maintainer = maintainer
        self._schedule = scheduler or (lambda coro: asyncio.create_task(coro))

    async def __call__(self, incoming: IncomingMessage) -> OutgoingMessage:
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

        system = self._agent.build_prompt(facts, summary, recalls)
        reply = await self._llm.complete(system, history)

        await self._memory.append_message(
            Message(incoming.user_id, "assistant", reply, datetime.now(timezone.utc)))
        await self._store_recall(incoming.user_id, text, reply)

        if self._maintainer is not None:
            self._schedule(self._maintainer.extract_facts(incoming.user_id, text))
            self._schedule(self._maintainer.maybe_summarize(incoming.user_id))

        return OutgoingMessage(incoming.chat_id, reply)

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
