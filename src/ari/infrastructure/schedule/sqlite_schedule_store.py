import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import aiosqlite

from ari.domain.schedule.entities import (
    ACTIVE, CANCELLED, DONE, PAUSED, RUNNING, ScheduleItem)

_COLS = "id, user_id, chat_id, kind, text, next_run_at, cron, status, failures"
_OPEN = (ACTIVE, RUNNING, PAUSED)


def _iso(dt: datetime) -> str:
    # Fixed format keeps lexicographic order == chronological order in SQL.
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _item(r) -> ScheduleItem:
    return ScheduleItem(r["id"], r["user_id"], r["chat_id"], r["kind"], r["text"],
                        datetime.fromisoformat(r["next_run_at"]), r["cron"],
                        r["status"], r["failures"])


class SqliteScheduleStore:
    """Schedules + a small key/value table for engine state (see persistence/db.py)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def _write(self, sql: str, params: tuple) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur

    async def add(self, user_id, chat_id, kind, text, next_run_at, cron) -> int:
        cur = await self._write(
            "INSERT INTO schedules (user_id, chat_id, kind, text, next_run_at, cron, status, "
            "failures, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (user_id, chat_id, kind, text, _iso(next_run_at), cron, ACTIVE,
             _iso(datetime.now(timezone.utc))))
        return cur.lastrowid

    async def get(self, item_id: int) -> ScheduleItem | None:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM schedules WHERE id = ?", (item_id,))
        return _item(rows[0]) if rows else None

    async def list_for_user(self, user_id: str) -> list[ScheduleItem]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM schedules WHERE user_id = ? AND status IN (?, ?, ?) "
            "ORDER BY next_run_at", (user_id, *_OPEN))
        return [_item(r) for r in rows]

    async def count_active(self, user_id: str) -> int:
        rows = await self._conn.execute_fetchall(
            "SELECT COUNT(*) AS n FROM schedules WHERE user_id = ? AND status IN (?, ?, ?)",
            (user_id, *_OPEN))
        return rows[0]["n"]

    async def set_status(self, item_id: int, status: str) -> None:
        await self._write("UPDATE schedules SET status = ? WHERE id = ?", (status, item_id))

    async def claim_due(self, now: datetime) -> list[ScheduleItem]:
        async with self._lock:
            rows = await self._conn.execute_fetchall(
                f"SELECT {_COLS} FROM schedules WHERE status = ? AND next_run_at <= ? "
                "ORDER BY next_run_at", (ACTIVE, _iso(now)))
            if rows:
                ids = [r["id"] for r in rows]
                marks = ",".join("?" * len(ids))
                await self._conn.execute(
                    f"UPDATE schedules SET status = ? WHERE id IN ({marks})", (RUNNING, *ids))
                await self._conn.commit()
        return [replace(_item(r), status=RUNNING) for r in rows]

    async def reschedule(self, item_id: int, next_run_at: datetime, now: datetime) -> None:
        await self._write(
            "UPDATE schedules SET status = ?, next_run_at = ?, failures = 0, last_run_at = ? "
            "WHERE id = ?", (ACTIVE, _iso(next_run_at), _iso(now), item_id))

    async def finish(self, item_id: int, now: datetime) -> None:
        await self._write("UPDATE schedules SET status = ?, last_run_at = ? WHERE id = ?",
                          (DONE, _iso(now), item_id))

    async def record_failure(self, item_id: int, retry_at: datetime) -> int:
        await self._write(
            "UPDATE schedules SET status = ?, next_run_at = ?, failures = failures + 1 "
            "WHERE id = ?", (ACTIVE, _iso(retry_at), item_id))
        return (await self.get(item_id)).failures

    async def reset_running(self) -> int:
        cur = await self._write("UPDATE schedules SET status = ? WHERE status = ?",
                                (ACTIVE, RUNNING))
        return cur.rowcount

    async def cancel_user(self, user_id: str) -> int:
        cur = await self._write(
            "UPDATE schedules SET status = ? WHERE user_id = ? AND status IN (?, ?, ?)",
            (CANCELLED, user_id, *_OPEN))
        return cur.rowcount

    async def upcoming(self, user_id: str, until: datetime) -> list[ScheduleItem]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM schedules WHERE user_id = ? AND status = ? "
            "AND next_run_at <= ? ORDER BY next_run_at", (user_id, ACTIVE, _iso(until)))
        return [_item(r) for r in rows]

    async def kv_get(self, key: str) -> str | None:
        rows = await self._conn.execute_fetchall("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    async def kv_set(self, key: str, value: str) -> None:
        await self._write("INSERT INTO kv (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    async def kv_delete(self, key: str) -> None:
        await self._write("DELETE FROM kv WHERE key = ?", (key,))
