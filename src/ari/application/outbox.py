import asyncio
import logging
from datetime import timedelta

log = logging.getLogger("ari.outbox")


class OutboxFlusher:
    """Sends what Ari's MCP server queued (messages, approval notices). Runs right
    after each turn and on every scheduler tick; a lock makes concurrent runs
    send each message once."""

    def __init__(self, turn_log, send, clock, max_attempts: int = 3,
                 keep_receipts: timedelta = timedelta(days=1)):
        self._log, self._send, self._clock = turn_log, send, clock
        self._max, self._keep = max_attempts, keep_receipts
        self._lock = asyncio.Lock()

    async def __call__(self) -> None:
        async with self._lock:
            for item_id, chat_id, text, _attempts in await self._log.outbox_pending():
                if await self._send(chat_id, text):
                    await self._log.outbox_mark_sent(item_id, self._clock())
                    continue
                if await self._log.outbox_mark_failed(item_id) >= self._max:
                    log.warning("dropping outbox message %s to %s after %s attempts",
                                item_id, chat_id, self._max)
                    await self._log.outbox_drop(item_id)
            await self._log.purge_receipts(self._clock() - self._keep)
