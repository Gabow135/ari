import logging
from datetime import datetime, timezone

from ari.domain.agent.message import Message
from ari.domain.memory.fact_keys import normalize_key
from ari.domain.memory.memory_port import MemoryPort
from ari.domain.ports.llm_port import LLMPort

log = logging.getLogger("ari.memory_maintainer")

_FACT_SYSTEM = (
    "Extract durable facts about the user from their message. "
    "Reply ONLY with lines 'key: value'. If none, reply with nothing.")

_CONFLICT_SYSTEM = (
    "You are a fact conflict resolver. Given a stored fact and a new candidate value, "
    "decide whether to supersede, keep, or merge them.\n\n"
    "Reply on the FIRST line with exactly one of: SUPERSEDE, KEEP, or MERGE.\n"
    "If MERGE, put the merged value on the SECOND line.\n"
    "SUPERSEDE: the new value is clearly more current/correct — use it.\n"
    "KEEP: the old value is more reliable — discard the new one.\n"
    "MERGE: both carry information worth keeping — combine them."
)


class MemoryMaintainer:
    def __init__(self, memory: MemoryPort, llm: LLMPort, summary_threshold: int = 40):
        self._memory = memory
        self._llm = llm
        self._threshold = summary_threshold

    async def extract_facts(self, user_id: str, user_text: str) -> None:
        try:
            existing = await self._memory.get_facts(user_id)
            system = _FACT_SYSTEM
            if existing:
                key_list = ", ".join(f.key for f in existing)
                system = (
                    system + f" Reuse these existing keys when they apply instead of "
                    f"inventing synonyms: {key_list}."
                )
            probe = [Message(user_id, "user", user_text, datetime.now(timezone.utc))]
            raw = await self._llm.complete(system, probe, max_tokens=256)
            for line in raw.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    key, value = normalize_key(key), value.strip()
                    if not key or not value:
                        continue
                    current = await self._memory.get_fact(user_id, key)
                    if current is None:
                        await self._memory.upsert_fact(user_id, key, value)
                        await self._memory.add_fact_history(user_id, key, None, value, "new")
                    elif current.value == value:
                        pass  # identical — nothing to do
                    else:
                        resolution, final_value = await self._judge_conflict(
                            key, current.value, value
                        )
                        if final_value != current.value:
                            await self._memory.upsert_fact(user_id, key, final_value)
                        await self._memory.add_fact_history(
                            user_id, key, current.value, value, resolution
                        )
        except Exception:
            log.exception("fact extraction failed")

    async def _judge_conflict(
        self, key: str, old: str, new: str
    ) -> tuple[str, str]:
        """Call the LLM to decide SUPERSEDE/KEEP/MERGE.  Safe default: KEEP."""
        try:
            user_prompt = (
                f"Fact key: {key}\n"
                f"Stored value: {old}\n"
                f"New candidate: {new}"
            )
            probe = [Message("system", "user", user_prompt, datetime.now(timezone.utc))]
            raw = await self._llm.complete(_CONFLICT_SYSTEM, probe, max_tokens=128)
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            if not lines:
                return ("keep", old)
            verb = lines[0].strip().split()[0].upper()
            if verb == "SUPERSEDE":
                return ("supersede", new)
            elif verb == "KEEP":
                return ("keep", old)
            elif verb == "MERGE":
                merged = lines[1].strip() if len(lines) > 1 else f"{old}; {new}"
                if not merged:
                    merged = f"{old}; {new}"
                return ("merge", merged)
            else:
                return ("keep", old)
        except Exception:
            log.exception("conflict judge failed — keeping old value")
            return ("keep", old)

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
