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


# ---------------------------------------------------------------------------
# T1 (Frente 3) — bump_recall_importance, decay_recalls, prune_recalls
# ---------------------------------------------------------------------------


async def test_bump_recall_importance_raises_value(adapter):
    """bump_recall_importance must increase importance by delta and read back."""
    await adapter.store_recall("u1", "content A", [1.0, 0.0, 0.0, 0.0],
                               {"type": "exchange", "importance": 0.5})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=1)
    rid = results[0].id
    await adapter.bump_recall_importance(rid, 0.2)
    rows = await adapter._conn.execute_fetchall(
        "SELECT json_extract(metadata_json, '$.importance') AS imp FROM recalls WHERE id = ?",
        (rid,),
    )
    assert rows[0]["imp"] == pytest.approx(0.7)


async def test_bump_recall_importance_caps_at_1(adapter):
    """Bumping past 1.0 must clamp to 1.0."""
    await adapter.store_recall("u1", "content B", [1.0, 0.0, 0.0, 0.0],
                               {"type": "exchange", "importance": 0.9})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=1)
    rid = results[0].id
    await adapter.bump_recall_importance(rid, 0.5)  # 0.9 + 0.5 = 1.4 → should cap at 1.0
    rows = await adapter._conn.execute_fetchall(
        "SELECT json_extract(metadata_json, '$.importance') AS imp FROM recalls WHERE id = ?",
        (rid,),
    )
    assert rows[0]["imp"] == pytest.approx(1.0)


async def test_decay_recalls_multiplies_unpinned_importance(adapter):
    """decay_recalls must multiply unpinned recall importance by factor."""
    await adapter.store_recall("u1", "unpinned", [1.0, 0.0, 0.0, 0.0],
                               {"type": "exchange", "importance": 0.8})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=1)
    rid = results[0].id
    await adapter.decay_recalls(0.5)
    rows = await adapter._conn.execute_fetchall(
        "SELECT json_extract(metadata_json, '$.importance') AS imp FROM recalls WHERE id = ?",
        (rid,),
    )
    assert rows[0]["imp"] == pytest.approx(0.4)


async def test_decay_recalls_leaves_pinned_unchanged(adapter):
    """decay_recalls must NOT touch recalls that have pinned=true in metadata."""
    await adapter.store_recall("u1", "pinned recall", [1.0, 0.0, 0.0, 0.0],
                               {"type": "exchange", "importance": 0.8, "pinned": True})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=1)
    rid = results[0].id
    await adapter.decay_recalls(0.5)
    rows = await adapter._conn.execute_fetchall(
        "SELECT json_extract(metadata_json, '$.importance') AS imp FROM recalls WHERE id = ?",
        (rid,),
    )
    assert rows[0]["imp"] == pytest.approx(0.8)  # unchanged


async def test_prune_recalls_removes_low_importance_old_unpinned(adapter):
    """prune_recalls must delete rows that are low-importance, old, and not pinned."""
    import json as _json
    old_iso = "2020-01-01T00:00:00+00:00"
    # Insert directly with an old created_at
    cur = await adapter._conn.execute(
        "INSERT INTO recalls (user_id, content, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        ("u1", "stale recall", _json.dumps({"importance": 0.1}), old_iso),
    )
    stale_id = cur.lastrowid
    await adapter._conn.execute(
        "INSERT INTO recalls_vec (id, user_id, embedding) VALUES (?, ?, ?)",
        (stale_id, "u1", bytes(16)),  # 4 floats × 4 bytes = 16 zero bytes
    )
    await adapter._conn.commit()

    cutoff = "2025-01-01T00:00:00+00:00"
    count = await adapter.prune_recalls(0.2, cutoff)
    assert count == 1

    # Stale recall gone from both tables
    recalls_rows = await adapter._conn.execute_fetchall(
        "SELECT id FROM recalls WHERE id = ?", (stale_id,)
    )
    assert recalls_rows == []
    vec_rows = await adapter._conn.execute_fetchall(
        "SELECT id FROM recalls_vec WHERE id = ?", (stale_id,)
    )
    assert vec_rows == []


async def test_prune_recalls_keeps_pinned(adapter):
    """prune_recalls must keep pinned recalls even if old and low-importance."""
    import json as _json
    old_iso = "2020-01-01T00:00:00+00:00"
    cur = await adapter._conn.execute(
        "INSERT INTO recalls (user_id, content, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        ("u1", "pinned stale", _json.dumps({"importance": 0.05, "pinned": True}), old_iso),
    )
    pinned_id = cur.lastrowid
    await adapter._conn.execute(
        "INSERT INTO recalls_vec (id, user_id, embedding) VALUES (?, ?, ?)",
        (pinned_id, "u1", bytes(16)),
    )
    await adapter._conn.commit()

    cutoff = "2025-01-01T00:00:00+00:00"
    count = await adapter.prune_recalls(0.2, cutoff)
    assert count == 0

    rows = await adapter._conn.execute_fetchall(
        "SELECT id FROM recalls WHERE id = ?", (pinned_id,)
    )
    assert len(rows) == 1


async def test_prune_recalls_keeps_recent(adapter):
    """prune_recalls must keep low-importance recalls that are NOT old enough."""
    await adapter.store_recall("u1", "recent but faded", [1.0, 0.0, 0.0, 0.0],
                               {"importance": 0.05})
    # cutoff is in the past relative to now, so the just-stored recall is newer
    cutoff = "2020-01-01T00:00:00+00:00"
    count = await adapter.prune_recalls(0.2, cutoff)
    assert count == 0


async def test_prune_recalls_keeps_high_importance(adapter):
    """prune_recalls must keep old recalls whose importance is above floor."""
    import json as _json
    old_iso = "2020-01-01T00:00:00+00:00"
    cur = await adapter._conn.execute(
        "INSERT INTO recalls (user_id, content, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        ("u1", "important old", _json.dumps({"importance": 0.9}), old_iso),
    )
    imp_id = cur.lastrowid
    await adapter._conn.execute(
        "INSERT INTO recalls_vec (id, user_id, embedding) VALUES (?, ?, ?)",
        (imp_id, "u1", bytes(16)),
    )
    await adapter._conn.commit()

    cutoff = "2025-01-01T00:00:00+00:00"
    count = await adapter.prune_recalls(0.2, cutoff)
    assert count == 0
