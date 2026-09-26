from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Attachment:
    kind: str            # "voice" | "audio"
    mime: str            # e.g. "audio/ogg"
    data: bytes
    filename: str = ""
    duration: int | None = None


@dataclass(frozen=True, slots=True)
class RawInbound:
    user_id: str
    chat_id: str
    text: str | None = None
    attachment: Attachment | None = None


@dataclass(frozen=True, slots=True)
class Delivery:
    kind: str            # "voice" | "audio" | "text"
    data: bytes | None = None
    mime: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class InboundContext:
    came_from_voice: bool
    chat_id: str
    user_id: str
