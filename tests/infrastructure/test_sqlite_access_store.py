import pytest

from ari.domain.access.entities import APPROVED, PENDING
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.persistence.db import connect


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteAccessStore(conn)
    await conn.close()


async def test_unknown_user_has_no_record(store):
    assert await store.get("u1") is None


async def test_pending_roundtrip_and_lookup_by_code(store):
    await store.create_pending("u1", "juan", "ABCD2345")
    rec = await store.get("u1")
    assert (rec.user_id, rec.username, rec.code, rec.status) == (
        "u1", "juan", "ABCD2345", PENDING)
    assert (await store.find_by_code("ABCD2345")).user_id == "u1"
    assert await store.find_by_code("ZZZZ9999") is None


async def test_approve_and_delete(store):
    await store.create_pending("u1", None, "ABCD2345")
    await store.set_status("u1", APPROVED)
    assert (await store.get("u1")).status == APPROVED
    await store.delete("u1")
    assert await store.get("u1") is None


async def test_list_all(store):
    await store.create_pending("u1", "a", "AAAA2222")
    await store.create_pending("u2", "b", "BBBB3333")
    assert {r.user_id for r in await store.list_all()} == {"u1", "u2"}


async def test_pending_since_lists_only_pending_with_creation_time(store):
    await store.create_pending("u1", "a", "AAAA2222")
    await store.create_pending("u2", "b", "BBBB3333")
    await store.set_status("u2", APPROVED)
    rows = await store.pending_since()
    assert [(r.user_id, type(ts).__name__) for r, ts in rows] == [("u1", "datetime")]
    assert rows[0][1].tzinfo is not None
