from dataclasses import dataclass
from datetime import datetime

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"


@dataclass(frozen=True)
class FileRequest:
    id: int
    user_id: str
    chat_id: str
    path: str
    created_at: datetime
