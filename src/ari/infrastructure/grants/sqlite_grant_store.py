import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.grants.entities import Grant

_COLS = "grantor_id, grantee_id, capability, level"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _grant(r) -> Grant:
    return Grant(r["grantor_id"], r["grantee_id"], r["capability"], r["level"])


class SqliteGrantStore:
    """GrantPort backed by the ``grants`` table (see persistence/db.py)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._write_lock = asyncio.Lock()

    async def upsert(self, grantor_id: str, grantee_id: str, capability: str, level: str) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO grants (grantor_id, grantee_id, capability, level, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(grantor_id, grantee_id, capability) DO UPDATE SET level = excluded.level",
                (grantor_id, grantee_id, capability, level, _now()))
            await self._conn.commit()

    async def get(self, grantor_id: str, grantee_id: str, capability: str) -> Grant | None:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM grants "
            "WHERE grantor_id = ? AND grantee_id = ? AND capability = ?",
            (grantor_id, grantee_id, capability))
        return _grant(rows[0]) if rows else None

    async def given_by(self, grantor_id: str) -> list[Grant]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM grants WHERE grantor_id = ? ORDER BY created_at", (grantor_id,))
        return [_grant(r) for r in rows]

    async def received_by(self, grantee_id: str) -> list[Grant]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM grants WHERE grantee_id = ? ORDER BY created_at", (grantee_id,))
        return [_grant(r) for r in rows]

    async def revoke(self, grantor_id: str, grantee_id: str, capability: str) -> bool:
        async with self._write_lock:
            cur = await self._conn.execute(
                "DELETE FROM grants WHERE grantor_id = ? AND grantee_id = ? AND capability = ?",
                (grantor_id, grantee_id, capability))
            await self._conn.commit()
            return cur.rowcount > 0

    async def delete_for_user(self, user_id: str) -> int:
        async with self._write_lock:
            cur = await self._conn.execute(
                "DELETE FROM grants WHERE grantor_id = ? OR grantee_id = ?", (user_id, user_id))
            await self._conn.commit()
            return cur.rowcount
