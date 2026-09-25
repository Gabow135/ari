import asyncio
import contextlib
import logging

log = logging.getLogger("ari.scheduler")


class Scheduler:
    """Runs each job every ``interval`` seconds, in order, on the bot's loop.
    A failing job is logged and never stops the others or the loop."""

    def __init__(self, jobs, interval: float = 30.0):
        self._jobs, self._interval = list(jobs), interval
        self._task: asyncio.Task | None = None

    async def tick(self) -> None:
        for job in self._jobs:
            try:
                await job()
            except Exception:  # noqa: BLE001
                log.exception("scheduler job %r failed", job)

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._interval)
