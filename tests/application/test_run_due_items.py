import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.run_due_items import RunDueItems
from ari.domain.schedule.entities import ACTIVE, CANCELLED, DONE, PAUSED, REMINDER, TASK
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


def _runner(store, clock, run_task=None):
    sent, paused = [], []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    async def default_task(item):
        return f"resultado de {item.text}"

    async def on_paused(item, reason):
        paused.append((item.id, reason))

    due = RunDueItems(store, send, run_task or default_task, on_paused, TZ, clock)
    return due, sent, paused


async def test_on_time_reminder_is_sent_and_done(store):
    rid = await store.add("u1", "c1", REMINDER, "llamar", T0, None)
    due, sent, _ = _runner(store, Clock(T0 + timedelta(seconds=20)))
    await due()
    assert sent == [("c1", "⏰ Recordatorio: llamar")]
    assert (await store.get(rid)).status == DONE
    await due()
    assert len(sent) == 1  # never twice


async def test_late_and_very_late_reminders(store):
    await store.add("u1", "c1", REMINDER, "a", T0, None)
    await store.add("u1", "c1", REMINDER, "b", T0 - timedelta(hours=3), None)
    due, sent, _ = _runner(store, Clock(T0 + timedelta(minutes=10)))
    await due()
    texts = sorted(t for _, t in sent)
    assert texts == ["⏰ Recordatorio: a (con retraso)",
                     "⚠️ Mientras estuve apagada no pude recordarte: b"]


async def test_recurring_reminder_is_rescheduled_in_local_time(store):
    rid = await store.add("u1", "c1", REMINDER, "pastilla", T0, "0 8 * * *")
    due, sent, _ = _runner(store, Clock(T0))
    await due()
    item = await store.get(rid)
    assert item.status == ACTIVE
    assert item.next_run_at == datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)  # 08:00 local


async def test_task_runs_and_missed_occurrences_run_once(store):
    tid = await store.add("u1", "c1", TASK, "resumen", T0 - timedelta(days=15), "0 8 * * 1")
    due, sent, _ = _runner(store, Clock(T0))
    await due()
    await due.drain()
    assert sent == [("c1", f"🔁 Tarea #{tid}: resultado de resumen")]
    assert (await store.get(tid)).next_run_at > T0


async def test_task_failures_retry_then_pause(store):
    tid = await store.add("u1", "c1", TASK, "x", T0, None)

    async def boom(item):
        raise RuntimeError("claude caído")

    clock = Clock(T0)
    due, sent, paused = _runner(store, clock, run_task=boom)
    for _ in range(3):
        await due()
        await due.drain()
        clock.now += timedelta(minutes=6)
    assert (await store.get(tid)).status == PAUSED
    assert paused == [(tid, "claude caído")]
    assert sent == []


async def test_failure_after_item_cancelled_meanwhile_stays_cancelled(store):
    """The user's access could be revoked (cancel_user) while their task is
    RUNNING. record_failure then no-ops (returns 0): the task must end up
    CANCELLED, and on_paused (which would notify/pause) must never fire."""
    tid = await store.add("u1", "c1", TASK, "x", T0, None)

    async def boom_and_cancel(item):
        await store.cancel_user(item.user_id)
        raise RuntimeError("claude caído")

    due, sent, paused = _runner(store, Clock(T0), run_task=boom_and_cancel)
    await due()
    await due.drain()
    assert (await store.get(tid)).status == CANCELLED
    assert paused == []
    assert sent == []


async def test_slow_task_does_not_delay_reminders(store):
    release = asyncio.Event()

    async def slow(item):
        await release.wait()
        return "tarde"

    await store.add("u1", "c1", TASK, "lenta", T0, None)
    await store.add("u2", "c2", REMINDER, "rápido", T0, None)
    due, sent, _ = _runner(store, Clock(T0), run_task=slow)
    await asyncio.wait_for(due(), timeout=1)
    assert ("c2", "⏰ Recordatorio: rápido") in sent
    release.set()
    await due.drain()
    assert any(t.startswith("🔁 Tarea") for _, t in sent)


async def test_store_failure_on_one_item_does_not_stop_others(store):
    rid1 = await store.add("u1", "c1", REMINDER, "primer", T0, None)
    rid2 = await store.add("u1", "c1", REMINDER, "segundo", T0, None)

    original_finish = store.finish
    async def finish_with_failure(item_id, now):
        if item_id == rid1:
            raise RuntimeError("storage corrupted")
        return await original_finish(item_id, now)

    store.finish = finish_with_failure
    due, sent, _ = _runner(store, Clock(T0 + timedelta(seconds=20)))
    await due()

    # Both reminders should have been sent despite first one's store failure
    assert len(sent) == 2
    assert ("c1", "⏰ Recordatorio: primer") in sent
    assert ("c1", "⏰ Recordatorio: segundo") in sent

    # Second item should be DONE
    assert (await store.get(rid2)).status == DONE
    # First item should still be RUNNING (finish failed)
    assert (await store.get(rid1)).status != DONE
