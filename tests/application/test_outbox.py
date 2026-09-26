import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ari.application.outbox import OutboxFlusher
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def log():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteTurnLog(conn)
    await conn.close()


async def test_sends_once_even_when_flushed_concurrently(log):
    await log.outbox_add("7", "hola")
    sent = []

    async def send(chat_id, text):
        await asyncio.sleep(0.05)
        sent.append((chat_id, text))
        return True

    flusher = OutboxFlusher(log, send, clock=lambda: NOW)
    await asyncio.gather(flusher(), flusher())
    assert sent == [("7", "hola")]
    assert await log.outbox_pending() == []


async def test_failed_send_retries_then_drops(log):
    await log.outbox_add("7", "hola")

    async def send(chat_id, text):
        return False

    flusher = OutboxFlusher(log, send, clock=lambda: NOW, max_attempts=3)
    await flusher()
    await flusher()
    assert len(await log.outbox_pending()) == 1
    await flusher()
    assert await log.outbox_pending() == []


class _CountingPurgeLog:
    """No outbox items to send: only purge_receipts is exercised, and counted."""

    def __init__(self):
        self.purges: list = []

    async def outbox_pending(self):
        return []

    async def purge_receipts(self, before):
        self.purges.append(before)
        return 0


async def test_purges_receipts_at_most_once_per_hour():
    async def send(chat_id, text):
        return True

    fake_log = _CountingPurgeLog()
    now = {"t": NOW}
    flusher = OutboxFlusher(fake_log, send, clock=lambda: now["t"])

    await flusher()
    assert len(fake_log.purges) == 1  # first flush always purges

    now["t"] += timedelta(minutes=30)
    await flusher()
    assert len(fake_log.purges) == 1  # within the hour: no second purge

    now["t"] += timedelta(minutes=31)
    await flusher()
    assert len(fake_log.purges) == 2  # an hour elapsed since the last purge
