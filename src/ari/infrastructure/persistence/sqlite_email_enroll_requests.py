import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

_PENDING, _TAKEN = "pending", "taken"


@dataclass(frozen=True, slots=True)
class EnrollRequest:
    id: int
    user_id: str
    chat_id: str


class SqliteEmailEnrollRequests:
    """Queue between the Ari MCP server (writer) and the bot (claimer/sender)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO email_enroll_requests (user_id, chat_id, status, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, chat_id, _PENDING,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[EnrollRequest]:
        async with self._lock:
            cur = await self._conn.execute(
                "UPDATE email_enroll_requests SET status = ? WHERE status = ? "
                "RETURNING id, user_id, chat_id", (_TAKEN, _PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((EnrollRequest(r["id"], r["user_id"], r["chat_id"]) for r in rows),
                      key=lambda r: r.id)

    async def finish(self, request_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM email_enroll_requests WHERE id = ?", (request_id,))
            await self._conn.commit()
