from dataclasses import dataclass
from datetime import datetime

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"


@dataclass(frozen=True)
class CodingRequest:
    """A code change Ari proposed in the owner's chat, waiting to be planned."""
    id: int
    user_id: str
    chat_id: str
    instruction: str
    target: str | None
    created_at: datetime  # tz-aware UTC
