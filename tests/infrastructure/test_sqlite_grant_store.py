import pytest

from ari.domain.grants.entities import ACT, READ
from ari.infrastructure.grants.sqlite_grant_store import SqliteGrantStore
from ari.infrastructure.persistence.db import connect


@pytest.fixture
async def store(tmp_path):
    conn = await connect(str(tmp_path / "ari.db"))
    try:
        yield SqliteGrantStore(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upsert_and_get(store):
    await store.upsert("A", "B", "schedule", READ)
    g = await store.get("A", "B", "schedule")
    assert g is not None and g.level == READ
    assert await store.get("A", "C", "schedule") is None


@pytest.mark.asyncio
async def test_regrant_updates_level(store):
    await store.upsert("A", "B", "schedule", READ)
    await store.upsert("A", "B", "schedule", ACT)
    g = await store.get("A", "B", "schedule")
    assert g.level == ACT
    assert len(await store.given_by("A")) == 1  # upsert, not duplicate


@pytest.mark.asyncio
async def test_given_by_and_received_by(store):
    await store.upsert("A", "B", "schedule", ACT)
    await store.upsert("C", "B", "schedule", READ)
    assert {g.grantor_id for g in await store.received_by("B")} == {"A", "C"}
    assert [g.grantee_id for g in await store.given_by("A")] == ["B"]


@pytest.mark.asyncio
async def test_revoke(store):
    await store.upsert("A", "B", "schedule", ACT)
    assert await store.revoke("A", "B", "schedule") is True
    assert await store.get("A", "B", "schedule") is None
    assert await store.revoke("A", "B", "schedule") is False


@pytest.mark.asyncio
async def test_delete_for_user_both_directions(store):
    await store.upsert("A", "B", "schedule", ACT)   # B is grantee
    await store.upsert("B", "C", "schedule", READ)  # B is grantor
    await store.upsert("A", "C", "schedule", READ)  # unrelated to B
    assert await store.delete_for_user("B") == 2
    assert await store.get("A", "C", "schedule") is not None
