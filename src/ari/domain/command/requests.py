from dataclasses import dataclass
from datetime import datetime

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"


@dataclass(frozen=True)
class CommandRequest:
    """A shell command Ari proposed in the owner's chat, waiting for «dale»."""
    id: int
    user_id: str
    chat_id: str
    command: str
    created_at: datetime  # tz-aware UTC
