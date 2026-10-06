"""Thin neonize (whatsmeow) adapter. All logic lives in `inbound_from_event`;
the client calls themselves are validated manually during real pairing."""
import logging
import os

from ari.domain.whatsapp.entities import InboundWhatsApp

log = logging.getLogger("ari.whatsapp")

_MEDIA = (("imageMessage", "image"), ("audioMessage", "audio"),
          ("videoMessage", "video"), ("documentMessage", "document"),
          ("stickerMessage", "sticker"))


def inbound_from_event(event) -> InboundWhatsApp:
    msg, info = event.Message, event.Info
    source = info.MessageSource
    text = getattr(msg, "conversation", "") or ""
    ext = getattr(msg, "extendedTextMessage", None)
    if not text and ext is not None:
        text = getattr(ext, "text", "") or ""
    media_kind = ""
    for attr, kind in _MEDIA:
        if getattr(msg, attr, None):
            media_kind = kind
            break
    # neonize's wrapper protos are PascalCase; MessageSource.Chat is a JID with
    # User + Server (e.g. 549111@s.whatsapp.net, groupid@g.us). Build the chat id
    # from the parts (keeps this pure — no neonize import — for the unit tests).
    chat = getattr(source, "Chat", None)
    user = getattr(chat, "User", "") or ""
    server = getattr(chat, "Server", "") or "s.whatsapp.net"
    wa_chat_id = f"{user}@{server}" if user else ""
    contact = getattr(info, "Pushname", "") or user or wa_chat_id.split("@", 1)[0]
    return InboundWhatsApp(
        wa_chat_id=wa_chat_id,
        contact_name=contact,
        text=text,
        media_kind=media_kind,
        is_group=bool(getattr(source, "IsGroup", False)),
        is_from_me=bool(getattr(source, "IsFromMe", False)),
        ts=int(getattr(info, "Timestamp", 0) or 0),
    )


class NeonizeWhatsApp:
    """Implements WhatsAppPort over neonize's async client."""

    def __init__(self, session_dir: str):
        self._session_dir = session_dir
        self._client = None
        self._state = "logged_out"

    async def start(self, on_message) -> None:
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, MessageEv
        os.makedirs(self._session_dir, exist_ok=True)
        db = os.path.join(self._session_dir, "session.db")
        self._client = NewAClient(db)

        @self._client.event(ConnectedEv)
        async def _on_connected(client, event):  # noqa: ANN001
            self._state = "connected"

        @self._client.event(MessageEv)
        async def _on_message(client, event):  # noqa: ANN001
            try:
                await on_message(inbound_from_event(event))
            except Exception:
                log.exception("whatsapp on_message failed")

        await self._client.connect()

    async def send(self, wa_chat_id: str, text: str) -> None:
        from neonize.utils.jid import build_jid
        jid = build_jid(wa_chat_id.split("@", 1)[0])
        await self._client.send_message(jid, text)

    async def pair_phone(self, number: str) -> str:
        # The async client exposes PascalCase PairPhone; show_push_notification
        # is a required positional. Returns the code to enter in WhatsApp mobile.
        return await self._client.PairPhone(number, True)

    def connection_state(self) -> str:
        # Reflect the stored device registration, not the async ConnectedEv flag:
        # connect() returns before ConnectedEv fires, so an event-based flag races
        # and would re-pair an already-linked session on every restart.
        if self._client is None:
            return "logged_out"
        try:
            logged = self._client.is_logged_in
            logged = logged() if callable(logged) else bool(logged)
        except Exception:
            logged = False
        return "connected" if logged else "logged_out"
