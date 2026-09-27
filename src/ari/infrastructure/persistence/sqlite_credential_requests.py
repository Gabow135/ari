import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.credentials.requests import PENDING, TAKEN, CredentialRequest

_COLS = "id, user_id, chat_id, requested, created_at"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _request(r) -> CredentialRequest:
    return CredentialRequest(r["id"], r["user_id"], r["chat_id"], r["requested"],
                             datetime.fromisoformat(r["created_at"]))


class SqliteCredentialRequests:
    """Queue between Ari's MCP server (writer) and the bot (claimer)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str, requested: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO credential_requests (user_id, chat_id, requested, status, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, chat_id, requested, PENDING, _iso(datetime.now(timezone.utc))))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[CredentialRequest]:
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE credential_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (TAKEN, PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def reset_taken(self) -> list[CredentialRequest]:
        """Re-queue stranded `taken` rows (bot restarted mid-delivery) back to `pending`
        so the runner mints a fresh link. A credential link is cheap to re-mint, so unlike
        the coding queue this re-queues instead of failing."""
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE credential_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (PENDING, TAKEN))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def finish(self, request_id: int, status: str, detail: str | None = None) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE credential_requests SET status = ?, detail = ? WHERE id = ?",
                (status, detail, request_id))
            await self._conn.commit()

    async def status_of(self, request_id: int) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT status FROM credential_requests WHERE id = ?", (request_id,))
        return rows[0]["status"] if rows else None
