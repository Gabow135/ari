import asyncio

import pytest

from ari.domain.credentials.requests import DONE, FAILED, PENDING, TAKEN
from ari.infrastructure.persistence.db import connect, open_existing
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCredentialRequests(conn)
    await conn.close()


async def test_add_and_claim_once(store):
    a = await store.add("42", "42", "el api de groq")
    b = await store.add("42", "42", "OPENAI_API_KEY")
    claimed = await store.claim_pending()
    assert [(r.id, r.requested) for r in claimed] == [(a, "el api de groq"), (b, "OPENAI_API_KEY")]
    assert claimed[0].created_at.tzinfo is not None
    assert await store.claim_pending() == []
    assert await store.status_of(a) == TAKEN


async def test_finish(store):
    a = await store.add("42", "42", "x")
    await store.claim_pending()
    await store.finish(a, DONE)
    assert await store.status_of(a) == DONE
    await store.finish(a, FAILED, "boom")
    assert await store.status_of(a) == FAILED
    assert await store.status_of(999) is None


async def test_reset_taken_requeues_stranded_rows(store):
    a = await store.add("42", "42", "groq")
    await store.claim_pending()                 # a -> TAKEN
    reset = await store.reset_taken()
    assert [r.id for r in reset] == [a]
    assert await store.status_of(a) == PENDING  # re-queued, NOT failed
    assert [r.id for r in await store.claim_pending()] == [a]  # claimable again
    second_reset = await store.reset_taken()
    assert [r.id for r in second_reset] == [a]  # should reset again after being claimed


async def test_claim_is_atomic_across_connections(tmp_path):
    db = str(tmp_path / "ari.db")
    first = await connect(db, embedding_dim=4)
    await SqliteCredentialRequests(first).add("42", "42", "x")
    other = await open_existing(db)
    try:
        got = await asyncio.gather(SqliteCredentialRequests(first).claim_pending(),
                                   SqliteCredentialRequests(other).claim_pending())
    finally:
        await other.close()
        await first.close()
    assert sorted(len(g) for g in got) == [0, 1]
