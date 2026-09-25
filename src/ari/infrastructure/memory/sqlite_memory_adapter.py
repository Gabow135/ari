import json
import struct
from datetime import datetime, timezone

import aiosqlite

from ari.domain.agent.message import Message
from ari.domain.memory.entities import Fact, Recall, Summary


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteMemoryAdapter:
    def __init__(self, conn: aiosqlite.Connection, embedding_dim: int = 1024):
        self._conn = conn
        self._dim = embedding_dim

    async def recent_messages(self, user_id: str, limit: int) -> list[Message]:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, role, content, created_at FROM messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        msgs = [
            Message(r["user_id"], r["role"], r["content"],
                    datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]
        return list(reversed(msgs))

    async def append_message(self, message: Message) -> None:
        await self._conn.execute(
            "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (message.user_id, message.role, message.content,
             message.created_at.isoformat()),
        )
        await self._conn.commit()

    async def store_recall(
        self, user_id: str, content: str, embedding: list[float], metadata: dict
    ) -> None:
        if len(embedding) != self._dim:
            raise ValueError(
                f"embedding has {len(embedding)} dims, expected {self._dim}"
            )
        cur = await self._conn.execute(
            "INSERT INTO recalls (user_id, content, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, content, json.dumps(metadata), _now()),
        )
        recall_id = cur.lastrowid
        await self._conn.execute(
            "INSERT INTO recalls_vec (id, user_id, embedding) VALUES (?, ?, ?)",
            (recall_id, user_id, _pack(embedding)),
        )
        await self._conn.commit()

    async def retrieve_recalls(
        self, user_id: str, query_embedding: list[float], k: int
    ) -> list[Recall]:
        if len(query_embedding) != self._dim:
            raise ValueError(
                f"embedding has {len(query_embedding)} dims, expected {self._dim}"
            )
        # recalls_vec has a `user_id TEXT partition` column so the WHERE clause
        # restricts KNN search to that partition — fully user-isolated at the
        # vector-index level, with results ordered by similarity (distance ASC).
        rows = await self._conn.execute_fetchall(
            "SELECT r.id, r.user_id, r.content, r.metadata_json "
            "FROM recalls_vec v "
            "JOIN recalls r ON r.id = v.id "
            "WHERE v.user_id = ? AND v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (user_id, _pack(query_embedding), k),
        )
        return [
            Recall(r["id"], r["user_id"], r["content"], json.loads(r["metadata_json"]))
            for r in rows
        ]

    async def get_facts(self, user_id: str) -> list[Fact]:
        rows = await self._conn.execute_fetchall(
            "SELECT key, value FROM facts WHERE user_id = ? ORDER BY key",
            (user_id,),
        )
        return [Fact(user_id, r["key"], r["value"]) for r in rows]

    async def upsert_fact(self, user_id: str, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO facts (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (user_id, key, value, _now()),
        )
        await self._conn.commit()

    async def get_summary(self, user_id: str) -> Summary | None:
        rows = await self._conn.execute_fetchall(
            "SELECT content FROM summaries WHERE user_id = ?", (user_id,)
        )
        return Summary(user_id, rows[0]["content"]) if rows else None

    async def upsert_summary(self, user_id: str, content: str) -> None:
        await self._conn.execute(
            "INSERT INTO summaries (user_id, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET content=excluded.content, "
            "updated_at=excluded.updated_at",
            (user_id, content, _now()),
        )
        await self._conn.commit()
