from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    user_id: str
    role: Role
    content: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.content or not self.content.strip():
            raise ValueError("Message.content must not be empty")
