import asyncio
import logging

log = logging.getLogger("ari.whatsapp")


class WhatsAppOutbox:
    def __init__(self, store, port, notify, owner_ids, min_delay=3.0, sleep=asyncio.sleep):
        self._store = store
        self._port = port
        self._notify = notify
        self._owners = list(owner_ids)
        self._min_delay = min_delay
        self._sleep = sleep

    async def __call__(self) -> None:
        while True:
            row = await self._store.claim_queued()
            if row is None:
                return
            try:
                if self._min_delay:
                    await self._sleep(self._min_delay)  # human pace, anti-ban
                await self._port.send(row.wa_chat_id, row.text)
                await self._store.mark_sent(row.id)
            except Exception:
                log.exception("whatsapp send failed for %s", row.wa_chat_id)
                await self._store.mark_failed(row.id)
                for owner in self._owners:
                    await self._notify(owner, f"❌ No pude enviar a {row.contact_name}")
