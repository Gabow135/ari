from dataclasses import dataclass
from datetime import datetime

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
PAUSED = "paused"

MAX_FAILURES = 3


@dataclass(frozen=True)
class Mission:
    id: int
    user_id: str
    chat_id: str
    instruction: str
    status: str
    failures: int
    result: str | None
    created_at: datetime
    updated_at: datetime
