import logging

log = logging.getLogger("ari.llm_health")


class MonitoredLLM:
    """LLMPort decorator: tells ``listener`` when the Claude CLI fails
    ``threshold`` times in a row, and checks for recovery on every success
    (the listener persists the "down" flag, so this also covers restarts)."""

    def __init__(self, llm, threshold: int = 3):
        self._llm, self._threshold, self._fails = llm, threshold, 0
        self.listener = None  # SystemNotices, bound once it exists

    async def complete(self, system, messages, max_tokens: int = 1024, toolset=None) -> str:
        extra = {"toolset": toolset} if toolset is not None else {}
        try:
            reply = await self._llm.complete(system, messages, max_tokens=max_tokens, **extra)
        except Exception as exc:
            self._fails += 1
            if self._fails == self._threshold:
                await self._notify("cli_failed", str(exc))
            raise
        self._fails = 0
        await self._notify("cli_recovered")
        return reply

    async def _notify(self, method: str, *args) -> None:
        if self.listener is None:
            return
        try:
            await getattr(self.listener, method)(*args)
        except Exception:  # noqa: BLE001 — monitoring must never break a reply
            log.exception("llm health listener failed")
