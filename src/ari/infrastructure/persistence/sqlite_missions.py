import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.missions.entities import (
    CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING, Mission,
)

_COLS = "id, user_id, chat_id, instruction, status, failures, result, created_at, updated_at"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _mission(r) -> Mission:
    return Mission(
        id=r["id"],
        user_id=r["user_id"],
        chat_id=r["chat_id"],
        instruction=r["instruction"],
        status=r["status"],
        failures=r["failures"],
        result=r["result"],
        created_at=datetime.fromisoformat(r["created_at"]),
        updated_at=datetime.fromisoformat(r["updated_at"]),
    )


class SqliteMissions:
    """Persistence for background missions: create, claim, finish, query."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str, instruction: str) -> int:
        now = _now_iso()
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO missions (user_id, chat_id, instruction, status, failures, "
                "result, created_at, updated_at) VALUES (?, ?, ?, ?, 0, NULL, ?, ?)",
                (user_id, chat_id, instruction, PENDING, now, now))
            await self._conn.commit()
            return cur.lastrowid

    async def get(self, mission_id: int) -> Mission | None:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM missions WHERE id = ?", (mission_id,))
        return _mission(rows[0]) if rows else None

    async def list_for_user(self, user_id: str) -> list[Mission]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM missions WHERE user_id = ? "
            "AND status NOT IN (?, ?) ORDER BY id DESC LIMIT 20",
            (user_id, DONE, CANCELLED))
        return [_mission(r) for r in rows]

    async def claim_pending(self) -> list[Mission]:
        """Atomically mark all PENDING missions as RUNNING and return them."""
        now = _now_iso()
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE missions SET status = ?, updated_at = ? "
                f"WHERE status = ? RETURNING {_COLS}",
                (RUNNING, now, PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_mission(r) for r in rows), key=lambda m: m.id)

    async def finish(self, mission_id: int, status: str,
                     result: str | None = None) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE missions SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, result, _now_iso(), mission_id))
            await self._conn.commit()

    async def set_status(self, mission_id: int, status: str) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE missions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now_iso(), mission_id))
            await self._conn.commit()

    async def increment_failures(self, mission_id: int) -> int:
        """Increment failure count and return the new total. Returns 0 if not found."""
        async with self._lock:
            await self._conn.execute(
                "UPDATE missions SET failures = failures + 1, updated_at = ? WHERE id = ?",
                (_now_iso(), mission_id))
            await self._conn.commit()
        rows = await self._conn.execute_fetchall(
            "SELECT failures FROM missions WHERE id = ?", (mission_id,))
        return rows[0]["failures"] if rows else 0

    async def reset_running(self) -> list[Mission]:
        """On startup, mark stranded RUNNING missions as PENDING for retry."""
        now = _now_iso()
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE missions SET status = ?, updated_at = ? "
                f"WHERE status = ? RETURNING {_COLS}",
                (PENDING, now, RUNNING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return [_mission(r) for r in rows]
