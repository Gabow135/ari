from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """A shell command awaiting the owner's «dale». ``proposed`` mirrors
    PendingAction: a command always comes from a background proposal
    (proponer_comando), so only an exact «dale» confirms it."""
    command: str
    proposed: bool = True

    def __post_init__(self) -> None:
        if not self.command or not self.command.strip():
            raise ValueError("PendingCommand.command must not be empty")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of running a shell command. ``timed_out`` means it was killed for
    exceeding the limit (returncode is then meaningless)."""
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
