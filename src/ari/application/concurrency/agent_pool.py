import asyncio
from typing import Self


class AgentPool:
    """A bounded pool that caps how many background agents run at once.

    Missions, scheduled tasks, and coding acquire the pool before launching their
    Claude work::

        async with pool:
            await run_the_heavy_turn()

    Excess work waits for a free slot instead of spawning an unbounded number of
    ``claude`` subprocesses that would thrash the host. The interactive chat path
    does NOT use this pool (chat is capped separately by PTB's
    ``concurrent_updates``), so a background flood never starves chat.
    """

    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError("AgentPool size must be >= 1")
        self._size = size
        self._sem = asyncio.Semaphore(size)

    @property
    def size(self) -> int:
        return self._size

    async def __aenter__(self) -> Self:
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._sem.release()
        return False
