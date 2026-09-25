from datetime import datetime, timedelta, timezone

import pytest

from ari.domain.schedule.entities import ACTIVE, CANCELLED, DONE, PAUSED, REMINDER, RUNNING, TASK
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


async def test_add_get_roundtrip(store):
    item_id = await store.add("u1", "c1", REMINDER, "llamar", T0, None)
    item = await store.get(item_id)
    assert (item.user_id, item.chat_id, item.kind, item.text, item.next_run_at,
            item.cron, item.status, item.failures) == (
        "u1", "c1", REMINDER, "llamar", T0, None, ACTIVE, 0)
    assert await store.get(999) is None


async def test_claim_due_only_returns_due_active_once(store):
    due = await store.add("u1", "c1", REMINDER, "a", T0, None)
    await store.add("u1", "c1", REMINDER, "later", T0 + timedelta(hours=1), None)
    claimed = await store.claim_due(T0)
    assert [i.id for i in claimed] == [due] and claimed[0].status == RUNNING
    assert await store.claim_due(T0) == []  # already running: never twice
    assert await store.reset_running() == 1
    assert [i.id for i in await store.claim_due(T0)] == [due]


async def test_reschedule_finish_and_failures(store):
    a = await store.add("u1", "c1", TASK, "t", T0, "0 8 * * 1")
    await store.claim_due(T0)
    assert await store.record_failure(a, T0 + timedelta(minutes=5)) == 1
    assert await store.record_failure(a, T0 + timedelta(minutes=10)) == 2
    item = await store.get(a)
    assert item.status == ACTIVE and item.next_run_at == T0 + timedelta(minutes=10)
    await store.reschedule(a, T0 + timedelta(days=3), T0)
    item = await store.get(a)
    assert item.failures == 0 and item.next_run_at == T0 + timedelta(days=3)
    await store.finish(a, T0)
    assert (await store.get(a)).status == DONE


async def test_user_listing_count_cancel_and_upcoming(store):
    a = await store.add("u1", "c1", REMINDER, "a", T0 + timedelta(hours=2), None)
    b = await store.add("u1", "c1", REMINDER, "b", T0 + timedelta(hours=30), None)
    await store.add("u2", "c2", REMINDER, "other", T0, None)
    await store.set_status(b, PAUSED)
    assert [i.id for i in await store.list_for_user("u1")] == [a, b]
    assert await store.count_active("u1") == 2
    assert [i.id for i in await store.upcoming("u1", T0 + timedelta(hours=24))] == [a]
    assert await store.cancel_user("u1") == 2
    assert (await store.get(a)).status == CANCELLED
    assert await store.count_active("u1") == 0


async def test_kv(store):
    assert await store.kv_get("k") is None
    await store.kv_set("k", "1")
    await store.kv_set("k", "2")
    assert await store.kv_get("k") == "2"
    await store.kv_delete("k")
    assert await store.kv_get("k") is None
