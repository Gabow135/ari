from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundWhatsApp:
    wa_chat_id: str
    contact_name: str
    text: str
    media_kind: str = ""   # "", "image", "audio", "video", "document", "sticker"
    is_group: bool = False
    is_from_me: bool = False  # the owner's own outgoing message
    ts: int = 0               # unix epoch seconds (0 = unknown)
