from dataclasses import dataclass
from datetime import datetime

REMINDER = "reminder"  # fixed text sent at the time
TASK = "task"  # instruction Ari runs at the time

ACTIVE = "active"
RUNNING = "running"
DONE = "done"
CANCELLED = "cancelled"
PAUSED = "paused"


@dataclass(frozen=True)
class ScheduleItem:
    id: int
    user_id: str
    chat_id: str
    kind: str
    text: str
    next_run_at: datetime  # tz-aware, UTC
    cron: str | None  # None = one-shot
    status: str
    failures: int = 0
