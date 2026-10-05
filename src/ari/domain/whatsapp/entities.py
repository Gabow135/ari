from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundWhatsApp:
    wa_chat_id: str
    contact_name: str
    text: str
    media_kind: str = ""   # "", "image", "audio", "video", "document", "sticker"
    is_group: bool = False
