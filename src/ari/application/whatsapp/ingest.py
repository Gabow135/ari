import logging
from typing import Awaitable, Callable, Iterable

from ari.application.whatsapp.filter import passes_filter
from ari.domain.whatsapp.entities import InboundWhatsApp

log = logging.getLogger("ari.whatsapp")

Notify = Callable[[str, str], Awaitable[None]]


class WhatsAppIngest:
    def __init__(self, store, notify: Notify, owner_ids: Iterable[str], since: int = 0):
        self._store = store
        self._notify = notify
        self._owners = list(owner_ids)
        self._since = since  # unix epoch; drop messages older than startup

    async def __call__(self, m: InboundWhatsApp) -> None:
        try:
            # Ignore the owner's own outgoing messages and the history-sync /
            # offline backlog replayed on pairing (anything older than startup).
            if m.is_from_me:
                return
            if self._since and m.ts and m.ts < self._since:
                return
            rid = await self._store.record_inbound(m)
            f = await self._store.get_filter()
            if not passes_filter(m.contact_name, m.wa_chat_id, m.text, f):
                return
            body = m.text.strip() or f"[{m.media_kind or 'media'}]"
            for owner in self._owners:
                await self._notify(owner, f"📱 WhatsApp de {m.contact_name}: {body}")
            await self._store.mark_notified(rid)
        except Exception:  # never break the WhatsApp event loop
            log.exception("whatsapp ingest failed")
