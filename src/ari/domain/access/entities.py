from dataclasses import dataclass

PENDING = "pending"
APPROVED = "approved"


@dataclass(frozen=True)
class AccessRecord:
    user_id: str
    username: str | None
    code: str  # normalized: uppercase, no separators
    status: str  # PENDING | APPROVED
