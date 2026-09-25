from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CodingInstruction:
    user_id: str
    text: str
    target: str | None = None

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("CodingInstruction.text must not be empty")


@dataclass(frozen=True, slots=True)
class CodingPlan:
    summary: str
    target_dir: str
    instruction_text: str


@dataclass(frozen=True, slots=True)
class CodingResult:
    ok: bool
    branch: str
    changed_files: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PendingAction:
    instruction: CodingInstruction
    plan: CodingPlan
