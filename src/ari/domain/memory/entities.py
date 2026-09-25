from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Recall:
    id: int | None
    user_id: str
    content: str
    metadata: dict = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Fact:
    user_id: str
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class Summary:
    user_id: str
    content: str
