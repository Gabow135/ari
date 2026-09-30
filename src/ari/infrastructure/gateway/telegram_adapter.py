import logging

from ari.domain.ports.gateway_port import IncomingMessage
from ari.domain.skills.models import Attachment, RawInbound

log = logging.getLogger("ari.telegram")
TELEGRAM_LIMIT = 4096


# Phase 1: the composition root (main.py) owns the PTB run_polling loop and
# wires the on-message handler via post_init so the aiosqlite connection is
# always created inside the same event loop that run_polling drives.
# TelegramAdapter exposes only the stateless conversion helpers used there.
class TelegramAdapter:
    @staticmethod
    def to_incoming(update) -> IncomingMessage | None:
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if msg is None or not getattr(msg, "text", None):
            return None
        user = msg.from_user
        return IncomingMessage(
            user_id=str(user.id), chat_id=str(msg.chat_id), text=msg.text,
            display_name=getattr(user, "first_name", None) or getattr(user, "username", None) or "")

    @staticmethod
    def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
        return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


async def voice_to_text(update, download, manager, is_owner: bool) -> str | None:
    """Extract a voice/audio attachment, download its bytes, and transcribe it through
    the skill manager's inbound transforms. Returns the text, or None when there is no
    audio media or no skill handled it. `download` is an async callable(media)->bytes."""
    msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if msg is None:
        return None
    media = getattr(msg, "voice", None) or getattr(msg, "audio", None)
    if media is None:
        return None
    data = await download(media)
    raw = RawInbound(
        user_id=str(msg.from_user.id), chat_id=str(msg.chat_id), text=None,
        attachment=Attachment(kind="voice", mime=getattr(media, "mime_type", None) or "audio/ogg",
                              data=bytes(data), duration=getattr(media, "duration", None)))
    return await manager.run_inbound(raw, is_owner)


async def media_to_text(update, download, manager, is_owner: bool) -> str | None:
    """Extract a photo (largest size) or document, download its bytes, and run it through
    the skill manager's inbound transforms (vision for images, extraction for docs).
    Returns the text, or None when there is no photo/document or no skill handled it.
    `download` is an async callable(media)->bytes."""
    msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if msg is None:
        return None
    photos = getattr(msg, "photo", None)
    doc = getattr(msg, "document", None)
    if photos:
        media, kind, mime, filename = photos[-1], "photo", "image/jpeg", "photo.jpg"
    elif doc is not None:
        mime = getattr(doc, "mime_type", None) or "application/octet-stream"
        kind = "photo" if mime.startswith("image/") else "document"
        media, filename = doc, getattr(doc, "file_name", None) or ""
    else:
        return None
    data = await download(media)
    raw = RawInbound(
        user_id=str(msg.from_user.id), chat_id=str(msg.chat_id),
        text=getattr(msg, "caption", None),
        attachment=Attachment(kind=kind, mime=mime, data=bytes(data), filename=filename))
    return await manager.run_inbound(raw, is_owner)
