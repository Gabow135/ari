from dataclasses import dataclass
from datetime import datetime

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"


@dataclass(frozen=True)
class CredentialRequest:
    """A credential the owner wants to load, waiting for the bot to mint a vault link."""
    id: int
    user_id: str
    chat_id: str
    requested: str        # free-text hint of what the owner wants to provide
    created_at: datetime  # tz-aware UTC
