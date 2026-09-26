import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.coding.requests import PENDING, TAKEN, CodingRequest

_COLS = "id, user_id, chat_id, instruction, target, created_at"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _request(r) -> CodingRequest:
    return CodingRequest(r["id"], r["user_id"], r["chat_id"], r["instruction"], r["target"],
                         datetime.fromisoformat(r["created_at"]))


class SqliteCodingRequests:
    """Queue between Ari's MCP server (writer) and the bot (claimer)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str, instruction: str,
                  target: str | None) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO coding_requests (user_id, chat_id, instruction, target, status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, chat_id, instruction, target, PENDING,
                 _iso(datetime.now(timezone.utc))))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[CodingRequest]:
        # One guarded UPDATE … RETURNING: two claimers (even in two processes)
        # can never both take the same request.
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE coding_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (TAKEN, PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def finish(self, request_id: int, status: str, detail: str | None = None) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE coding_requests SET status = ?, detail = ? WHERE id = ?",
                (status, detail, request_id))
            await self._conn.commit()

    async def status_of(self, request_id: int) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT status FROM coding_requests WHERE id = ?", (request_id,))
        return rows[0]["status"] if rows else None
