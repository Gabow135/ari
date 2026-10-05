from ari.domain.files.requests import DONE, PENDING, TAKEN
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests


async def test_add_claim_finish_roundtrip():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn)
    try:
        rid = await q.add("42", "42", "/tmp/factura.pdf")
        assert await q.status_of(rid) == PENDING
        [req] = await q.claim_pending()
        assert (req.user_id, req.chat_id, req.path) == ("42", "42", "/tmp/factura.pdf")
        assert await q.status_of(rid) == TAKEN
        assert await q.claim_pending() == []  # nothing left pending
        await q.finish(rid, DONE)
        assert await q.status_of(rid) == DONE
    finally:
        await conn.close()


async def test_reset_taken_requeues():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn)
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await q.claim_pending()
        [req] = await q.reset_taken()
        assert req.id == rid
        assert await q.status_of(rid) == PENDING
    finally:
        await conn.close()
