import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.access.entities import PENDING, AccessRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(r) -> AccessRecord:
    return AccessRecord(r["user_id"], r["username"], r["code"], r["status"])


class SqliteAccessStore:
    """AccessPort backed by the ``access`` table (see persistence/db.py)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._write_lock = asyncio.Lock()

    async def get(self, user_id: str) -> AccessRecord | None:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, username, code, status FROM access WHERE user_id = ?",
            (user_id,))
        return _record(rows[0]) if rows else None

    async def find_by_code(self, code: str) -> AccessRecord | None:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, username, code, status FROM access WHERE code = ?", (code,))
        return _record(rows[0]) if rows else None

    async def create_pending(self, user_id: str, username: str | None, code: str) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO access (user_id, username, code, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (user_id, username, code, _now(), _now()))
            await self._conn.commit()

    async def set_status(self, user_id: str, status: str) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE access SET status = ?, updated_at = ? WHERE user_id = ?",
                (status, _now(), user_id))
            await self._conn.commit()

    async def delete(self, user_id: str) -> None:
        async with self._write_lock:
            await self._conn.execute("DELETE FROM access WHERE user_id = ?", (user_id,))
            await self._conn.commit()

    async def list_all(self) -> list[AccessRecord]:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, username, code, status FROM access ORDER BY created_at")
        return [_record(r) for r in rows]

    async def pending_since(self) -> list[tuple[AccessRecord, datetime]]:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, username, code, status, created_at FROM access "
            "WHERE status = ? ORDER BY created_at", (PENDING,))
        return [(_record(r), datetime.fromisoformat(r["created_at"])) for r in rows]
