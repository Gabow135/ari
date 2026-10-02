"""MemoryConsolidator — scheduled job that decays and prunes recalls.

Throttled via a KV timestamp so it runs at most once per ``interval_hours``
regardless of how frequently the scheduler ticks.
"""
import logging
from datetime import datetime, timedelta

log = logging.getLogger("ari.consolidator")


class MemoryConsolidator:
    """Scheduled job: decay recall importance and prune faded, old, unpinned recalls.

    Throttled via kv so it runs about every interval_hours regardless of tick rate.
    """

    _LAST = "consolidator.last_at"

    def __init__(self, memory, kv, clock, *, interval_hours: int, decay_factor: float,
                 prune_floor: float, prune_min_age_days: int) -> None:
        self._memory = memory
        self._kv = kv
        self._clock = clock
        self._interval_hours = interval_hours
        self._decay_factor = decay_factor
        self._prune_floor = prune_floor
        self._prune_min_age_days = prune_min_age_days

    async def __call__(self) -> None:
        try:
            now = self._clock()
            last = await self._kv.kv_get(self._LAST)
            if last is not None and now - datetime.fromisoformat(last) < timedelta(
                hours=self._interval_hours
            ):
                return
            await self._kv.kv_set(self._LAST, now.isoformat())
            await self._memory.decay_recalls(self._decay_factor)
            older_than = (now - timedelta(days=self._prune_min_age_days)).isoformat()
            await self._memory.prune_recalls(self._prune_floor, older_than)
        except Exception:
            log.exception("memory consolidation failed")
