import asyncio
from datetime import datetime, timezone

import aiosqlite


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class SqliteTurnLog:
    """Per-turn receipts (code-generated confirmations) and the outbox of
    messages Ari's MCP server asks the bot to send."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def _write(self, sql: str, params: tuple) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur

    async def add_receipt(self, turn_id: str, text: str) -> None:
        await self._write("INSERT INTO receipts (turn_id, text, created_at) VALUES (?, ?, ?)",
                          (turn_id, text, _iso(datetime.now(timezone.utc))))

    async def receipts(self, turn_id: str) -> list[str]:
        rows = await self._conn.execute_fetchall(
            "SELECT text FROM receipts WHERE turn_id = ? ORDER BY id", (turn_id,))
        return [r["text"] for r in rows]

    async def purge_receipts(self, before: datetime) -> int:
        cur = await self._write("DELETE FROM receipts WHERE created_at < ?", (_iso(before),))
        return cur.rowcount

    async def outbox_add(self, chat_id: str, text: str) -> int:
        cur = await self._write("INSERT INTO outbox (chat_id, text, created_at) VALUES (?, ?, ?)",
                                (chat_id, text, _iso(datetime.now(timezone.utc))))
        return cur.lastrowid

    async def outbox_pending(self, limit: int = 50) -> list[tuple[int, str, str, int]]:
        rows = await self._conn.execute_fetchall(
            "SELECT id, chat_id, text, attempts FROM outbox WHERE sent_at IS NULL "
            "ORDER BY id LIMIT ?", (limit,))
        return [(r["id"], r["chat_id"], r["text"], r["attempts"]) for r in rows]

    async def outbox_mark_sent(self, item_id: int, now: datetime) -> None:
        await self._write("UPDATE outbox SET sent_at = ? WHERE id = ?", (_iso(now), item_id))

    async def outbox_mark_failed(self, item_id: int) -> int:
        await self._write("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (item_id,))
        rows = await self._conn.execute_fetchall(
            "SELECT attempts FROM outbox WHERE id = ?", (item_id,))
        return rows[0]["attempts"] if rows else 0

    async def outbox_drop(self, item_id: int) -> None:
        await self._write("DELETE FROM outbox WHERE id = ?", (item_id,))
