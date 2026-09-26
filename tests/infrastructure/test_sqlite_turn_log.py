from datetime import datetime, timedelta, timezone

import pytest

from ari.infrastructure.persistence.db import connect, open_existing
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog

T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def log(tmp_path):
    db = str(tmp_path / "ari.db")
    first = await connect(db, embedding_dim=4)  # creates the full schema like main does
    await first.close()
    conn = await open_existing(db)
    yield SqliteTurnLog(conn)
    await conn.close()


async def test_receipts_per_turn_in_order(log):
    await log.add_receipt("t1", "✅ uno")
    await log.add_receipt("t2", "otro turno")
    await log.add_receipt("t1", "🗑️ dos")
    assert await log.receipts("t1") == ["✅ uno", "🗑️ dos"]
    assert await log.receipts("nope") == []


async def test_purge_receipts(log):
    await log.add_receipt("t1", "x")
    assert await log.purge_receipts(datetime.now(timezone.utc) + timedelta(seconds=5)) == 1
    assert await log.receipts("t1") == []


async def test_outbox_lifecycle(log):
    a = await log.outbox_add("7", "hola")
    b = await log.outbox_add("8", "chao")
    assert [p[:3] for p in await log.outbox_pending()] == [(a, "7", "hola"), (b, "8", "chao")]
    await log.outbox_mark_sent(a, T0)
    assert await log.outbox_mark_failed(b) == 1
    assert await log.outbox_mark_failed(b) == 2
    assert [p[0] for p in await log.outbox_pending()] == [b]
    await log.outbox_drop(b)
    assert await log.outbox_pending() == []


async def test_open_existing_does_not_need_sqlite_vec(tmp_path):
    conn = await open_existing(str(tmp_path / "fresh.db"))  # plain tables only
    rows = await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    await conn.close()
    assert {"schedules", "facts", "access", "receipts", "outbox"} <= names
