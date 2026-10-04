import pytest

from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_email_enroll_requests import (
    SqliteEmailEnrollRequests,
)


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteEmailEnrollRequests(conn)
    await conn.close()


async def test_add_claim_once_and_finish(store):
    rid = await store.add("7", "7")
    claimed = await store.claim_pending()
    assert [(r.id, r.user_id, r.chat_id) for r in claimed] == [(rid, "7", "7")]
    assert await store.claim_pending() == []
    await store.finish(rid)
    assert await store.claim_pending() == []
