import pytest

from ari.infrastructure.persistence.db import connect
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter


@pytest.fixture
async def adapter():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteMemoryAdapter(conn, embedding_dim=4)
    await conn.close()


async def test_facts_and_summary_roundtrip(adapter):
    await adapter.upsert_fact("u1", "name", "Gabo")
    await adapter.upsert_fact("u1", "name", "Gabriel")  # upsert overwrites
    facts = await adapter.get_facts("u1")
    assert [(f.key, f.value) for f in facts] == [("name", "Gabriel")]
    await adapter.upsert_summary("u1", "hello")
    assert (await adapter.get_summary("u1")).content == "hello"


async def test_recall_search_is_user_scoped(adapter):
    await adapter.store_recall("u1", "user one secret", [1.0, 0.0, 0.0, 0.0], {})
    await adapter.store_recall("u2", "user two secret", [1.0, 0.0, 0.0, 0.0], {})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=5)
    assert all(r.user_id == "u1" for r in results)
    assert any("user one" in r.content for r in results)
    assert not any("user two" in r.content for r in results)


async def test_wrong_dimension_embedding_raises(adapter):
    with pytest.raises(ValueError):
        await adapter.store_recall("u1", "x", [1.0, 2.0], {})  # 2 dims, expected 4


async def test_wrong_dimension_query_embedding_raises(adapter):
    await adapter.store_recall("u1", "x", [1.0, 0.0, 0.0, 0.0], {})
    with pytest.raises(ValueError):
        await adapter.retrieve_recalls("u1", [1.0, 2.0], k=5)  # 2 dims, expected 4
