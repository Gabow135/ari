# Ari — Phase 2 (Telegram-driven Coding Agent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner instruct Ari over Telegram to write/modify code; Ari plans, asks for confirmation, then drives Claude Code (with tools) on an isolated branch and reports back.

**Architecture:** Additive to Phase 1's hexagonal layout. A `CommandRouter` sits in front of the Phase 1 chat path and forwards coding instructions to new application use cases (`RequestCoding`, `ConfirmCoding`) guarded by an `Authorizer` (owner-only) and a `PendingStore` (plan→confirm state). The dangerous capability lives behind a domain `CoderPort`, implemented by `ClaudeCodeCoder` which runs `claude -p` with tools inside a path validated by `Workspace` (sandbox), on a fresh git branch, never pushing.

**Tech Stack:** Python 3.12+ (invoke as `python3`), asyncio, `python-telegram-bot`, the `claude` CLI, `pytest`/`pytest-asyncio`. No new third-party dependency.

**Spec:** `docs/superpowers/specs/2026-09-25-ari-fase2-coding-agent-design.md`

## Global Constraints

- Python 3.12+; all I/O `async`. Run tests with `python3 -m pytest` (bare `pip`/`pytest` resolve to 3.9 here — always `python3 -m ...`).
- Domain (`src/ari/domain/**`) and application (`src/ari/application/**`) MUST NOT import `anthropic`, `telegram`, `aiosqlite`, `sqlite_vec`, `fastembed`, or `pydantic`. (Coding domain/app included.)
- Coding is **owner-only**: only Telegram user IDs in `ARI_OWNER_IDS` may trigger it.
- Every coder target path MUST resolve inside `ARI_ALLOWED_ROOT`; anything else is refused.
- Execution always happens on a **fresh git branch**; NEVER commit to the default branch, NEVER push.
- Nothing with side effects runs before an explicit owner confirmation of the shown plan.
- No secrets on subprocess command lines or in logs.
- Conventional Commits; no AI attribution / no `Co-Authored-By` trailer.

## Review Focus

- **Target path escapes the sandbox** (`..`, absolute path, symlink) — `Workspace.resolve` must refuse anything outside `ARI_ALLOWED_ROOT`. (Test in Task 4.)
- **Non-owner sends `/code`** — must be declined with no planning/execution. (Test in Task 3 + Task 5.)
- **Confirmation (`dale`) with no pending action** — must be treated as ordinary chat, never crash or execute. (Test in Task 7.)
- **Coding run fails or times out** — must report and preserve the branch, never crash the bot. (Test in Task 7 + Task 9.)
- **Second coding request while one is running for that user** — busy guard must reject the new one. (Test in Task 3.)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/ari/domain/coding/entities.py` | `CodingInstruction`, `CodingPlan`, `CodingResult`, `PendingAction` |
| `src/ari/domain/coding/coder_port.py` | `CoderPort` protocol |
| `src/ari/application/coding/authorizer.py` | `Authorizer` (owner allowlist) |
| `src/ari/application/coding/pending_store.py` | `PendingStore` (per-user pending + busy guard) |
| `src/ari/application/coding/command_router.py` | parse `/code`/`/fase2`; affirmative/negative helpers |
| `src/ari/application/coding/request_coding.py` | `RequestCoding` use case (plan + store) |
| `src/ari/application/coding/confirm_coding.py` | `ConfirmCoding` use case (branch + execute + report) |
| `src/ari/infrastructure/coder/workspace.py` | `Workspace.resolve` (sandbox) + `create_branch` |
| `src/ari/infrastructure/coder/claude_code_coder.py` | `CoderPort` on the `claude` CLI with tools |
| `src/ari/config/settings.py` | + `owner_ids`, `allowed_root`, `coder_model`, `coding_timeout_seconds` |
| `src/ari/main.py` | route messages: chat (Phase 1) vs coding; background exec + reporting |
| `tests/coding_fakes.py` | `FakeCoder` for tests |

---

### Task 1: Coding domain entities

**Files:**
- Create: `src/ari/domain/coding/__init__.py`, `src/ari/domain/coding/entities.py`
- Test: `tests/domain/test_coding_entities.py`

**Interfaces:**
- Produces:
  - `CodingInstruction(user_id: str, text: str, target: str | None = None)` — frozen; `text` non-empty after strip.
  - `CodingPlan(summary: str, target_dir: str, instruction_text: str)` — frozen.
  - `CodingResult(ok: bool, branch: str, changed_files: list[str], commits: list[str], detail: str = "")` — frozen (default lists via field).
  - `PendingAction(instruction: CodingInstruction, plan: CodingPlan)` — frozen.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_coding_entities.py
import pytest

from ari.domain.coding.entities import (
    CodingInstruction, CodingPlan, CodingResult, PendingAction)


def test_instruction_rejects_empty_text():
    with pytest.raises(ValueError):
        CodingInstruction(user_id="u1", text="   ")


def test_entities_hold_fields():
    instr = CodingInstruction("u1", "add a healthcheck", target=None)
    plan = CodingPlan(summary="1. add endpoint", target_dir="/repo", instruction_text=instr.text)
    res = CodingResult(ok=True, branch="ari/tg-x", changed_files=["a.py"], commits=["abc123"])
    pend = PendingAction(instruction=instr, plan=plan)
    assert pend.plan.target_dir == "/repo"
    assert res.ok and res.changed_files == ["a.py"]
    assert instr.target is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_coding_entities.py -v`
Expected: FAIL (`ModuleNotFoundError: ari.domain.coding.entities`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/coding/entities.py
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
```

Create `src/ari/domain/coding/__init__.py` (empty).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/domain/test_coding_entities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/coding tests/domain/test_coding_entities.py
git commit -m "feat: add coding domain entities"
```

---

### Task 2: CoderPort + FakeCoder

**Files:**
- Create: `src/ari/domain/coding/coder_port.py`
- Create: `tests/coding_fakes.py`
- Test: `tests/domain/test_coder_port.py`

**Interfaces:**
- Consumes: entities from Task 1.
- Produces:
  - `CoderPort` protocol: `async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan`; `async def execute(self, plan: CodingPlan, branch: str) -> CodingResult`.
  - `FakeCoder(plan_summary="do X", fail=False)` implementing `CoderPort`; records calls; `execute` returns `CodingResult(ok=not fail, branch=branch, changed_files=["f.py"], commits=["deadbee"])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_coder_port.py
from ari.domain.coding.entities import CodingInstruction, CodingPlan
from tests.coding_fakes import FakeCoder


async def test_fake_coder_plans_and_executes():
    c = FakeCoder(plan_summary="add endpoint")
    instr = CodingInstruction("u1", "add healthcheck")
    plan = await c.plan(instr, "/repo")
    assert plan.summary == "add endpoint"
    assert plan.target_dir == "/repo"
    res = await c.execute(plan, "ari/tg-1")
    assert res.ok and res.branch == "ari/tg-1"
    assert c.executed == [("ari/tg-1", plan)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_coder_port.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/coding/coder_port.py
from typing import Protocol

from ari.domain.coding.entities import CodingInstruction, CodingPlan, CodingResult


class CoderPort(Protocol):
    async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan: ...
    async def execute(self, plan: CodingPlan, branch: str) -> CodingResult: ...
```

```python
# tests/coding_fakes.py
from ari.domain.coding.entities import CodingInstruction, CodingPlan, CodingResult


class FakeCoder:
    def __init__(self, plan_summary: str = "do X", fail: bool = False):
        self.plan_summary = plan_summary
        self.fail = fail
        self.planned: list[tuple[CodingInstruction, str]] = []
        self.executed: list[tuple[str, CodingPlan]] = []

    async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan:
        self.planned.append((instruction, target_dir))
        return CodingPlan(summary=self.plan_summary, target_dir=target_dir,
                          instruction_text=instruction.text)

    async def execute(self, plan: CodingPlan, branch: str) -> CodingResult:
        self.executed.append((branch, plan))
        if self.fail:
            return CodingResult(ok=False, branch=branch, detail="boom")
        return CodingResult(ok=True, branch=branch, changed_files=["f.py"], commits=["deadbee"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/domain/test_coder_port.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/coding/coder_port.py tests/coding_fakes.py tests/domain/test_coder_port.py
git commit -m "feat: add CoderPort and FakeCoder"
```

---

### Task 3: Authorizer + PendingStore

**Files:**
- Create: `src/ari/application/coding/__init__.py`, `src/ari/application/coding/authorizer.py`, `src/ari/application/coding/pending_store.py`
- Test: `tests/application/test_authorizer_pending.py`

**Interfaces:**
- Produces:
  - `Authorizer(owner_ids: set[str])` with `is_owner(user_id: str) -> bool`.
  - `PendingStore()` with: `put(user_id, PendingAction)`, `get(user_id) -> PendingAction | None`, `pop(user_id) -> PendingAction | None`, `clear(user_id)`, `mark_busy(user_id)`, `clear_busy(user_id)`, `is_busy(user_id) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_authorizer_pending.py
from ari.application.coding.authorizer import Authorizer
from ari.application.coding.pending_store import PendingStore
from ari.domain.coding.entities import CodingInstruction, CodingPlan, PendingAction


def test_authorizer_owner_only():
    a = Authorizer({"42", "7"})
    assert a.is_owner("42")
    assert not a.is_owner("999")


def _pending():
    instr = CodingInstruction("u1", "x")
    return PendingAction(instr, CodingPlan("s", "/r", "x"))


def test_pending_store_lifecycle_and_busy_guard():
    s = PendingStore()
    assert s.get("u1") is None
    s.put("u1", _pending())
    assert s.get("u1") is not None
    assert s.pop("u1") is not None
    assert s.get("u1") is None      # pop removed it

    assert not s.is_busy("u1")
    s.mark_busy("u1")
    assert s.is_busy("u1")
    s.clear_busy("u1")
    assert not s.is_busy("u1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_authorizer_pending.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/coding/authorizer.py
class Authorizer:
    def __init__(self, owner_ids: set[str]):
        self._owner_ids = {str(x) for x in owner_ids}

    def is_owner(self, user_id: str) -> bool:
        return str(user_id) in self._owner_ids
```

```python
# src/ari/application/coding/pending_store.py
from ari.domain.coding.entities import PendingAction


class PendingStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingAction] = {}
        self._busy: set[str] = set()

    def put(self, user_id: str, action: PendingAction) -> None:
        self._pending[user_id] = action

    def get(self, user_id: str) -> PendingAction | None:
        return self._pending.get(user_id)

    def pop(self, user_id: str) -> PendingAction | None:
        return self._pending.pop(user_id, None)

    def clear(self, user_id: str) -> None:
        self._pending.pop(user_id, None)

    def mark_busy(self, user_id: str) -> None:
        self._busy.add(user_id)

    def clear_busy(self, user_id: str) -> None:
        self._busy.discard(user_id)

    def is_busy(self, user_id: str) -> bool:
        return user_id in self._busy
```

Create `src/ari/application/coding/__init__.py` (empty).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_authorizer_pending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/coding/__init__.py src/ari/application/coding/authorizer.py src/ari/application/coding/pending_store.py tests/application/test_authorizer_pending.py
git commit -m "feat: add coding authorizer and pending store"
```

---

### Task 4: Workspace sandbox (SECURITY-CRITICAL)

**Files:**
- Create: `src/ari/infrastructure/coder/__init__.py`, `src/ari/infrastructure/coder/workspace.py`
- Test: `tests/infrastructure/test_workspace.py`

**Interfaces:**
- Produces:
  - `Workspace(allowed_root: str)` with:
    - `resolve(self, target: str | None, default_dir: str) -> str` — returns the canonical absolute path of `target` (or `default_dir` when `target is None`); raises `ValueError` if the result is not inside `allowed_root` or does not exist.
    - `async def create_branch(self, target_dir: str, slug: str) -> str` — `git -C <dir> checkout -b <branch>` from current HEAD; returns the branch name. (Integration; unit tests focus on `resolve`.)

- [ ] **Step 1: Write the failing test** (security is the point here)

```python
# tests/infrastructure/test_workspace.py
import os
import pytest

from ari.infrastructure.coder.workspace import Workspace


@pytest.fixture
def root(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "sub").mkdir()
    (tmp_path / "outside").mkdir()
    return tmp_path


def test_resolve_accepts_inside_root(root):
    ws = Workspace(str(root))
    got = ws.resolve("proj/sub", default_dir=str(root / "proj"))
    assert got == os.path.realpath(str(root / "proj" / "sub"))


def test_resolve_defaults_when_target_none(root):
    ws = Workspace(str(root))
    assert ws.resolve(None, default_dir=str(root / "proj")) == os.path.realpath(str(root / "proj"))


def test_resolve_rejects_parent_traversal(root):
    ws = Workspace(str(root / "proj"))
    with pytest.raises(ValueError):
        ws.resolve("../outside", default_dir=str(root / "proj"))


def test_resolve_rejects_absolute_escape(root):
    ws = Workspace(str(root / "proj"))
    with pytest.raises(ValueError):
        ws.resolve("/etc", default_dir=str(root / "proj"))


def test_resolve_rejects_symlink_escape(root):
    ws = Workspace(str(root / "proj"))
    link = root / "proj" / "escape"
    os.symlink(str(root / "outside"), str(link))
    with pytest.raises(ValueError):
        ws.resolve("escape", default_dir=str(root / "proj"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_workspace.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/coder/workspace.py
import asyncio
import os


class Workspace:
    def __init__(self, allowed_root: str):
        self._root = os.path.realpath(allowed_root)

    def resolve(self, target: str | None, default_dir: str) -> str:
        raw = default_dir if target is None or not target.strip() else target
        if not os.path.isabs(raw):
            raw = os.path.join(self._root, raw)
        path = os.path.realpath(raw)  # resolves symlinks + '..'
        root = self._root
        if path != root and not path.startswith(root + os.sep):
            raise ValueError(f"target '{target}' resolves outside allowed root")
        if not os.path.isdir(path):
            raise ValueError(f"target directory does not exist: {path}")
        return path

    async def create_branch(self, target_dir: str, slug: str) -> str:
        branch = f"ari/tg-{slug}"
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", target_dir, "checkout", "-b", branch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git checkout -b failed: {err.decode(errors='replace')[:300]}")
        return branch
```

> Note: the containment check compares against `realpath(allowed_root)` and uses
> `realpath(target)` so `..` and symlinks are resolved before the check. `resolve`
> only accepts existing directories.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_workspace.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/coder/__init__.py src/ari/infrastructure/coder/workspace.py tests/infrastructure/test_workspace.py
git commit -m "feat: add workspace sandbox with path-containment validation"
```

---

### Task 5: CommandRouter (parse coding commands + confirm/cancel helpers)

**Files:**
- Create: `src/ari/application/coding/command_router.py`
- Test: `tests/application/test_command_router.py`

**Interfaces:**
- Produces (module-level functions):
  - `parse_coding_command(text: str) -> tuple[str, str | None] | None` — for `/code <instr>` and `/fase2 <instr>` returns `(instruction_text, target)` where `target` is an optional `dir:<path>` prefix in the instruction (e.g. `/code dir:proj add X` → target `proj`); returns `None` for non-coding text.
  - `is_affirmative(text: str) -> bool` — True for `dale`, `sí`, `si`, `ok`, `dale total`, `yes` (case/space-insensitive, exact token set).
  - `is_negative(text: str) -> bool` — True for `no`, `cancelar`, `cancel`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_command_router.py
from ari.application.coding.command_router import (
    parse_coding_command, is_affirmative, is_negative)


def test_parse_code_command():
    assert parse_coding_command("/code add a healthcheck") == ("add a healthcheck", None)


def test_parse_fase2_command():
    assert parse_coding_command("/fase2 empezá la fase 2") == ("empezá la fase 2", None)


def test_parse_target_prefix():
    assert parse_coding_command("/code dir:proj add X") == ("add X", "proj")


def test_parse_non_command_returns_none():
    assert parse_coding_command("hola, cómo estás") is None
    assert parse_coding_command("/code   ") is None   # empty instruction


def test_affirmative_and_negative():
    assert is_affirmative("dale")
    assert is_affirmative("  Sí ")
    assert not is_affirmative("dale pero esperá")   # only exact affirmatives execute
    assert is_negative("no")
    assert is_negative("cancelar")
    assert not is_negative("nope maybe")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_command_router.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/coding/command_router.py
_AFFIRMATIVE = {"dale", "si", "sí", "ok", "okay", "yes", "dale total"}
_NEGATIVE = {"no", "cancelar", "cancel", "nope"}


def parse_coding_command(text: str) -> tuple[str, str | None] | None:
    stripped = text.strip()
    for prefix in ("/code", "/fase2"):
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            if not rest:
                return None
            target = None
            if rest.startswith("dir:"):
                head, _, tail = rest.partition(" ")
                target = head[len("dir:"):] or None
                rest = tail.strip()
            if not rest:
                return None
            return rest, target
    return None


def is_affirmative(text: str) -> bool:
    return text.strip().casefold() in _AFFIRMATIVE


def is_negative(text: str) -> bool:
    return text.strip().casefold() in _NEGATIVE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_command_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/coding/command_router.py tests/application/test_command_router.py
git commit -m "feat: add coding command parsing and confirm/cancel helpers"
```

---

### Task 6: RequestCoding use case

**Files:**
- Create: `src/ari/application/coding/request_coding.py`
- Test: `tests/application/test_request_coding.py`

**Interfaces:**
- Consumes: `CoderPort` (Task 2), `Workspace` (Task 4), `PendingStore` (Task 3), entities (Task 1).
- Produces: `RequestCoding(coder, workspace, pending_store, default_dir)` with `async def __call__(self, user_id: str, instruction_text: str, target: str | None) -> str` returning the reply text (plan + confirm ask, or a refusal). On success it stores a `PendingAction`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_request_coding.py
import os

from ari.application.coding.request_coding import RequestCoding
from ari.application.coding.pending_store import PendingStore
from ari.infrastructure.coder.workspace import Workspace
from tests.coding_fakes import FakeCoder


async def test_request_plans_and_stores_pending(tmp_path):
    (tmp_path / "repo").mkdir()
    store = PendingStore()
    rc = RequestCoding(FakeCoder(plan_summary="1. add endpoint"),
                       Workspace(str(tmp_path)), store, default_dir=str(tmp_path / "repo"))
    reply = await rc("42", "add healthcheck", target=None)
    assert "add endpoint" in reply
    assert "dale" in reply.lower()
    assert store.get("42") is not None


async def test_request_refuses_bad_target(tmp_path):
    (tmp_path / "repo").mkdir()
    store = PendingStore()
    rc = RequestCoding(FakeCoder(), Workspace(str(tmp_path / "repo")), store,
                       default_dir=str(tmp_path / "repo"))
    reply = await rc("42", "do X", target="../etc")
    assert "no puedo" in reply.lower() or "fuera" in reply.lower()
    assert store.get("42") is None      # nothing stored on refusal
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_request_coding.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/coding/request_coding.py
import logging

from ari.domain.coding.coder_port import CoderPort
from ari.domain.coding.entities import CodingInstruction, PendingAction

log = logging.getLogger("ari.request_coding")


class RequestCoding:
    def __init__(self, coder: CoderPort, workspace, pending_store, default_dir: str):
        self._coder = coder
        self._ws = workspace
        self._store = pending_store
        self._default_dir = default_dir

    async def __call__(self, user_id: str, instruction_text: str, target: str | None) -> str:
        try:
            target_dir = self._ws.resolve(target, self._default_dir)
        except ValueError as exc:
            log.info("coding target refused: %s", exc)
            return "No puedo trabajar ahí: el destino queda fuera de la carpeta permitida."
        instruction = CodingInstruction(user_id, instruction_text, target)
        plan = await self._coder.plan(instruction, target_dir)
        self._store.put(user_id, PendingAction(instruction, plan))
        return (f"Plan para `{target_dir}`:\n{plan.summary}\n\n"
                "Respondé *dale* para ejecutar, o *no* para cancelar.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_request_coding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/coding/request_coding.py tests/application/test_request_coding.py
git commit -m "feat: add RequestCoding use case (plan + confirm prompt)"
```

---

### Task 7: ConfirmCoding use case (branch + execute + report; failure-safe)

**Files:**
- Create: `src/ari/application/coding/confirm_coding.py`
- Test: `tests/application/test_confirm_coding.py`

**Interfaces:**
- Consumes: `CoderPort`, `Workspace` (`create_branch`), `PendingStore`, entities.
- Produces: `ConfirmCoding(coder, workspace, pending_store, slug_source=None)` with `async def __call__(self, user_id: str, report) -> None` where `report` is an `async` callable `report(text: str)`. It pops the pending action, marks busy, creates a branch, executes, reports start + final, and always clears busy. On no pending action it reports nothing and returns (caller decides). Branch slug is derived deterministically from the instruction plus an injected `slug_source()` (defaults to a counter) so tests avoid time/randomness.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_confirm_coding.py
from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.pending_store import PendingStore
from ari.domain.coding.entities import CodingInstruction, CodingPlan, PendingAction
from tests.coding_fakes import FakeCoder


class FakeWorkspace:
    def __init__(self): self.created = []
    async def create_branch(self, target_dir, slug):
        branch = f"ari/tg-{slug}"
        self.created.append((target_dir, branch))
        return branch


def _seed(store, user_id="42"):
    instr = CodingInstruction(user_id, "add healthcheck")
    store.put(user_id, PendingAction(instr, CodingPlan("s", "/repo", instr.text)))


async def test_confirm_executes_and_reports():
    store = PendingStore(); _seed(store)
    coder = FakeCoder()
    msgs = []
    cc = ConfirmCoding(coder, FakeWorkspace(), store, slug_source=lambda: "1")
    await cc("42", report=lambda t: msgs.append(t) or _noop())
    assert coder.executed and coder.executed[0][0] == "ari/tg-1"
    assert any("ari/tg-1" in m for m in msgs)         # start or final names the branch
    assert not store.is_busy("42")                    # busy cleared
    assert store.get("42") is None                    # pending consumed


async def test_confirm_reports_failure_without_crashing():
    store = PendingStore(); _seed(store)
    cc = ConfirmCoding(FakeCoder(fail=True), FakeWorkspace(), store, slug_source=lambda: "1")
    msgs = []
    await cc("42", report=lambda t: msgs.append(t) or _noop())
    assert any("falló" in m.lower() or "error" in m.lower() for m in msgs)
    assert not store.is_busy("42")


async def _noop():
    return None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_confirm_coding.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/coding/confirm_coding.py
import logging
import re

log = logging.getLogger("ari.confirm_coding")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:24] or "task")


class ConfirmCoding:
    def __init__(self, coder, workspace, pending_store, slug_source=None):
        self._coder = coder
        self._ws = workspace
        self._store = pending_store
        self._n = 0
        self._slug_source = slug_source or self._auto_slug

    def _auto_slug(self) -> str:
        self._n += 1
        return str(self._n)

    async def __call__(self, user_id: str, report) -> None:
        pending = self._store.pop(user_id)
        if pending is None:
            return
        self._store.mark_busy(user_id)
        try:
            slug = f"{_slugify(pending.instruction.text)}-{self._slug_source()}"
            branch = await self._ws.create_branch(pending.plan.target_dir, slug)
            await report(f"Arranco en branch `{branch}`…")
            result = await self._coder.execute(pending.plan, branch)
            if result.ok:
                files = ", ".join(result.changed_files) or "(sin cambios)"
                commits = ", ".join(result.commits) or "(sin commits)"
                await report(f"Listo en `{branch}`. Archivos: {files}. Commits: {commits}. "
                             "El push/PR/merge quedan para vos.")
            else:
                await report(f"El trabajo en `{branch}` falló: {result.detail[:300]}. "
                             "Dejé el branch para que lo revises.")
        except Exception as exc:
            log.exception("coding execution failed")
            await report(f"Error ejecutando la tarea: {str(exc)[:300]}")
        finally:
            self._store.clear_busy(user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_confirm_coding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/coding/confirm_coding.py tests/application/test_confirm_coding.py
git commit -m "feat: add ConfirmCoding use case (branch, execute, report, failure-safe)"
```

---

### Task 8: Settings additions

**Files:**
- Modify: `src/ari/config/settings.py`
- Test: `tests/config/test_settings_phase2.py`

**Interfaces:**
- Produces on `Settings`: `owner_ids: str = ""` (comma-separated) + property `owner_id_set -> set[str]`; `allowed_root: str = "."`; `coder_model: str = "claude-sonnet-4-6"`; `coding_timeout_seconds: int = 900`. All via `ARI_` prefix (`ARI_OWNER_IDS`, `ARI_ALLOWED_ROOT`, `ARI_CODER_MODEL`, `ARI_CODING_TIMEOUT_SECONDS`).

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_settings_phase2.py
from ari.config.settings import Settings


def test_phase2_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.setenv("ARI_OWNER_IDS", "42, 7 ,100")
    monkeypatch.setenv("ARI_ALLOWED_ROOT", "/tmp/projects")
    s = Settings()
    assert s.owner_id_set == {"42", "7", "100"}
    assert s.allowed_root == "/tmp/projects"
    assert s.coder_model == "claude-sonnet-4-6"
    assert s.coding_timeout_seconds == 900


def test_owner_id_set_empty_when_unset(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.delenv("ARI_OWNER_IDS", raising=False)
    assert Settings().owner_id_set == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/config/test_settings_phase2.py -v`
Expected: FAIL (`AttributeError: owner_id_set` / missing fields)

- [ ] **Step 3: Write minimal implementation**

Add to `Settings` (keep existing fields):

```python
    owner_ids: str = ""
    allowed_root: str = "."
    coder_model: str = "claude-sonnet-4-6"
    coding_timeout_seconds: int = 900

    @property
    def owner_id_set(self) -> set[str]:
        return {p.strip() for p in self.owner_ids.split(",") if p.strip()}
```

Also add these keys to `.env.example` with comments (owner IDs empty by default → coding disabled; `ARI_ALLOWED_ROOT` documents the sandbox root).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/config/test_settings_phase2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/config/settings.py tests/config/test_settings_phase2.py .env.example
git commit -m "feat: add Phase 2 settings (owner ids, allowed root, coder model, timeout)"
```

---

### Task 9: ClaudeCodeCoder adapter (real coding via the CLI)

**Files:**
- Create: `src/ari/infrastructure/coder/claude_code_coder.py`
- Test: `tests/infrastructure/test_claude_code_coder.py`

**Interfaces:**
- Consumes: entities, `CoderPort` contract.
- Produces: `ClaudeCodeCoder(model="claude-sonnet-4-6", claude_bin="claude", timeout=900, plan_runner=None, exec_runner=None)` implementing `CoderPort`. `plan_runner(instruction_text, target_dir, model) -> str` and `exec_runner(instruction_text, target_dir, model) -> str` are injectable async callables (default: real `claude` subprocess) so unit tests never call the CLI. `execute` also collects changed files + commit shas via `git` and commits leftover changes on the current branch.

- [ ] **Step 1: Write the failing test** (unit — injected runners, no CLI)

```python
# tests/infrastructure/test_claude_code_coder.py
from ari.infrastructure.coder.claude_code_coder import ClaudeCodeCoder
from ari.domain.coding.entities import CodingInstruction, CodingPlan


async def test_plan_uses_runner_and_returns_summary():
    async def plan_runner(instr, target, model):
        assert model == "claude-sonnet-4-6"
        return '{"result": "1. add endpoint\\n2. test it"}'
    coder = ClaudeCodeCoder(plan_runner=plan_runner)
    plan = await coder.plan(CodingInstruction("u1", "add healthcheck"), "/repo")
    assert "add endpoint" in plan.summary
    assert plan.target_dir == "/repo"


async def test_execute_reports_error_on_failed_runner():
    async def exec_runner(instr, target, model):
        return '{"result": "nope", "is_error": true}'
    coder = ClaudeCodeCoder(exec_runner=exec_runner)
    plan = CodingPlan("s", "/repo", "add healthcheck")
    res = await coder.execute(plan, "ari/tg-1")
    assert res.ok is False and res.branch == "ari/tg-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_claude_code_coder.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/coder/claude_code_coder.py
import asyncio
import json
import logging

from ari.domain.coding.entities import CodingInstruction, CodingPlan, CodingResult

log = logging.getLogger("ari.claude_code_coder")


class ClaudeCodeCoder:
    def __init__(self, model: str = "claude-sonnet-4-6", claude_bin: str = "claude",
                 timeout: int = 900, plan_runner=None, exec_runner=None):
        self._model = model
        self._bin = claude_bin
        self._timeout = timeout
        self._plan_runner = plan_runner or self._default_plan_runner
        self._exec_runner = exec_runner or self._default_exec_runner

    async def plan(self, instruction: CodingInstruction, target_dir: str) -> CodingPlan:
        raw = await self._plan_runner(instruction.text, target_dir, self._model)
        summary = self._parse_result(raw) or "(sin plan)"
        return CodingPlan(summary=summary, target_dir=target_dir,
                          instruction_text=instruction.text)

    async def execute(self, plan: CodingPlan, branch: str) -> CodingResult:
        try:
            raw = await self._exec_runner(plan.instruction_text, plan.target_dir, self._model)
            data = json.loads(raw)
            if data.get("is_error"):
                return CodingResult(ok=False, branch=branch,
                                    detail=str(data.get("result", ""))[:500])
        except Exception as exc:  # runner/subprocess failure
            return CodingResult(ok=False, branch=branch, detail=str(exc)[:500])
        changed, commits = await self._collect_git(plan.target_dir)
        return CodingResult(ok=True, branch=branch, changed_files=changed, commits=commits)

    @staticmethod
    def _parse_result(raw: str) -> str:
        try:
            return (json.loads(raw).get("result") or "").strip()
        except Exception:
            return (raw or "").strip()

    async def _default_plan_runner(self, text, target_dir, model) -> str:
        return await self._run([
            self._bin, "-p", f"{text}\n\nProduce a concise step plan only; do NOT modify files.",
            "--model", model, "--permission-mode", "plan", "--output-format", "json",
        ], cwd=target_dir)

    async def _default_exec_runner(self, text, target_dir, model) -> str:
        return await self._run([
            self._bin, "-p", text, "--model", model,
            "--allowed-tools", "Read", "Edit", "Write", "Bash",
            "--permission-mode", "acceptEdits", "--output-format", "json",
        ], cwd=target_dir)

    async def _run(self, argv: list[str], cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude timed out after {self._timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"claude failed ({proc.returncode}): {err.decode(errors='replace')[:400]}")
        return out.decode(errors="replace")

    async def _collect_git(self, target_dir: str) -> tuple[list[str], list[str]]:
        # Commit any leftover changes, then report files + commits on this branch.
        async def git(*args):
            p = await asyncio.create_subprocess_exec(
                "git", "-C", target_dir, *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            o, _ = await p.communicate()
            return o.decode(errors="replace").strip()
        status = await git("status", "--porcelain")
        if status:
            await git("add", "-A")
            await git("commit", "-m", "chore: apply Ari coding task")
        files = [l[3:] for l in status.splitlines()] if status else []
        log_out = await git("log", "--oneline", "-5", "--format=%h")
        commits = [c for c in log_out.splitlines() if c]
        return files, commits
```

> Note on flags: `--permission-mode plan` and `acceptEdits` are verified against
> the installed `claude` CLI (2.1.x). If a flag differs, adjust in the default
> runners only — the unit tests use injected runners and are unaffected.

- [ ] **Step 4: Run test to verify it passes** (unit)

Run: `python3 -m pytest tests/infrastructure/test_claude_code_coder.py -v`
Expected: PASS

- [ ] **Step 5 (preferred): slow integration test**

Add a `@pytest.mark.slow` test that: `git init` a temp dir, commits a file, constructs `ClaudeCodeCoder()` (real runners), on a created branch runs `execute` with an instruction like "create a file hello.txt containing the word pong", and asserts `hello.txt` exists and a new commit is present. Skip-note in the report if the environment can't run the real CLI.

- [ ] **Step 6: Commit**

```bash
git add src/ari/infrastructure/coder/claude_code_coder.py tests/infrastructure/test_claude_code_coder.py
git commit -m "feat: add ClaudeCodeCoder adapter (claude CLI with tools)"
```

---

### Task 10: Wire the coding flow into main.py

**Files:**
- Modify: `src/ari/main.py`
- Test: `tests/application/test_coding_flow.py` (a small orchestration helper is unit-tested; the PTB wiring is manual-smoke)

**Interfaces:**
- Consumes: everything above + Phase 1 `HandleMessage`, `Settings`.
- Produces: a pure `async def route_message(text, user_id, deps) -> str | None` helper (in `src/ari/application/coding/flow.py`) that returns a reply string for the synchronous plan/confirm/chat decisions, or `None` when it scheduled background execution (so the caller knows to have already replied). `main.py` builds the coding deps in `_post_init` alongside the Phase 1 handler and calls this from `_on_message`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_coding_flow.py
from dataclasses import dataclass

from ari.application.coding.flow import route_message, CodingDeps
from ari.application.coding.authorizer import Authorizer
from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_coding import RequestCoding
from ari.infrastructure.coder.workspace import Workspace
from tests.coding_fakes import FakeCoder


def _deps(tmp_path, scheduled):
    store = PendingStore()
    ws = Workspace(str(tmp_path))
    (tmp_path / "repo").mkdir(exist_ok=True)
    rc = RequestCoding(FakeCoder(plan_summary="1. do it"), ws, store, str(tmp_path / "repo"))
    return CodingDeps(
        authorizer=Authorizer({"42"}), pending_store=store, request_coding=rc,
        confirm_coding=None, chat=None, scheduler=lambda coro: scheduled.append(coro))


async def test_non_owner_code_is_declined(tmp_path):
    deps = _deps(tmp_path, [])
    reply = await route_message("/code add X", "999", deps)
    assert "solo" in reply.lower() or "no autoriz" in reply.lower()


async def test_owner_code_returns_plan_and_stores(tmp_path):
    deps = _deps(tmp_path, [])
    reply = await route_message("/code add X", "42", deps)
    assert "do it" in reply and deps.pending_store.get("42") is not None


async def test_confirm_without_pending_falls_to_chat(tmp_path):
    called = {}
    deps = _deps(tmp_path, [])
    async def chat(text, user_id): called["hit"] = True; return "chat reply"
    deps.chat = chat
    reply = await route_message("dale", "42", deps)   # no pending
    assert reply == "chat reply" and called.get("hit")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_coding_flow.py -v`
Expected: FAIL (`ModuleNotFoundError` for `flow`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/coding/flow.py
from dataclasses import dataclass
from typing import Awaitable, Callable

from ari.application.coding.command_router import (
    parse_coding_command, is_affirmative, is_negative)


@dataclass
class CodingDeps:
    authorizer: object
    pending_store: object
    request_coding: object
    confirm_coding: object
    chat: Callable[[str, str], Awaitable[str]]
    scheduler: Callable


async def route_message(text: str, user_id: str, deps: CodingDeps) -> str | None:
    pending = deps.pending_store.get(user_id)
    if pending is not None:
        if is_affirmative(text):
            deps.scheduler(deps.confirm_coding(user_id))   # background; caller already told to reply
            return "Dale, arranco. Te aviso cuando termine."
        if is_negative(text):
            deps.pending_store.clear(user_id)
            return "Cancelado."
        # fallthrough: not a clear yes/no -> treat as chat
    cmd = parse_coding_command(text)
    if cmd is not None:
        if not deps.authorizer.is_owner(user_id):
            return "El modo código es solo para el dueño; no estás autorizado."
        if deps.pending_store.is_busy(user_id):
            return "Ya tengo un trabajo en curso para vos; esperá a que termine."
        instruction_text, target = cmd
        return await deps.request_coding(user_id, instruction_text, target)
    return await deps.chat(text, user_id)
```

Then modify `src/ari/main.py`:
- In `_post_init`, after building the Phase 1 `handler`, also build: `Authorizer(settings.owner_id_set)`, `PendingStore`, `Workspace(settings.allowed_root)`, `ClaudeCodeCoder(model=settings.coder_model, claude_bin=settings.claude_bin, timeout=settings.coding_timeout_seconds)`, `RequestCoding(coder, workspace, store, default_dir=<ari repo dir>)`, `ConfirmCoding(coder, workspace, store)`, and a `CodingDeps`. Store them in `app.bot_data`.
- `_on_message` becomes: build a `chat(text, user_id)` closure that calls the Phase 1 `handler` and returns its reply text; call `reply = await route_message(text, user_id, deps)`; if `reply` is not None, send it (split via `TelegramAdapter.split_text`). `ConfirmCoding` is scheduled with a `report` that sends to the same chat.

```python
# additions/shape inside main.py _on_message (illustrative)
async def _on_message(update, _context):
    inc = TelegramAdapter.to_incoming(update)
    if inc is None:
        return
    deps = app.bot_data["coding_deps"]
    # bind confirm_coding to this chat's reporter lazily:
    async def report(text):
        for part in TelegramAdapter.split_text(text):
            await update.effective_message.reply_text(part)
    deps.confirm_coding_for = lambda uid: _confirm(uid, report)   # see note
    reply = await route_message(inc.text, inc.user_id, deps)
    if reply is not None:
        for part in TelegramAdapter.split_text(reply):
            await update.effective_message.reply_text(part)
```

> Wiring note: `ConfirmCoding.__call__(user_id, report)` needs the per-message
> `report`. Adapt `CodingDeps.confirm_coding` to a zero-arg-per-call factory:
> in `flow.route_message`, call `deps.scheduler(deps.confirm_coding(user_id))`
> where `deps.confirm_coding` is a callable returning the coroutine
> `confirm.__call__(user_id, report)` with `report` captured for this chat. Keep
> the flow signature stable; only the closure creation lives in `main.py`.

- [ ] **Step 4: Run tests + smoke**

Run: `python3 -m pytest -m "not slow"` (all green) and `python3 -c "import ari.main; print('main import OK')"`.

- [ ] **Step 5: Manual smoke + commit**

Manual: with `ARI_OWNER_IDS` set to your Telegram id and `ARI_ALLOWED_ROOT` set, run `python3 -m ari.main`, send `/code dir:. add a CONTRIBUTING.md stub`, confirm the plan, reply `dale`, and confirm Ari reports a branch with a commit. Then `git log` the branch.

```bash
git add src/ari/application/coding/flow.py src/ari/main.py tests/application/test_coding_flow.py
git commit -m "feat: wire Telegram coding flow (route chat vs owner-gated coding)"
```

---

## Self-Review Notes

- **Spec coverage:** §3 layout → Tasks 1–10; §4 flow → Task 10 (`route_message`) + 6 + 7; §5 security → Task 4 (sandbox), Task 3/5/10 (owner-only), Task 7 (branch/no-push/confirm); §6 coder adapter → Task 9; §7 background/report → Task 7 + 10; §8 config → Task 8; §9 errors → Task 6/7/9; §10 testing → every task; §12 acceptance → Tasks 4,5,6,7,9,10 tests + manual smoke.
- **Review Focus mapping:** path escape → Task 4 (5 tests incl. symlink/`..`/absolute); non-owner `/code` → Task 10 `test_non_owner_code_is_declined` (+ Task 3 Authorizer); confirm w/o pending → Task 10 `test_confirm_without_pending_falls_to_chat`; run failure/timeout → Task 7 `test_confirm_reports_failure_without_crashing` + Task 9 timeout in `_run`; busy guard → Task 3 `test_pending_store_lifecycle_and_busy_guard` (+ enforced in Task 10 flow).
- **Type consistency:** `CoderPort.plan/execute`, `Workspace.resolve/create_branch`, `PendingStore` methods, `route_message`/`CodingDeps` used consistently across tasks.
- **No new dependencies** — reuses the `claude` CLI and stdlib.
- **Import boundary:** coding domain/app import only stdlib + domain; adapters (`workspace`, `claude_code_coder`) may use asyncio/subprocess/os. The Phase 1 domain-purity guard (walks `ari.domain`) also covers `ari.domain.coding`.
