import logging
from datetime import datetime, timezone

from ari.domain.agent.message import Message
from ari.domain.memory.memory_port import MemoryPort
from ari.domain.ports.llm_port import LLMPort

log = logging.getLogger("ari.memory_maintainer")

_FACT_SYSTEM = (
    "Extract durable facts about the user from their message. "
    "Reply ONLY with lines 'key: value'. If none, reply with nothing.")


class MemoryMaintainer:
    def __init__(self, memory: MemoryPort, llm: LLMPort, summary_threshold: int = 40):
        self._memory = memory
        self._llm = llm
        self._threshold = summary_threshold

    async def extract_facts(self, user_id: str, user_text: str) -> None:
        try:
            probe = [Message(user_id, "user", user_text, datetime.now(timezone.utc))]
            raw = await self._llm.complete(_FACT_SYSTEM, probe, max_tokens=256)
            for line in raw.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    key, value = key.strip(), value.strip()
                    if key and value:
                        await self._memory.upsert_fact(user_id, key, value)
        except Exception:
            log.exception("fact extraction failed")

    async def maybe_summarize(self, user_id: str) -> None:
        try:
            history = await self._memory.recent_messages(user_id, self._threshold + 1)
            if len(history) <= self._threshold:
                return
            prior = await self._memory.get_summary(user_id)
            parts: list[str] = []
            if prior is not None:
                parts.append(f"Previous summary:\n{prior.content}")
            parts.append(
                "Recent messages:\n"
                + "\n".join(f"{m.role}: {m.content}" for m in history)
            )
            joined = "\n\n".join(parts)
            probe = [Message(user_id, "user", joined, history[-1].created_at)]
            summary = await self._llm.complete(
                "Produce an updated rolling summary of this conversation. "
                "Incorporate the previous summary (if any) with the recent messages "
                "so that no earlier context is lost.",
                probe,
                max_tokens=512,
            )
            await self._memory.upsert_summary(user_id, summary)
        except Exception:
            log.exception("summarization failed")
