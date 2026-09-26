import asyncio
from datetime import datetime, timezone

import pytest

from ari.domain.coding.requests import DONE, FAILED, TAKEN
from ari.infrastructure.persistence.db import connect, open_existing
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCodingRequests(conn)
    await conn.close()


async def test_add_and_claim_once(store):
    a = await store.add("42", "42", "agrega /ping", None)
    b = await store.add("42", "42", "otra cosa", "proyectos/x")
    claimed = await store.claim_pending()
    assert [(r.id, r.instruction, r.target) for r in claimed] == [
        (a, "agrega /ping", None), (b, "otra cosa", "proyectos/x")]
    assert claimed[0].created_at.tzinfo is not None
    assert await store.claim_pending() == []
    assert await store.status_of(a) == TAKEN


async def test_finish(store):
    a = await store.add("42", "42", "x", None)
    await store.claim_pending()
    await store.finish(a, DONE)
    assert await store.status_of(a) == DONE
    await store.finish(a, FAILED, "boom")
    assert await store.status_of(a) == FAILED
    assert await store.status_of(999) is None


async def test_claim_is_atomic_across_connections(tmp_path):
    db = str(tmp_path / "ari.db")
    first = await connect(db, embedding_dim=4)
    await SqliteCodingRequests(first).add("42", "42", "x", None)
    other = await open_existing(db)
    try:
        got = await asyncio.gather(SqliteCodingRequests(first).claim_pending(),
                                   SqliteCodingRequests(other).claim_pending())
    finally:
        await other.close()
        await first.close()
    assert sorted(len(g) for g in got) == [0, 1]
