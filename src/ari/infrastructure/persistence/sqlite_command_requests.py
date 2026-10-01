import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.command.requests import FAILED, PENDING, TAKEN, CommandRequest

_COLS = "id, user_id, chat_id, command, created_at"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _request(r) -> CommandRequest:
    return CommandRequest(r["id"], r["user_id"], r["chat_id"], r["command"],
                          datetime.fromisoformat(r["created_at"]))


class SqliteCommandRequests:
    """Queue between Ari's MCP server (writer) and the bot (claimer) for shell
    commands proposed via proponer_comando. Mirrors SqliteCodingRequests."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str, command: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO command_requests (user_id, chat_id, command, status, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, chat_id, command, PENDING, _iso(datetime.now(timezone.utc))))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[CommandRequest]:
        # One guarded UPDATE … RETURNING: two claimers (even in two processes)
        # can never both take the same request.
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE command_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (TAKEN, PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def reset_taken(self) -> list[CommandRequest]:
        """Mark every stranded `taken` row (e.g. the process crashed/restarted
        before «dale») as `failed` and return them, so the caller can notify
        their chats. Single guarded UPDATE … RETURNING, same as claim_pending."""
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE command_requests SET status = ?, detail = ? WHERE status = ? "
                f"RETURNING {_COLS}",
                (FAILED, "interrumpido", TAKEN))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def finish(self, request_id: int, status: str, detail: str | None = None) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE command_requests SET status = ?, detail = ? WHERE id = ?",
                (status, detail, request_id))
            await self._conn.commit()

    async def status_of(self, request_id: int) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT status FROM command_requests WHERE id = ?", (request_id,))
        return rows[0]["status"] if rows else None
