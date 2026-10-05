from datetime import datetime, timedelta, timezone

from ari.application.files.request_runner import FileServeRequestRunner
from ari.domain.files.requests import DONE, FAILED, SKIPPED
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests


class _Web:
    def __init__(self, link=None, exc=None): self._link, self._exc = link, exc
    def new_file_link(self, path):
        if self._exc:
            raise self._exc
        return self._link


async def _run(q, web, sent, clock=None):
    async def send(chat_id, text): sent.append((chat_id, text))
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    await FileServeRequestRunner(q, web, send, clock)()


async def test_pending_request_gets_link():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn); sent = []
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await _run(q, _Web(link="https://1.2.3.4:8765/f/tok"), sent)
        assert sent and "https://1.2.3.4:8765/f/tok" in sent[0][1]
        assert await q.status_of(rid) == DONE
    finally:
        await conn.close()


async def test_mint_failure_reported():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn); sent = []
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await _run(q, _Web(exc=PermissionError("denied path: /tmp/a.pdf")), sent)
        assert await q.status_of(rid) == FAILED
        assert sent and "No pude" in sent[0][1]
    finally:
        await conn.close()


async def test_stale_request_skipped_without_link():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn); sent = []
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await _run(q, _Web(link="x"), sent, clock=lambda: datetime.now(timezone.utc) + timedelta(hours=2))
        assert await q.status_of(rid) == SKIPPED and sent == []
    finally:
        await conn.close()
