import asyncio
from datetime import datetime, timezone

import pytest

from ari.domain.agent.message import Message
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


async def test_concurrent_writes_no_exception_and_isolation(adapter):
    """Concurrent writes for two users must not raise and must be user-isolated."""
    ts = datetime.now(timezone.utc)

    async def write_messages(user_id: str, n: int) -> None:
        for i in range(n):
            await adapter.append_message(
                Message(user_id, "user", f"msg {i} from {user_id}", ts)
            )

    # Run concurrent writes for two users simultaneously.
    await asyncio.gather(
        write_messages("alice", 5),
        write_messages("bob", 5),
    )

    alice_msgs = await adapter.recent_messages("alice", 10)
    bob_msgs = await adapter.recent_messages("bob", 10)

    # Each user reads back exactly their own messages — isolation holds.
    assert all(m.user_id == "alice" for m in alice_msgs)
    assert all(m.user_id == "bob" for m in bob_msgs)
    assert len(alice_msgs) == 5
    assert len(bob_msgs) == 5


async def test_delete_fact(adapter):
    await adapter.upsert_fact("u1", "color", "azul")
    assert await adapter.delete_fact("u1", "color") is True
    assert await adapter.delete_fact("u1", "color") is False
    assert await adapter.get_facts("u1") == []


async def test_retrieve_recalls_exposes_created_at_and_similarity_score(adapter):
    # Recall precision (Frente 1): retrieval must surface recency + a [0,1]
    # similarity so the ranker can weight by more than raw cosine order.
    await adapter.store_recall("u1", "hola mundo", [1.0, 0.0, 0.0, 0.0], {})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=5)
    assert results
    top = results[0]
    assert top.created_at is not None
    assert top.score is not None
    assert 0.0 < top.score <= 1.0


# ---------------------------------------------------------------------------
# T2 — get_fact, add_fact_history, get_fact_history
# ---------------------------------------------------------------------------


async def test_get_fact_returns_stored_fact(adapter):
    await adapter.upsert_fact("u1", "city", "Madrid")
    fact = await adapter.get_fact("u1", "city")
    assert fact is not None
    assert fact.user_id == "u1"
    assert fact.key == "city"
    assert fact.value == "Madrid"


async def test_get_fact_returns_none_for_missing_key(adapter):
    result = await adapter.get_fact("u1", "nonexistent")
    assert result is None


async def test_get_fact_is_user_scoped(adapter):
    await adapter.upsert_fact("u1", "city", "Madrid")
    assert await adapter.get_fact("u2", "city") is None


async def test_fact_history_roundtrip(adapter):
    await adapter.add_fact_history("u1", "city", "Madrid", "Barcelona", "supersede")
    history = await adapter.get_fact_history("u1", "city")
    assert len(history) == 1
    entry = history[0]
    assert entry["old_value"] == "Madrid"
    assert entry["new_value"] == "Barcelona"
    assert entry["resolution"] == "supersede"
    assert entry["created_at"]


async def test_fact_history_new_entry_has_null_old_value(adapter):
    await adapter.add_fact_history("u1", "city", None, "Madrid", "new")
    history = await adapter.get_fact_history("u1", "city")
    assert history[0]["old_value"] is None


async def test_fact_history_ordered_by_id(adapter):
    await adapter.add_fact_history("u1", "city", None, "Madrid", "new")
    await adapter.add_fact_history("u1", "city", "Madrid", "Barcelona", "supersede")
    history = await adapter.get_fact_history("u1", "city")
    assert [h["new_value"] for h in history] == ["Madrid", "Barcelona"]


async def test_fact_history_is_user_scoped(adapter):
    await adapter.add_fact_history("u1", "city", None, "Madrid", "new")
    await adapter.add_fact_history("u2", "city", None, "Lima", "new")
    u1_history = await adapter.get_fact_history("u1", "city")
    assert len(u1_history) == 1
    assert u1_history[0]["new_value"] == "Madrid"
