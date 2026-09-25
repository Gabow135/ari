from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

THINKING = "thinking"
TOOL = "tool"
TEXT = "text"


@dataclass(frozen=True)
class ProgressEvent:
    kind: str  # THINKING | TOOL | TEXT
    name: str = ""  # tool name, for TOOL
    detail: str = ""  # tool target (file, command…) for TOOL; the delta for TEXT


class ProgressSink(Protocol):
    def emit(self, event: ProgressEvent) -> None: ...


# Whoever is presenting the current turn (e.g. a Telegram progress message) sets
# this; LLM adapters report through it without it being threaded through every
# use case. Unset means nobody is listening.
current_progress: ContextVar[ProgressSink | None] = ContextVar("ari_progress", default=None)


def emit_progress(event: ProgressEvent) -> None:
    sink = current_progress.get()
    if sink is not None:
        sink.emit(event)
