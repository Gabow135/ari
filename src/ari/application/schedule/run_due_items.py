import asyncio
import logging
from datetime import timedelta

from ari.domain.schedule.actions import next_cron_run
from ari.domain.schedule.entities import PAUSED, TASK, ScheduleItem

log = logging.getLogger("ari.schedule")

LATE = timedelta(minutes=2)  # beyond normal tick jitter
VERY_LATE = timedelta(hours=1)
RETRY = timedelta(minutes=5)
MAX_FAILURES = 3


class RunDueItems:
    """One pass: fire every due reminder/task. Reminders are sent inline (fast);
    tasks run as background coroutines so a slow Claude turn never delays
    anyone else's reminder. ``send`` must never raise."""

    def __init__(self, store, send, run_task, on_paused, tz, clock):
        self._store, self._send, self._run = store, send, run_task
        self._on_paused, self._tz, self._clock = on_paused, tz, clock
        self._inflight: set[asyncio.Task] = set()

    async def __call__(self) -> None:
        now = self._clock()
        for item in await self._store.claim_due(now):
            if item.kind == TASK:
                task = asyncio.ensure_future(self._run_task(item, now))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
            else:
                try:
                    await self._remind(item, now)
                except Exception:  # noqa: BLE001 — isolate each item
                    log.exception("reminder #%s failed", item.id)

    async def drain(self) -> None:
        await asyncio.gather(*list(self._inflight), return_exceptions=True)

    async def _remind(self, item: ScheduleItem, now) -> None:
        late = now - item.next_run_at
        if late >= VERY_LATE:
            text = f"⚠️ Mientras estuve apagada no pude recordarte: {item.text}"
        else:
            text = f"⏰ Recordatorio: {item.text}" + (" (con retraso)" if late >= LATE else "")
        await self._send(item.chat_id, text)
        await self._advance(item, now)

    async def _run_task(self, item: ScheduleItem, now) -> None:
        try:
            reply = await self._run(item)
        except Exception as exc:  # noqa: BLE001 — isolate each item
            log.exception("scheduled task #%s failed", item.id)
            try:
                # record_failure returns 0 if the item is no longer RUNNING
                # (e.g. its user's access was revoked mid-flight, cancelling
                # it): nothing to pause or notify about in that case.
                failures = await self._store.record_failure(item.id, now + RETRY)
                if failures >= MAX_FAILURES:
                    await self._store.set_status(item.id, PAUSED)
                    await self._on_paused(item, str(exc)[:200])
            except Exception:  # noqa: BLE001 — never let the failure path itself
                log.exception("scheduled task #%s: failure handling itself failed", item.id)
            return
        try:
            await self._send(item.chat_id, f"🔁 Tarea #{item.id}: {reply}")
            await self._advance(item, now)
        except Exception:  # noqa: BLE001 — isolate each item
            log.exception("scheduled task #%s: delivery/reschedule failed", item.id)

    async def _advance(self, item: ScheduleItem, now) -> None:
        if item.cron:
            await self._store.reschedule(item.id, next_cron_run(item.cron, now, self._tz), now)
        else:
            await self._store.finish(item.id, now)
