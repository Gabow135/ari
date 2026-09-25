# Ari Proactivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ari keeps reminders and recurring tasks created in natural language, takes hourly initiative for the owner (heartbeat), and pushes owner-only system notices — all persistent across restarts.

**Architecture:** Chat replies may end with `<ari-action>{json}</ari-action>` blocks that the app extracts, validates (pure domain rules), stores in a SQLite `schedules` table and replaces with code-generated confirmations. An in-process asyncio `Scheduler` ticks every 30 s and runs three jobs: `RunDueItems` (reminders/tasks), `SystemNotices.tick` (quiet-hours queue + stale access requests) and `Heartbeat`. Hexagonal layout: `domain/schedule` (pure), `application/schedule` (use cases), `infrastructure/schedule` (SQLite).

**Tech Stack:** Python ≥3.11, asyncio, aiosqlite, python-telegram-bot 22, `croniter` (cron), `zoneinfo` + `tzdata` (IANA zones on Windows), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-09-25-ari-proactividad-design.md`

## Global Constraints

- Python `>=3.11`; must run on Windows, macOS and Linux.
- Only new dependencies: `croniter>=2.0`, `tzdata>=2024.1`.
- All user-facing text: español neutro, **tuteo** (`tests/test_tone.py` fails on voseo).
- Times are stored in UTC as ISO-8601 with seconds (`2026-09-26T14:00:00+00:00`); local zone = `ARI_TIMEZONE` (default `America/Guayaquil`).
- Quiet hours default `22-7` (local); heartbeat default every `60` minutes (`0` disables); `20` active items per user.
- Heartbeat and system notices are **owner-only**; reminders/tasks are for owner and approved users.
- Action blocks are honored **only** in replies to a user's own message; stripped and ignored in task runs and heartbeat replies.
- `domain/` has no I/O. Every Telegram send from background jobs is best-effort (never raises).
- Run tests with the project venv: `.venv/Scripts/python.exe -m pytest …` on Windows, `.venv/bin/python -m pytest …` on macOS/Linux. Below written as `python -m pytest`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Naive `at` without offset** (`"2026-09-26T09:00"`): must be read as local time (`ARI_TIMEZONE`), not rejected nor read as UTC → test in Task 2.
2. **Unclosed `<ari-action>` block** in a reply: must never reach the user's screen → test in Task 4.
3. **Reply that is only an action block**: the user must still get the confirmation, not an empty message → test in Task 4.
4. **A slow task (minutes of Claude) must not delay other users' reminders** due in the same tick → test in Task 5.
5. **Task output longer than 4096 chars**: must arrive split, not fail silently → test in Task 8.

---

### Task 1: Dependencies, settings, quiet hours, time formatting

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `src/ari/config/settings.py`
- Create: `src/ari/domain/schedule/__init__.py` (empty)
- Create: `src/ari/domain/schedule/quiet_hours.py`
- Create: `src/ari/domain/schedule/timefmt.py`
- Test: `tests/domain/test_quiet_hours.py`, `tests/domain/test_timefmt.py`, `tests/config/test_settings_proactivity.py`

**Interfaces:**
- Produces: `parse_window(spec: str) -> tuple[int, int] | None`; `is_quiet(local: datetime, window: tuple[int,int] | None) -> bool`; `fmt_short(dt: datetime, tz: tzinfo) -> str` (`"vie 26/09 09:00"`); `fmt_long(dt: datetime, tz: tzinfo) -> str` (`"viernes 25/09/2026 15:40"`); `Settings.timezone: str`, `Settings.quiet_hours: str`, `Settings.heartbeat_minutes: int`, `Settings.max_items_per_user: int`.

- [ ] **Step 1: Add dependencies and install**

In `pyproject.toml`, `dependencies` becomes:
```toml
dependencies = [
  "python-telegram-bot>=21",
  "aiosqlite>=0.20",
  "sqlite-vec>=0.1.3",
  "fastembed>=0.4",
  "pydantic-settings>=2.5",
  "croniter>=2.0",
  "tzdata>=2024.1",
]
```
Run: `python -m pip install -e ".[dev]"` — Expected: installs croniter and tzdata.

- [ ] **Step 2: Write the failing tests**

`tests/domain/test_quiet_hours.py`:
```python
from datetime import datetime

import pytest

from ari.domain.schedule.quiet_hours import is_quiet, parse_window


def test_parse_window():
    assert parse_window("22-7") == (22, 7)
    assert parse_window(" 9-17 ") == (9, 17)
    assert parse_window("") is None


@pytest.mark.parametrize("spec", ["25-7", "x-7", "22"])
def test_parse_window_rejects_garbage(spec):
    with pytest.raises(ValueError):
        parse_window(spec)


@pytest.mark.parametrize("hour,quiet", [(21, False), (22, True), (23, True), (0, True),
                                        (6, True), (7, False), (12, False)])
def test_window_crossing_midnight(hour, quiet):
    assert is_quiet(datetime(2026, 9, 25, hour, 30), (22, 7)) is quiet


def test_daytime_window_and_none():
    assert is_quiet(datetime(2026, 9, 25, 10), (9, 17))
    assert not is_quiet(datetime(2026, 9, 25, 17), (9, 17))
    assert not is_quiet(datetime(2026, 9, 25, 3), None)
    assert not is_quiet(datetime(2026, 9, 25, 3), (5, 5))
```

`tests/domain/test_timefmt.py`:
```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ari.domain.schedule.timefmt import fmt_long, fmt_short

TZ = ZoneInfo("America/Guayaquil")


def test_fmt_short_converts_utc_to_local():
    # 14:00 UTC = 09:00 in Guayaquil (UTC-5); 2026-09-26 is a Saturday
    assert fmt_short(datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc), TZ) == "sáb 26/09 09:00"


def test_fmt_long():
    assert fmt_long(datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc), TZ) == \
        "viernes 25/09/2026 15:40"
```

`tests/config/test_settings_proactivity.py`:
```python
from ari.config.settings import Settings


def test_proactivity_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    s = Settings(_env_file=None)
    assert s.timezone == "America/Guayaquil"
    assert s.quiet_hours == "22-7"
    assert s.heartbeat_minutes == 60
    assert s.max_items_per_user == 20
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/domain/test_quiet_hours.py tests/domain/test_timefmt.py tests/config/test_settings_proactivity.py -q`
Expected: FAIL (`ModuleNotFoundError: ari.domain.schedule`, missing settings attributes).

- [ ] **Step 4: Implement**

`src/ari/domain/schedule/__init__.py`: empty file.

`src/ari/domain/schedule/quiet_hours.py`:
```python
from datetime import datetime

QuietWindow = tuple[int, int]


def parse_window(spec: str) -> QuietWindow | None:
    """"22-7" -> (22, 7); "" -> None (no quiet hours)."""
    spec = (spec or "").strip()
    if not spec:
        return None
    start, sep, end = spec.partition("-")
    if not sep:
        raise ValueError(f"invalid quiet hours: {spec!r}")
    s, e = int(start), int(end)
    if not (0 <= s <= 23 and 0 <= e <= 23):
        raise ValueError(f"invalid quiet hours: {spec!r}")
    return (s, e)


def is_quiet(local: datetime, window: QuietWindow | None) -> bool:
    """``local`` must already be in the configured timezone."""
    if window is None:
        return False
    s, e = window
    if s == e:
        return False
    if s < e:
        return s <= local.hour < e
    return local.hour >= s or local.hour < e
```

`src/ari/domain/schedule/timefmt.py`:
```python
from datetime import datetime, tzinfo

_DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_DAYS_LONG = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fmt_short(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    return f"{_DAYS[local.weekday()]} {local:%d/%m %H:%M}"


def fmt_long(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    return f"{_DAYS_LONG[local.weekday()]} {local:%d/%m/%Y %H:%M}"
```

In `src/ari/config/settings.py`, after `claude_config_dir`:
```python
    # Proactivity
    timezone: str = "America/Guayaquil"
    quiet_hours: str = "22-7"  # local hours "start-end"; empty = none
    heartbeat_minutes: int = 60  # 0 disables the heartbeat
    max_items_per_user: int = 20
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_quiet_hours.py tests/domain/test_timefmt.py tests/config/test_settings_proactivity.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ari/config/settings.py src/ari/domain/schedule tests/domain/test_quiet_hours.py tests/domain/test_timefmt.py tests/config/test_settings_proactivity.py
git commit -m "feat: proactivity settings, quiet hours and local time formatting"
```

---

### Task 2: Schedule entities and action validation

**Files:**
- Create: `src/ari/domain/schedule/entities.py`
- Create: `src/ari/domain/schedule/actions.py`
- Test: `tests/domain/test_schedule_actions_domain.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `REMINDER="reminder"`, `TASK="task"`; statuses `ACTIVE`, `RUNNING`, `DONE`, `CANCELLED`, `PAUSED`.
  - `@dataclass(frozen=True) ScheduleItem(id: int, user_id: str, chat_id: str, kind: str, text: str, next_run_at: datetime, cron: str | None, status: str, failures: int = 0)` (`next_run_at` tz-aware UTC).
  - `class ActionError(ValueError)` — `str(e)` is a user-facing Spanish reason.
  - `@dataclass(frozen=True) CreateAction(kind: str, text: str, at: datetime | None, cron: str | None)`; `@dataclass(frozen=True) CancelAction(id: int)`.
  - `parse_action(data: object, now_utc: datetime, tz: tzinfo) -> CreateAction | CancelAction` (raises `ActionError`).
  - `next_cron_run(cron: str, after_utc: datetime, tz: tzinfo) -> datetime` (UTC).
  - `describe_cron(cron: str) -> str` (`"cada lunes 08:00"`, `"cada día 08:00"`, fallback `"según cron …"`).

- [ ] **Step 1: Write the failing tests**

`tests/domain/test_schedule_actions_domain.py`:
```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.domain.schedule.actions import (
    ActionError,
    CancelAction,
    CreateAction,
    describe_cron,
    next_cron_run,
    parse_action,
)
from ari.domain.schedule.entities import REMINDER, TASK

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # 15:00 local, Friday


def test_one_shot_reminder_with_offset():
    act = parse_action({"type": "reminder", "at": "2026-09-26T09:00:00-05:00",
                        "text": "llamar a Juan"}, NOW, TZ)
    assert act == CreateAction(REMINDER, "llamar a Juan",
                               datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc), None)


def test_naive_at_is_local_time():
    act = parse_action({"type": "reminder", "at": "2026-09-26T09:00", "text": "x"}, NOW, TZ)
    assert act.at == datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)


def test_recurring_task():
    act = parse_action({"type": "task", "cron": "0 8 * * 1", "text": "resumen"}, NOW, TZ)
    assert act == CreateAction(TASK, "resumen", None, "0 8 * * 1")


def test_cancel():
    assert parse_action({"type": "cancel", "id": "12"}, NOW, TZ) == CancelAction(12)


@pytest.mark.parametrize("data,reason", [
    ({"type": "reminder", "at": "2026-09-25T10:00:00-05:00", "text": "x"}, "ya pasó"),
    ({"type": "reminder", "at": "2028-01-01T10:00:00-05:00", "text": "x"}, "más de un año"),
    ({"type": "reminder", "at": "mañana", "text": "x"}, "no es válida"),
    ({"type": "reminder", "text": "x"}, "fecha"),
    ({"type": "reminder", "at": "2026-09-26T09:00", "cron": "0 8 * * *", "text": "x"}, "no ambas"),
    ({"type": "task", "cron": "*/5 * * * *", "text": "x"}, "1 hora"),
    ({"type": "task", "cron": "0 8 * *", "text": "x"}, "no es válida"),
    ({"type": "reminder", "at": "2026-09-26T09:00", "text": ""}, "texto"),
    ({"type": "reminder", "at": "2026-09-26T09:00", "text": "x" * 501}, "largo"),
    ({"type": "explode"}, "desconocido"),
    ({"type": "cancel"}, "número"),
    ("not a dict", "formato"),
])
def test_invalid_actions_explain_why(data, reason):
    with pytest.raises(ActionError, match=reason):
        parse_action(data, NOW, TZ)


def test_next_cron_run_is_computed_in_local_time():
    # next Monday 08:00 local = 13:00 UTC
    assert next_cron_run("0 8 * * 1", NOW, TZ) == datetime(2026, 9, 28, 13, 0,
                                                           tzinfo=timezone.utc)


def test_next_cron_run_skips_missed_occurrences():
    long_after = NOW + timedelta(days=20)
    assert next_cron_run("0 8 * * *", long_after, TZ) > long_after


@pytest.mark.parametrize("cron,text", [
    ("0 8 * * *", "cada día 08:00"),
    ("30 7 * * 1", "cada lunes 07:30"),
    ("0 9 * * 1,3,5", "cada lunes, miércoles, viernes 09:00"),
    ("0 9 1 * *", "según cron 0 9 1 * *"),
])
def test_describe_cron(cron, text):
    assert describe_cron(cron) == text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/domain/test_schedule_actions_domain.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`src/ari/domain/schedule/entities.py`:
```python
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
```

`src/ari/domain/schedule/actions.py`:
```python
"""Validation of the <ari-action> blocks Ari emits (pure: no I/O)."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from croniter import croniter

from ari.domain.schedule.entities import REMINDER, TASK

MAX_TEXT = 500
MAX_AHEAD = timedelta(days=366)
MIN_INTERVAL = timedelta(hours=1)
_DOW = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]


class ActionError(ValueError):
    """Invalid action; ``str(e)`` is shown to the user."""


@dataclass(frozen=True)
class CreateAction:
    kind: str  # REMINDER | TASK
    text: str
    at: datetime | None  # UTC, one-shot
    cron: str | None  # recurring, local time


@dataclass(frozen=True)
class CancelAction:
    id: int


def next_cron_run(cron: str, after_utc: datetime, tz: tzinfo) -> datetime:
    nxt = croniter(cron, after_utc.astimezone(tz)).get_next(datetime)
    return nxt.astimezone(timezone.utc)


def _interval_ok(cron: str, now_utc: datetime, tz: tzinfo) -> bool:
    it = croniter(cron, now_utc.astimezone(tz))
    runs = [it.get_next(datetime) for _ in range(6)]
    return all(b - a >= MIN_INTERVAL for a, b in zip(runs, runs[1:]))


def parse_action(data: object, now_utc: datetime, tz: tzinfo) -> CreateAction | CancelAction:
    if not isinstance(data, dict):
        raise ActionError("formato inválido")
    kind = data.get("type")
    if kind == "cancel":
        try:
            return CancelAction(int(data["id"]))
        except (KeyError, TypeError, ValueError):
            raise ActionError("falta el número del recordatorio a cancelar") from None
    if kind not in (REMINDER, TASK):
        raise ActionError(f"tipo de acción desconocido: {kind!r}")
    text = str(data.get("text") or "").strip()
    if not text:
        raise ActionError("falta el texto")
    if len(text) > MAX_TEXT:
        raise ActionError("el texto es demasiado largo")
    at_raw, cron = data.get("at"), data.get("cron")
    if at_raw and cron:
        raise ActionError("indica una fecha («at») o una repetición («cron»), no ambas")
    if not at_raw and not cron:
        raise ActionError("falta la fecha («at») o la repetición («cron»)")
    if cron:
        cron = str(cron).strip()
        if len(cron.split()) != 5 or not croniter.is_valid(cron):
            raise ActionError(f"la repetición «{cron}» no es válida")
        if not _interval_ok(cron, now_utc, tz):
            raise ActionError("la repetición mínima es cada 1 hora")
        return CreateAction(kind, text, None, cron)
    try:
        at = datetime.fromisoformat(str(at_raw))
    except ValueError:
        raise ActionError(f"la fecha «{at_raw}» no es válida") from None
    if at.tzinfo is None:
        at = at.replace(tzinfo=tz)  # no offset given: it's local time
    at = at.astimezone(timezone.utc)
    if at <= now_utc:
        raise ActionError("la fecha ya pasó")
    if at - now_utc > MAX_AHEAD:
        raise ActionError("la fecha está a más de un año")
    return CreateAction(kind, text, at, None)


def describe_cron(cron: str) -> str:
    parts = cron.split()
    if len(parts) == 5:
        minute, hour, dom, month, dow = parts
        if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*":
            at = f"{int(hour):02d}:{int(minute):02d}"
            if dow == "*":
                return f"cada día {at}"
            days = dow.split(",")
            if all(d.isdigit() and 0 <= int(d) <= 7 for d in days):
                return f"cada {', '.join(_DOW[int(d) % 7] for d in days)} {at}"
    return f"según cron {cron}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/domain/test_schedule_actions_domain.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/schedule/entities.py src/ari/domain/schedule/actions.py tests/domain/test_schedule_actions_domain.py
git commit -m "feat: schedule entities and <ari-action> validation rules"
```

---

### Task 3: SQLite schedule store and pending-access query

**Files:**
- Modify: `src/ari/infrastructure/persistence/db.py` (schema)
- Create: `src/ari/infrastructure/schedule/__init__.py` (empty)
- Create: `src/ari/infrastructure/schedule/sqlite_schedule_store.py`
- Modify: `src/ari/infrastructure/access/sqlite_access_store.py` (add `pending_since`)
- Modify: `src/ari/domain/access/access_port.py` (declare `pending_since`)
- Test: `tests/infrastructure/test_sqlite_schedule_store.py`, `tests/infrastructure/test_sqlite_access_store.py` (append)

**Interfaces:**
- Consumes: `ScheduleItem`, status constants (Task 2).
- Produces — `SqliteScheduleStore(conn)` with:
  - `add(user_id, chat_id, kind, text, next_run_at: datetime, cron: str | None) -> int`
  - `get(item_id: int) -> ScheduleItem | None`
  - `list_for_user(user_id) -> list[ScheduleItem]` (active, running, paused; by `next_run_at`)
  - `count_active(user_id) -> int` (active, running, paused)
  - `set_status(item_id, status) -> None`
  - `claim_due(now: datetime) -> list[ScheduleItem]` (flips due `active` → `running`, returns them with status `running`)
  - `reschedule(item_id, next_run_at: datetime, now: datetime) -> None` (→ active, failures 0, last_run_at now)
  - `finish(item_id, now: datetime) -> None` (→ done)
  - `record_failure(item_id, retry_at: datetime) -> int` (→ active, next_run_at=retry_at, failures+1; returns new count)
  - `reset_running() -> int`
  - `cancel_user(user_id) -> int`
  - `upcoming(user_id, until: datetime) -> list[ScheduleItem]` (active, `next_run_at <= until`)
  - `kv_get(key) -> str | None`, `kv_set(key, value: str)`, `kv_delete(key)`
- `SqliteAccessStore.pending_since() -> list[tuple[AccessRecord, datetime]]`.

- [ ] **Step 1: Write the failing tests**

`tests/infrastructure/test_sqlite_schedule_store.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest

from ari.domain.schedule.entities import ACTIVE, CANCELLED, DONE, PAUSED, REMINDER, RUNNING, TASK
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


async def test_add_get_roundtrip(store):
    item_id = await store.add("u1", "c1", REMINDER, "llamar", T0, None)
    item = await store.get(item_id)
    assert (item.user_id, item.chat_id, item.kind, item.text, item.next_run_at,
            item.cron, item.status, item.failures) == (
        "u1", "c1", REMINDER, "llamar", T0, None, ACTIVE, 0)
    assert await store.get(999) is None


async def test_claim_due_only_returns_due_active_once(store):
    due = await store.add("u1", "c1", REMINDER, "a", T0, None)
    await store.add("u1", "c1", REMINDER, "later", T0 + timedelta(hours=1), None)
    claimed = await store.claim_due(T0)
    assert [i.id for i in claimed] == [due] and claimed[0].status == RUNNING
    assert await store.claim_due(T0) == []  # already running: never twice
    assert await store.reset_running() == 1
    assert [i.id for i in await store.claim_due(T0)] == [due]


async def test_reschedule_finish_and_failures(store):
    a = await store.add("u1", "c1", TASK, "t", T0, "0 8 * * 1")
    await store.claim_due(T0)
    assert await store.record_failure(a, T0 + timedelta(minutes=5)) == 1
    assert await store.record_failure(a, T0 + timedelta(minutes=10)) == 2
    item = await store.get(a)
    assert item.status == ACTIVE and item.next_run_at == T0 + timedelta(minutes=10)
    await store.reschedule(a, T0 + timedelta(days=3), T0)
    item = await store.get(a)
    assert item.failures == 0 and item.next_run_at == T0 + timedelta(days=3)
    await store.finish(a, T0)
    assert (await store.get(a)).status == DONE


async def test_user_listing_count_cancel_and_upcoming(store):
    a = await store.add("u1", "c1", REMINDER, "a", T0 + timedelta(hours=2), None)
    b = await store.add("u1", "c1", REMINDER, "b", T0 + timedelta(hours=30), None)
    await store.add("u2", "c2", REMINDER, "other", T0, None)
    await store.set_status(b, PAUSED)
    assert [i.id for i in await store.list_for_user("u1")] == [a, b]
    assert await store.count_active("u1") == 2
    assert [i.id for i in await store.upcoming("u1", T0 + timedelta(hours=24))] == [a]
    assert await store.cancel_user("u1") == 2
    assert (await store.get(a)).status == CANCELLED
    assert await store.count_active("u1") == 0


async def test_kv(store):
    assert await store.kv_get("k") is None
    await store.kv_set("k", "1")
    await store.kv_set("k", "2")
    assert await store.kv_get("k") == "2"
    await store.kv_delete("k")
    assert await store.kv_get("k") is None
```

Append to `tests/infrastructure/test_sqlite_access_store.py`:
```python
async def test_pending_since_lists_only_pending_with_creation_time(store):
    await store.create_pending("u1", "a", "AAAA2222")
    await store.create_pending("u2", "b", "BBBB3333")
    await store.set_status("u2", APPROVED)
    rows = await store.pending_since()
    assert [(r.user_id, type(ts).__name__) for r, ts in rows] == [("u1", "datetime")]
    assert rows[0][1].tzinfo is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/infrastructure/test_sqlite_schedule_store.py tests/infrastructure/test_sqlite_access_store.py -q`
Expected: FAIL (`ModuleNotFoundError`, `AttributeError: pending_since`).

- [ ] **Step 3: Implement**

In `src/ari/infrastructure/persistence/db.py`, append to `_SCHEMA` (before the closing `"""`):
```sql

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  kind TEXT NOT NULL, text TEXT NOT NULL, next_run_at TEXT NOT NULL, cron TEXT,
  status TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, last_run_at TEXT);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id, status);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

`src/ari/infrastructure/schedule/__init__.py`: empty.

`src/ari/infrastructure/schedule/sqlite_schedule_store.py`:
```python
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import aiosqlite

from ari.domain.schedule.entities import (
    ACTIVE, CANCELLED, DONE, PAUSED, RUNNING, ScheduleItem)

_COLS = "id, user_id, chat_id, kind, text, next_run_at, cron, status, failures"
_OPEN = (ACTIVE, RUNNING, PAUSED)


def _iso(dt: datetime) -> str:
    # Fixed format keeps lexicographic order == chronological order in SQL.
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _item(r) -> ScheduleItem:
    return ScheduleItem(r["id"], r["user_id"], r["chat_id"], r["kind"], r["text"],
                        datetime.fromisoformat(r["next_run_at"]), r["cron"],
                        r["status"], r["failures"])


class SqliteScheduleStore:
    """Schedules + a small key/value table for engine state (see persistence/db.py)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def _write(self, sql: str, params: tuple) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur

    async def add(self, user_id, chat_id, kind, text, next_run_at, cron) -> int:
        cur = await self._write(
            "INSERT INTO schedules (user_id, chat_id, kind, text, next_run_at, cron, status, "
            "failures, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (user_id, chat_id, kind, text, _iso(next_run_at), cron, ACTIVE,
             _iso(datetime.now(timezone.utc))))
        return cur.lastrowid

    async def get(self, item_id: int) -> ScheduleItem | None:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM schedules WHERE id = ?", (item_id,))
        return _item(rows[0]) if rows else None

    async def list_for_user(self, user_id: str) -> list[ScheduleItem]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM schedules WHERE user_id = ? AND status IN (?, ?, ?) "
            "ORDER BY next_run_at", (user_id, *_OPEN))
        return [_item(r) for r in rows]

    async def count_active(self, user_id: str) -> int:
        rows = await self._conn.execute_fetchall(
            "SELECT COUNT(*) AS n FROM schedules WHERE user_id = ? AND status IN (?, ?, ?)",
            (user_id, *_OPEN))
        return rows[0]["n"]

    async def set_status(self, item_id: int, status: str) -> None:
        await self._write("UPDATE schedules SET status = ? WHERE id = ?", (status, item_id))

    async def claim_due(self, now: datetime) -> list[ScheduleItem]:
        async with self._lock:
            rows = await self._conn.execute_fetchall(
                f"SELECT {_COLS} FROM schedules WHERE status = ? AND next_run_at <= ? "
                "ORDER BY next_run_at", (ACTIVE, _iso(now)))
            if rows:
                ids = [r["id"] for r in rows]
                marks = ",".join("?" * len(ids))
                await self._conn.execute(
                    f"UPDATE schedules SET status = ? WHERE id IN ({marks})", (RUNNING, *ids))
                await self._conn.commit()
        return [replace(_item(r), status=RUNNING) for r in rows]

    async def reschedule(self, item_id: int, next_run_at: datetime, now: datetime) -> None:
        await self._write(
            "UPDATE schedules SET status = ?, next_run_at = ?, failures = 0, last_run_at = ? "
            "WHERE id = ?", (ACTIVE, _iso(next_run_at), _iso(now), item_id))

    async def finish(self, item_id: int, now: datetime) -> None:
        await self._write("UPDATE schedules SET status = ?, last_run_at = ? WHERE id = ?",
                          (DONE, _iso(now), item_id))

    async def record_failure(self, item_id: int, retry_at: datetime) -> int:
        await self._write(
            "UPDATE schedules SET status = ?, next_run_at = ?, failures = failures + 1 "
            "WHERE id = ?", (ACTIVE, _iso(retry_at), item_id))
        return (await self.get(item_id)).failures

    async def reset_running(self) -> int:
        cur = await self._write("UPDATE schedules SET status = ? WHERE status = ?",
                                (ACTIVE, RUNNING))
        return cur.rowcount

    async def cancel_user(self, user_id: str) -> int:
        cur = await self._write(
            "UPDATE schedules SET status = ? WHERE user_id = ? AND status IN (?, ?, ?)",
            (CANCELLED, user_id, *_OPEN))
        return cur.rowcount

    async def upcoming(self, user_id: str, until: datetime) -> list[ScheduleItem]:
        rows = await self._conn.execute_fetchall(
            f"SELECT {_COLS} FROM schedules WHERE user_id = ? AND status = ? "
            "AND next_run_at <= ? ORDER BY next_run_at", (user_id, ACTIVE, _iso(until)))
        return [_item(r) for r in rows]

    async def kv_get(self, key: str) -> str | None:
        rows = await self._conn.execute_fetchall("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    async def kv_set(self, key: str, value: str) -> None:
        await self._write("INSERT INTO kv (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    async def kv_delete(self, key: str) -> None:
        await self._write("DELETE FROM kv WHERE key = ?", (key,))
```

In `src/ari/infrastructure/access/sqlite_access_store.py` add (and `from ari.domain.access.entities import AccessRecord, PENDING` already partially imported — ensure `PENDING` is imported):
```python
    async def pending_since(self) -> list[tuple[AccessRecord, datetime]]:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, username, code, status, created_at FROM access "
            "WHERE status = ? ORDER BY created_at", (PENDING,))
        return [(_record(r), datetime.fromisoformat(r["created_at"])) for r in rows]
```
Imports at top of that file become:
```python
from ari.domain.access.entities import PENDING, AccessRecord
```

In `src/ari/domain/access/access_port.py`, add to the Protocol (and `from datetime import datetime`):
```python
    async def pending_since(self) -> list[tuple[AccessRecord, datetime]]: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/infrastructure/test_sqlite_schedule_store.py tests/infrastructure/test_sqlite_access_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/persistence/db.py src/ari/infrastructure/schedule src/ari/infrastructure/access/sqlite_access_store.py src/ari/domain/access/access_port.py tests/infrastructure/test_sqlite_schedule_store.py tests/infrastructure/test_sqlite_access_store.py
git commit -m "feat: SQLite schedule store with claim/retry semantics and kv state"
```

---

### Task 4: Action blocks in chat — parse, apply, confirm, list

**Files:**
- Create: `src/ari/application/schedule/__init__.py` (empty)
- Create: `src/ari/application/schedule/schedule_actions.py`
- Modify: `src/ari/domain/agent/agent_service.py` (`extra` parameter)
- Modify: `src/ari/application/handle_message.py` (`actions` dep, `allow_actions`)
- Test: `tests/application/test_schedule_actions.py`, `tests/domain/test_agent_service.py` (append), `tests/application/test_handle_message.py` (append)

**Interfaces:**
- Consumes: `parse_action`, `ActionError`, `CreateAction`, `CancelAction`, `next_cron_run`, `describe_cron` (Task 2); `fmt_short`, `fmt_long` (Task 1); `SqliteScheduleStore` methods `add/get/list_for_user/count_active/set_status` (Task 3).
- Produces:
  - `extract_actions(reply: str) -> tuple[str, list[str]]` (clean text, raw JSON strings).
  - `ScheduleActions(store, tz, max_items: int, clock: Callable[[], datetime])` with `async context(user_id) -> str`, `async apply(user_id, chat_id, reply, allow: bool = True) -> str`, `async list_text(user_id) -> str`.
  - `AgentService.build_prompt(..., extra: str | None = None)`.
  - `HandleMessage(..., actions=None)`; `await handler(incoming, allow_actions=True)`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_schedule_actions.py`:
```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.schedule_actions import ScheduleActions, extract_actions
from ari.domain.schedule.entities import CANCELLED, REMINDER
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # vie 15:00 local
REMIND = ('<ari-action>{"type":"reminder","at":"2026-09-26T09:00:00-05:00",'
          '"text":"llamar a Juan"}</ari-action>')


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


@pytest.fixture
def actions(store):
    return ScheduleActions(store, TZ, max_items=2, clock=lambda: NOW)


def test_extract_strips_blocks_including_unclosed():
    clean, blocks = extract_actions(f"Listo. {REMIND}\n<ari-action>{{\"type\":")
    assert clean == "Listo." and len(blocks) == 1
    assert "<ari-action>" not in clean


async def test_apply_stores_and_appends_code_confirmation(actions, store):
    out = await actions.apply("u1", "c1", f"Claro, te lo recuerdo.\n{REMIND}")
    assert out.startswith("Claro, te lo recuerdo.")
    assert "✅ Recordatorio #1: llamar a Juan — sáb 26/09 09:00" in out
    item = await store.get(1)
    assert (item.kind, item.chat_id, item.next_run_at) == (
        REMINDER, "c1", datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc))


async def test_action_only_reply_still_confirms(actions):
    out = await actions.apply("u1", "c1", REMIND)
    assert out.startswith("✅ Recordatorio #1")


async def test_recurring_confirmation(actions):
    out = await actions.apply("u1", "c1", '<ari-action>{"type":"task","cron":"0 8 * * 1",'
                                          '"text":"resumen"}</ari-action>')
    assert "🔁 Tarea #1 (cada lunes 08:00): resumen — próxima: lun 28/09 08:00" in out


async def test_invalid_block_stores_nothing_and_says_why(actions, store):
    out = await actions.apply("u1", "c1", 'Ok <ari-action>{"type":"reminder",'
                                          '"at":"2020-01-01T00:00","text":"x"}</ari-action>')
    assert "⚠️ No pude agendarlo: la fecha ya pasó" in out
    assert await store.count_active("u1") == 0


async def test_malformed_json_is_reported(actions):
    out = await actions.apply("u1", "c1", "Ok <ari-action>{not json}</ari-action>")
    assert "⚠️ No pude agendarlo" in out


async def test_blocks_ignored_when_not_allowed(actions, store):
    out = await actions.apply("u1", "c1", f"Resultado {REMIND}", allow=False)
    assert out == "Resultado"
    assert await store.count_active("u1") == 0


async def test_cancel_own_but_not_foreign(actions, store):
    await actions.apply("u1", "c1", REMIND)
    foreign = await actions.apply("u2", "c2", '<ari-action>{"type":"cancel","id":1}</ari-action>')
    assert "No encontré el #1" in foreign
    own = await actions.apply("u1", "c1", '<ari-action>{"type":"cancel","id":1}</ari-action>')
    assert "🗑️ Cancelado #1" in own
    assert (await store.get(1)).status == CANCELLED


async def test_per_user_cap(actions):
    await actions.apply("u1", "c1", REMIND)
    await actions.apply("u1", "c1", REMIND)
    out = await actions.apply("u1", "c1", REMIND)
    assert "ya tienes 2" in out


async def test_context_has_time_items_and_format(actions):
    await actions.apply("u1", "c1", REMIND)
    ctx = await actions.context("u1")
    assert "viernes 25/09/2026 15:00 (America/Guayaquil)" in ctx
    assert "#1 · sáb 26/09 09:00 · llamar a Juan" in ctx
    assert "<ari-action>" in ctx


async def test_list_text(actions, store):
    assert "No tienes" in await actions.list_text("u1")
    await actions.apply("u1", "c1", REMIND)
    assert "#1 · sáb 26/09 09:00 · llamar a Juan" in await actions.list_text("u1")
```

Append to `tests/domain/test_agent_service.py`:
```python
def test_extra_section_is_appended():
    prompt = AgentService().build_prompt([], None, [], extra="## Fecha y hora actual\nhoy")
    assert "## Fecha y hora actual\nhoy" in prompt
```

Append to `tests/application/test_handle_message.py`:
```python
class _FakeActions:
    def __init__(self):
        self.applied = []

    async def context(self, user_id):
        return "## CONTEXTO-AGENDA"

    async def apply(self, user_id, chat_id, reply, allow=True):
        self.applied.append((user_id, chat_id, allow))
        return reply.replace("<blk>", "") + " ✅"


async def test_actions_context_in_prompt_and_reply_processed():
    llm = FakeLLM(reply="hecho<blk>")
    acts = _FakeActions()
    mem = FakeMemory()
    handler = HandleMessage(memory=mem, llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), actions=acts)
    out = await handler(IncomingMessage("u1", "c1", "recuérdame algo"))
    assert "## CONTEXTO-AGENDA" in llm.calls[0][0]
    assert out.text == "hecho ✅"
    assert (await mem.recent_messages("u1", 10))[-1].content == "hecho ✅"
    await handler(IncomingMessage("u1", "c1", "tarea"), allow_actions=False)
    assert acts.applied == [("u1", "c1", True), ("u1", "c1", False)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_schedule_actions.py tests/domain/test_agent_service.py tests/application/test_handle_message.py -q`
Expected: FAIL (`ModuleNotFoundError`, unexpected keyword `extra` / `actions`).

- [ ] **Step 3: Implement**

`src/ari/application/schedule/__init__.py`: empty.

`src/ari/application/schedule/schedule_actions.py`:
```python
import json
import logging
import re
from datetime import datetime, timezone

from ari.domain.schedule.actions import (
    ActionError, CancelAction, describe_cron, next_cron_run, parse_action)
from ari.domain.schedule.entities import ACTIVE, CANCELLED, PAUSED, REMINDER, ScheduleItem
from ari.domain.schedule.timefmt import fmt_long, fmt_short

log = logging.getLogger("ari.schedule")

_BLOCK = re.compile(r"<ari-action>(.*?)</ari-action>", re.S | re.I)
_DANGLING = re.compile(r"<ari-action>.*\Z", re.S | re.I)

_FORMAT = """## Cómo agendar
Si el usuario te pide recordarle algo, o hacer algo más tarde o de forma periódica,
agrega AL FINAL de tu respuesta un bloque por acción. El usuario no ve los bloques;
el sistema agrega la confirmación exacta, así que no inventes números ni horas.
<ari-action>{"type":"reminder","at":"2026-09-26T09:00:00-05:00","text":"llamar a Juan"}</ari-action>
<ari-action>{"type":"task","cron":"0 8 * * 1","text":"resúmeme mis pendientes"}</ari-action>
<ari-action>{"type":"cancel","id":12}</ari-action>
- "reminder": a esa hora se envía el texto tal cual. "task": a esa hora TÚ ejecutas
  la instrucción y envías el resultado.
- Usa "at" (fecha y hora local ISO) para una vez, o "cron" (5 campos, hora local)
  para repetir. Frecuencia mínima: cada 1 hora.
- Para cancelar usa el # de la lista de arriba."""


def extract_actions(reply: str) -> tuple[str, list[str]]:
    """Split a reply into (text the user sees, raw JSON of each action block)."""
    blocks = [b.strip() for b in _BLOCK.findall(reply)]
    clean = _DANGLING.sub("", _BLOCK.sub("", reply))  # an unclosed block never leaks
    return clean.strip(), blocks


class ScheduleActions:
    def __init__(self, store, tz, max_items: int,
                 clock=lambda: datetime.now(timezone.utc)):
        self._store, self._tz, self._max, self._clock = store, tz, max_items, clock

    def _when(self, item: ScheduleItem) -> str:
        if item.cron:
            return f"{describe_cron(item.cron)} (próxima {fmt_short(item.next_run_at, self._tz)})"
        return fmt_short(item.next_run_at, self._tz)

    def _line(self, item: ScheduleItem) -> str:
        paused = " · ⏸️ pausada" if item.status == PAUSED else ""
        return f"#{item.id} · {self._when(item)} · {item.text}{paused}"

    async def context(self, user_id: str) -> str:
        items = await self._store.list_for_user(user_id)
        listing = "\n".join(f"- {self._line(i)}" for i in items) or "Ninguno."
        return (f"## Fecha y hora actual\n{fmt_long(self._clock(), self._tz)} "
                f"({self._tz.key})\n\n"
                f"## Recordatorios y tareas del usuario\n{listing}\n\n{_FORMAT}")

    async def list_text(self, user_id: str) -> str:
        items = await self._store.list_for_user(user_id)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "Tus recordatorios y tareas:\n" + "\n".join(self._line(i) for i in items)

    async def apply(self, user_id: str, chat_id: str, reply: str, allow: bool = True) -> str:
        clean, blocks = extract_actions(reply)
        if not allow or not blocks:
            return clean
        notes = [await self._apply_one(user_id, chat_id, raw) for raw in blocks]
        return "\n\n".join(p for p in (clean, "\n".join(notes)) if p)

    async def _apply_one(self, user_id: str, chat_id: str, raw: str) -> str:
        now = self._clock()
        try:
            action = parse_action(json.loads(raw), now, self._tz)
        except (ValueError, ActionError) as exc:  # JSONDecodeError is a ValueError
            reason = str(exc) if isinstance(exc, ActionError) else "el formato no es válido"
            log.info("rejected action from %s: %s (%r)", user_id, reason, raw[:200])
            return f"⚠️ No pude agendarlo: {reason}."
        if isinstance(action, CancelAction):
            item = await self._store.get(action.id)
            if item is None or item.user_id != user_id or item.status not in (ACTIVE, PAUSED):
                return f"⚠️ No encontré el #{action.id} entre tus recordatorios."
            await self._store.set_status(item.id, CANCELLED)
            return f"🗑️ Cancelado #{item.id}: {item.text}"
        if await self._store.count_active(user_id) >= self._max:
            return (f"⚠️ No pude agendarlo: ya tienes {self._max} recordatorios o tareas "
                    "activos. Cancela alguno primero.")
        next_run = action.at or next_cron_run(action.cron, now, self._tz)
        item_id = await self._store.add(user_id, chat_id, action.kind, action.text,
                                        next_run, action.cron)
        label = "Recordatorio" if action.kind == REMINDER else "Tarea"
        when = fmt_short(next_run, self._tz)
        if action.cron:
            return (f"🔁 {label} #{item_id} ({describe_cron(action.cron)}): "
                    f"{action.text} — próxima: {when}")
        return f"✅ {label} #{item_id}: {action.text} — {when}"
```

In `src/ari/domain/agent/agent_service.py`, change `build_prompt` signature and body:
```python
    def build_prompt(self, facts: list[Fact], summary: Summary | None,
                     recalls: list[Recall], soul: str | None = None,
                     is_owner: bool = False, extra: str | None = None) -> str:
        parts = [soul.strip() if soul and soul.strip() else self.SYSTEM_PREAMBLE,
                 _WITH_OWNER if is_owner else _WITH_USER,
                 render_capabilities(is_owner)]
        if extra:
            parts.append(extra)
```
(the rest of the method is unchanged).

In `src/ari/application/handle_message.py`:
- `__init__` signature gains `actions=None`; store `self._actions = actions  # ScheduleActions | None`.
- `async def __call__(self, incoming: IncomingMessage, allow_actions: bool = True) -> OutgoingMessage:`
- Replace the `system = …` / `reply = …` lines with:
```python
        extra = await self._actions.context(incoming.user_id) if self._actions else None
        system = self._agent.build_prompt(
            facts, summary, recalls, soul=self._soul(),
            is_owner=self._is_owner(incoming.user_id), extra=extra)
        reply = await self._llm.complete(system, history)
        if self._actions is not None:
            # Action blocks become stored items + confirmations; only honored for
            # the user's own messages (allow_actions=False for scheduled runs).
            reply = await self._actions.apply(incoming.user_id, incoming.chat_id, reply,
                                              allow=allow_actions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/application/test_schedule_actions.py tests/domain/test_agent_service.py tests/application/test_handle_message.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole fast suite**

Run: `python -m pytest -m "not slow" -q`
Expected: PASS (no regressions; `tests/test_tone.py` included).

- [ ] **Step 6: Commit**

```bash
git add src/ari/application/schedule src/ari/domain/agent/agent_service.py src/ari/application/handle_message.py tests/application/test_schedule_actions.py tests/domain/test_agent_service.py tests/application/test_handle_message.py
git commit -m "feat: natural-language reminders/tasks via <ari-action> blocks"
```

---

### Task 5: Firing due reminders and tasks

**Files:**
- Create: `src/ari/application/schedule/run_due_items.py`
- Test: `tests/application/test_run_due_items.py`

**Interfaces:**
- Consumes: `SqliteScheduleStore` (`claim_due`, `reschedule`, `finish`, `record_failure`, `set_status`, `get`) (Task 3); `next_cron_run` (Task 2); `REMINDER`, `TASK`, `PAUSED`, `ScheduleItem`.
- Produces: `RunDueItems(store, send, run_task, on_paused, tz, clock)`; `await due()` runs one pass; `await due.drain()` awaits in-flight tasks. `send(chat_id: str, text: str)` must never raise. `run_task(item: ScheduleItem) -> Awaitable[str]` returns the reply text. `on_paused(item: ScheduleItem, reason: str) -> Awaitable[None]`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_run_due_items.py`:
```python
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.run_due_items import RunDueItems
from ari.domain.schedule.entities import ACTIVE, DONE, PAUSED, REMINDER, TASK
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


def _runner(store, clock, run_task=None):
    sent, paused = [], []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    async def default_task(item):
        return f"resultado de {item.text}"

    async def on_paused(item, reason):
        paused.append((item.id, reason))

    due = RunDueItems(store, send, run_task or default_task, on_paused, TZ, clock)
    return due, sent, paused


async def test_on_time_reminder_is_sent_and_done(store):
    rid = await store.add("u1", "c1", REMINDER, "llamar", T0, None)
    due, sent, _ = _runner(store, Clock(T0 + timedelta(seconds=20)))
    await due()
    assert sent == [("c1", "⏰ Recordatorio: llamar")]
    assert (await store.get(rid)).status == DONE
    await due()
    assert len(sent) == 1  # never twice


async def test_late_and_very_late_reminders(store):
    await store.add("u1", "c1", REMINDER, "a", T0, None)
    await store.add("u1", "c1", REMINDER, "b", T0 - timedelta(hours=3), None)
    due, sent, _ = _runner(store, Clock(T0 + timedelta(minutes=10)))
    await due()
    texts = sorted(t for _, t in sent)
    assert texts == ["⏰ Recordatorio: a (con retraso)",
                     "⚠️ Mientras estuve apagada no pude recordarte: b"]


async def test_recurring_reminder_is_rescheduled_in_local_time(store):
    rid = await store.add("u1", "c1", REMINDER, "pastilla", T0, "0 8 * * *")
    due, sent, _ = _runner(store, Clock(T0))
    await due()
    item = await store.get(rid)
    assert item.status == ACTIVE
    assert item.next_run_at == datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)  # 08:00 local


async def test_task_runs_and_missed_occurrences_run_once(store):
    tid = await store.add("u1", "c1", TASK, "resumen", T0 - timedelta(days=15), "0 8 * * 1")
    due, sent, _ = _runner(store, Clock(T0))
    await due()
    await due.drain()
    assert sent == [("c1", f"🔁 Tarea #{tid}: resultado de resumen")]
    assert (await store.get(tid)).next_run_at > T0


async def test_task_failures_retry_then_pause(store):
    tid = await store.add("u1", "c1", TASK, "x", T0, None)

    async def boom(item):
        raise RuntimeError("claude caído")

    clock = Clock(T0)
    due, sent, paused = _runner(store, clock, run_task=boom)
    for _ in range(3):
        await due()
        await due.drain()
        clock.now += timedelta(minutes=6)
    assert (await store.get(tid)).status == PAUSED
    assert paused == [(tid, "claude caído")]
    assert sent == []


async def test_slow_task_does_not_delay_reminders(store):
    release = asyncio.Event()

    async def slow(item):
        await release.wait()
        return "tarde"

    await store.add("u1", "c1", TASK, "lenta", T0, None)
    await store.add("u2", "c2", REMINDER, "rápido", T0, None)
    due, sent, _ = _runner(store, Clock(T0), run_task=slow)
    await asyncio.wait_for(due(), timeout=1)
    assert ("c2", "⏰ Recordatorio: rápido") in sent
    release.set()
    await due.drain()
    assert any(t.startswith("🔁 Tarea") for _, t in sent)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_run_due_items.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`src/ari/application/schedule/run_due_items.py`:
```python
import asyncio
import logging
from datetime import timedelta

from ari.domain.schedule.actions import next_cron_run
from ari.domain.schedule.entities import PAUSED, TASK, ScheduleItem

log = logging.getLogger("ari.schedule")

LATE = timedelta(minutes=2)  # beyond normal tick jitter
VERY_LATE = timedelta(hours=1)
RETRY = timedelta(minutes=5)
MAX_FAILURES = 3


class RunDueItems:
    """One pass: fire every due reminder/task. Reminders are sent inline (fast);
    tasks run as background coroutines so a slow Claude turn never delays
    anyone else's reminder. ``send`` must never raise."""

    def __init__(self, store, send, run_task, on_paused, tz, clock):
        self._store, self._send, self._run = store, send, run_task
        self._on_paused, self._tz, self._clock = on_paused, tz, clock
        self._inflight: set[asyncio.Task] = set()

    async def __call__(self) -> None:
        now = self._clock()
        for item in await self._store.claim_due(now):
            if item.kind == TASK:
                task = asyncio.ensure_future(self._run_task(item, now))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
            else:
                await self._remind(item, now)

    async def drain(self) -> None:
        await asyncio.gather(*list(self._inflight), return_exceptions=True)

    async def _remind(self, item: ScheduleItem, now) -> None:
        late = now - item.next_run_at
        if late >= VERY_LATE:
            text = f"⚠️ Mientras estuve apagada no pude recordarte: {item.text}"
        else:
            text = f"⏰ Recordatorio: {item.text}" + (" (con retraso)" if late >= LATE else "")
        await self._send(item.chat_id, text)
        await self._advance(item, now)

    async def _run_task(self, item: ScheduleItem, now) -> None:
        try:
            reply = await self._run(item)
        except Exception as exc:  # noqa: BLE001 — isolate each item
            log.exception("scheduled task #%s failed", item.id)
            failures = await self._store.record_failure(item.id, now + RETRY)
            if failures >= MAX_FAILURES:
                await self._store.set_status(item.id, PAUSED)
                await self._on_paused(item, str(exc)[:200])
            return
        await self._send(item.chat_id, f"🔁 Tarea #{item.id}: {reply}")
        await self._advance(item, now)

    async def _advance(self, item: ScheduleItem, now) -> None:
        if item.cron:
            await self._store.reschedule(item.id, next_cron_run(item.cron, now, self._tz), now)
        else:
            await self._store.finish(item.id, now)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/application/test_run_due_items.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/schedule/run_due_items.py tests/application/test_run_due_items.py
git commit -m "feat: fire due reminders inline and tasks in background with retries"
```

---

### Task 6: System notices and Claude CLI health monitor

**Files:**
- Create: `src/ari/application/schedule/system_notices.py`
- Create: `src/ari/application/schedule/llm_health.py`
- Test: `tests/application/test_system_notices.py`, `tests/application/test_llm_health.py`

**Interfaces:**
- Consumes: `is_quiet` (Task 1); store `kv_get/kv_set/kv_delete` (Task 3); `SqliteAccessStore.pending_since()` (Task 3); `format_code` from `ari.application.access.gate`; `ScheduleItem` (Task 2).
- Produces:
  - `SystemNotices(kv, access, owners: set[str], send, tz, quiet, clock, stale_after=timedelta(hours=12))` with `async emit(text)`, `async tick()`, `async task_paused(item, reason)`, `async cli_failed(reason)`, `async cli_recovered()`.
  - `MonitoredLLM(llm, threshold=3)` implementing `LLMPort.complete`; attribute `listener` (object with `cli_failed(reason)` / `cli_recovered()`), default `None`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_system_notices.py`:
```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.system_notices import SystemNotices
from ari.domain.schedule.entities import TASK, ScheduleItem
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
DAY = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)  # 12:00 local
NIGHT = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)  # 23:00 local


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def stores():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn), SqliteAccessStore(conn)
    await conn.close()


def _notices(stores, clock):
    kv, access = stores
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    return SystemNotices(kv, access, {"42"}, send, TZ, (22, 7), clock), sent


async def test_emit_sends_to_owner_by_day(stores):
    n, sent = _notices(stores, Clock(DAY))
    await n.emit("hola")
    assert sent == [("42", "hola")]


async def test_quiet_hours_queue_then_flush_together(stores):
    clock = Clock(NIGHT)
    n, sent = _notices(stores, clock)
    await n.emit("uno")
    await n.emit("dos")
    await n.tick()
    assert sent == []
    clock.now = NIGHT + timedelta(hours=8)  # 07:00 local
    await n.tick()
    assert len(sent) == 1 and "• uno" in sent[0][1] and "• dos" in sent[0][1]
    await n.tick()
    assert len(sent) == 1


async def test_stale_access_request_notified_once(stores):
    kv, access = stores
    await access.create_pending("7", "juan", "K7QMX3PA")
    clock = Clock(datetime.now(timezone.utc) + timedelta(hours=1))
    n, sent = _notices(stores, clock)
    await n.tick()
    assert sent == []
    clock.now += timedelta(hours=12)
    await n.tick()
    await n.tick()
    assert len(sent) == 1
    assert "@juan" in sent[0][1] and "/aprobar K7QM-X3PA" in sent[0][1]


async def test_cli_down_and_up_once_each(stores):
    n, sent = _notices(stores, Clock(DAY))
    await n.cli_failed("Not logged in")
    await n.cli_failed("Not logged in")
    await n.cli_recovered()
    await n.cli_recovered()
    assert [t for _, t in sent] == [
        "⚠️ Estoy teniendo problemas con Claude: «Not logged in». Revisa el token o la conexión.",
        "✅ Claude volvió a responder."]


async def test_task_paused(stores):
    n, sent = _notices(stores, Clock(DAY))
    item = ScheduleItem(12, "u1", "c1", TASK, "resumen", DAY, None, "paused", 3)
    await n.task_paused(item, "timeout")
    assert sent == [("42", "⏸️ Pausé la tarea #12 (resumen) porque falló 3 veces: timeout")]
```

`tests/application/test_llm_health.py`:
```python
import pytest

from ari.application.schedule.llm_health import MonitoredLLM


class FlakyLLM:
    def __init__(self, script):
        self.script = list(script)

    async def complete(self, system, messages, max_tokens=1024):
        if self.script.pop(0):
            return "ok"
        raise RuntimeError("Not logged in")


class Listener:
    def __init__(self):
        self.events = []

    async def cli_failed(self, reason):
        self.events.append(("down", reason))

    async def cli_recovered(self):
        self.events.append(("up",))


async def _call(llm):
    try:
        return await llm.complete("s", [])
    except RuntimeError:
        return None


async def test_down_after_threshold_and_recovery_checked_on_success():
    llm = MonitoredLLM(FlakyLLM([False, False, False, False, True]), threshold=3)
    llm.listener = Listener()
    for _ in range(5):
        await _call(llm)
    downs = [e for e in llm.listener.events if e[0] == "down"]
    assert downs == [("down", "Not logged in")]
    assert llm.listener.events[-1] == ("up",)


async def test_errors_still_propagate_without_listener():
    llm = MonitoredLLM(FlakyLLM([False]))
    with pytest.raises(RuntimeError):
        await llm.complete("s", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_system_notices.py tests/application/test_llm_health.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`src/ari/application/schedule/system_notices.py`:
```python
import json
import logging
from datetime import timedelta

from ari.application.access.gate import format_code
from ari.domain.schedule.quiet_hours import is_quiet

log = logging.getLogger("ari.notices")

_QUEUE = "notices.queue"
_CLI_DOWN = "notices.cli_down"


class SystemNotices:
    """Owner-only operational notices, generated by code (no Claude), so they
    work even when Claude is down. Queued during quiet hours."""

    def __init__(self, kv, access, owners: set[str], send, tz, quiet, clock,
                 stale_after: timedelta = timedelta(hours=12)):
        self._kv, self._access, self._owners = kv, access, sorted(owners)
        self._send, self._tz, self._quiet = send, tz, quiet
        self._clock, self._stale_after = clock, stale_after

    def _is_quiet(self) -> bool:
        return is_quiet(self._clock().astimezone(self._tz), self._quiet)

    async def _to_owners(self, text: str) -> None:
        for owner in self._owners:
            await self._send(owner, text)

    async def emit(self, text: str) -> None:
        if self._is_quiet():
            queue = json.loads(await self._kv.kv_get(_QUEUE) or "[]")
            await self._kv.kv_set(_QUEUE, json.dumps([*queue, text], ensure_ascii=False))
        else:
            await self._to_owners(text)

    async def tick(self) -> None:
        await self._flush()
        await self._check_access()

    async def _flush(self) -> None:
        if self._is_quiet():
            return
        queue = json.loads(await self._kv.kv_get(_QUEUE) or "[]")
        if queue:
            await self._kv.kv_delete(_QUEUE)
            await self._to_owners("📬 Avisos mientras estaba en silencio:\n"
                                  + "\n".join(f"• {q}" for q in queue))

    async def _check_access(self) -> None:
        now = self._clock()
        for rec, since in await self._access.pending_since():
            key = f"notices.access:{rec.code}"
            if now - since < self._stale_after or await self._kv.kv_get(key):
                continue
            await self._kv.kv_set(key, "1")
            who = f"@{rec.username} (id {rec.user_id})" if rec.username else f"id {rec.user_id}"
            await self.emit(f"🔔 {who} sigue esperando acceso. "
                            f"Aprueba con: /aprobar {format_code(rec.code)}")

    async def task_paused(self, item, reason: str) -> None:
        await self.emit(f"⏸️ Pausé la tarea #{item.id} ({item.text[:60]}) "
                        f"porque falló 3 veces: {reason}")

    async def cli_failed(self, reason: str) -> None:
        if await self._kv.kv_get(_CLI_DOWN):
            return
        await self._kv.kv_set(_CLI_DOWN, "1")
        await self.emit(f"⚠️ Estoy teniendo problemas con Claude: «{reason[:200]}». "
                        "Revisa el token o la conexión.")

    async def cli_recovered(self) -> None:
        if not await self._kv.kv_get(_CLI_DOWN):
            return
        await self._kv.kv_delete(_CLI_DOWN)
        await self.emit("✅ Claude volvió a responder.")
```

`src/ari/application/schedule/llm_health.py`:
```python
import logging

log = logging.getLogger("ari.llm_health")


class MonitoredLLM:
    """LLMPort decorator: tells ``listener`` when the Claude CLI fails
    ``threshold`` times in a row, and checks for recovery on every success
    (the listener persists the "down" flag, so this also covers restarts)."""

    def __init__(self, llm, threshold: int = 3):
        self._llm, self._threshold, self._fails = llm, threshold, 0
        self.listener = None  # SystemNotices, bound once it exists

    async def complete(self, system, messages, max_tokens: int = 1024) -> str:
        try:
            reply = await self._llm.complete(system, messages, max_tokens=max_tokens)
        except Exception as exc:
            self._fails += 1
            if self._fails == self._threshold:
                await self._notify("cli_failed", str(exc))
            raise
        self._fails = 0
        await self._notify("cli_recovered")
        return reply

    async def _notify(self, method: str, *args) -> None:
        if self.listener is None:
            return
        try:
            await getattr(self.listener, method)(*args)
        except Exception:  # noqa: BLE001 — monitoring must never break a reply
            log.exception("llm health listener failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/application/test_system_notices.py tests/application/test_llm_health.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/schedule/system_notices.py src/ari/application/schedule/llm_health.py tests/application/test_system_notices.py tests/application/test_llm_health.py
git commit -m "feat: owner system notices with quiet-hours queue and CLI health monitor"
```

---

### Task 7: Heartbeat

**Files:**
- Create: `src/ari/application/schedule/heartbeat.py`
- Create: `soul/HEARTBEAT.md`
- Modify: `src/ari/infrastructure/soul/soul_loader.py` (`filename` parameter)
- Test: `tests/application/test_heartbeat.py`, `tests/infrastructure/test_soul_loader.py` (append)

**Interfaces:**
- Consumes: `is_quiet` (Task 1), `fmt_long`, `fmt_short` (Task 1); store `kv_*`, `upcoming` (Task 3); `extract_actions` (Task 4); `AgentService.build_prompt(..., extra=)` (Task 4); `MemoryPort` (`get_facts`, `get_summary`, `recent_messages`, `append_message`).
- Produces: `Heartbeat(*, llm, memory, store, agent, soul, checklist, owners, send, tz, quiet, interval_minutes, clock, daily_max=3)`; `await heartbeat()` runs one check. `soul`/`checklist` are `() -> str | None`. `SoulLoader(soul_dir, filename="SOUL.md")`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_heartbeat.py`:
```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.heartbeat import Heartbeat
from ari.domain.agent.agent_service import AgentService
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from tests.fakes import FakeLLM, FakeMemory

TZ = ZoneInfo("America/Guayaquil")
DAY = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)  # 10:00 local


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


def _hb(store, clock, reply="💬 Tienes la reunión a las 11, ¿preparo algo?", minutes=60):
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    llm, mem = FakeLLM(reply=reply), FakeMemory()
    hb = Heartbeat(llm=llm, memory=mem, store=store, agent=AgentService(),
                   soul=lambda: "Soy Ari", checklist=lambda: "- Revisa pendientes",
                   owners={"42"}, send=send, tz=TZ, quiet=(22, 7),
                   interval_minutes=minutes, clock=clock)
    return hb, sent, llm, mem


async def test_first_beat_waits_one_interval(store):
    clock = Clock(DAY)
    hb, sent, llm, _ = _hb(store, clock)
    await hb()
    assert llm.calls == []
    clock.now += timedelta(minutes=59)
    await hb()
    assert llm.calls == []
    clock.now += timedelta(minutes=2)
    await hb()
    assert len(llm.calls) == 1 and sent[0][0] == "42" and sent[0][1].startswith("💡 ")


async def test_prompt_contains_checklist_and_nada_rule(store):
    clock = Clock(DAY)
    hb, _, llm, _ = _hb(store, clock)
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    system = llm.calls[0][0]
    assert "Soy Ari" in system and "- Revisa pendientes" in system and "NADA" in system


async def test_nada_sends_nothing(store):
    clock = Clock(DAY)
    hb, sent, llm, mem = _hb(store, clock, reply="NADA.")
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    assert len(llm.calls) == 1 and sent == []


async def test_daily_cap_and_history(store):
    clock = Clock(DAY)
    hb, sent, _, mem = _hb(store, clock)
    await hb()
    for _ in range(5):
        clock.now += timedelta(hours=1)
        await hb()
    assert len(sent) == 3
    stored = await mem.recent_messages("42", 10)
    assert [m.role for m in stored] == ["assistant"] * 3


async def test_quiet_hours_and_disabled(store):
    clock = Clock(datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc))  # 23:00 local
    hb, _, llm, _ = _hb(store, clock)
    await hb()
    clock.now += timedelta(hours=2)
    await hb()
    assert llm.calls == []
    off, _, llm_off, _ = _hb(store, Clock(DAY), minutes=0)
    await off()
    assert llm_off.calls == []


async def test_action_blocks_in_heartbeat_are_ignored(store):
    clock = Clock(DAY)
    hb, sent, _, _ = _hb(store, clock, reply='Ojo <ari-action>{"type":"cancel","id":1}'
                                               '</ari-action>')
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    assert sent == [("42", "💡 Ojo")]
```

Append to `tests/infrastructure/test_soul_loader.py`:
```python
def test_other_filename(tmp_path):
    (tmp_path / "HEARTBEAT.md").write_text("- revisa", encoding="utf-8")
    assert SoulLoader(str(tmp_path), "HEARTBEAT.md")() == "- revisa"


def test_project_heartbeat_exists():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert SoulLoader(os.path.join(root, "soul"), "HEARTBEAT.md")()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_heartbeat.py tests/infrastructure/test_soul_loader.py -q`
Expected: FAIL (`ModuleNotFoundError`, `TypeError` on `SoulLoader` args).

- [ ] **Step 3: Implement**

In `src/ari/infrastructure/soul/soul_loader.py`, change the constructor:
```python
    def __init__(self, soul_dir: str, filename: str = "SOUL.md"):
        self._path = os.path.join(soul_dir, filename)
```
and update the class docstring to "Reads ``<soul_dir>/<filename>`` (default ``SOUL.md``)…".

`soul/HEARTBEAT.md`:
```markdown
# Latido de Ari

Cada hora revisas por tu cuenta si hay algo que valga la pena decirle a tu creador.
Escribe solo si aporta valor real; ante la duda, no escribas.

Vale la pena escribir si:
- Hay un recordatorio o tarea en las próximas horas que conviene preparar.
- En conversaciones recientes quedó algo pendiente, prometido o sin cerrar.
- Notas un patrón útil (algo que se repite y podrías automatizar o recordar).
- Hay una idea concreta para resolver un problema que el usuario mencionó.

No escribas para:
- Saludar, preguntar "¿en qué te ayudo?" o repetir algo que ya dijiste.
```

`src/ari/application/schedule/heartbeat.py`:
```python
import json
import logging
from datetime import datetime, timedelta

from ari.application.schedule.schedule_actions import extract_actions
from ari.domain.agent.message import Message
from ari.domain.schedule.quiet_hours import is_quiet
from ari.domain.schedule.timefmt import fmt_long, fmt_short

log = logging.getLogger("ari.heartbeat")

NADA = "NADA"
_LAST = "heartbeat.last_at"
_INSTRUCTION = ("(Latido automático: nadie te escribió.) Revisa según tus instrucciones "
                "de latido. Si no hay nada que realmente valga la pena, responde "
                "exactamente NADA.")


class Heartbeat:
    """Owner-only initiative: every ``interval_minutes`` (outside quiet hours) Ari
    decides whether it has something worth saying; ``NADA`` means stay silent."""

    def __init__(self, *, llm, memory, store, agent, soul, checklist, owners, send,
                 tz, quiet, interval_minutes: int, clock, daily_max: int = 3):
        self._llm, self._memory, self._store, self._agent = llm, memory, store, agent
        self._soul, self._checklist, self._owners = soul, checklist, sorted(owners)
        self._send, self._tz, self._quiet, self._clock = send, tz, quiet, clock
        self._interval = timedelta(minutes=interval_minutes)
        self._daily_max = daily_max

    async def __call__(self) -> None:
        if self._interval <= timedelta(0) or not self._owners:
            return
        now = self._clock()
        if is_quiet(now.astimezone(self._tz), self._quiet):
            return
        last = await self._store.kv_get(_LAST)
        if last is None:  # first ever run: start counting from now
            await self._store.kv_set(_LAST, now.isoformat())
            return
        if now - datetime.fromisoformat(last) < self._interval:
            return
        await self._store.kv_set(_LAST, now.isoformat())
        for owner in self._owners:
            try:
                await self._beat(owner, now)
            except Exception:  # noqa: BLE001 — retried next beat
                log.exception("heartbeat failed for %s", owner)

    async def _beat(self, owner: str, now: datetime) -> None:
        sent_key = f"heartbeat.sent:{owner}:{now.astimezone(self._tz).date().isoformat()}"
        sent = int(await self._store.kv_get(sent_key) or 0)
        if sent >= self._daily_max:
            return
        recent_key = f"heartbeat.recent:{owner}"
        recent = json.loads(await self._store.kv_get(recent_key) or "[]")
        system = self._agent.build_prompt(
            await self._memory.get_facts(owner), await self._memory.get_summary(owner), [],
            soul=self._soul(), is_owner=True, extra=await self._section(owner, now, recent))
        history = await self._memory.recent_messages(owner, 10)
        raw = await self._llm.complete(system, [*history, Message(owner, "user", _INSTRUCTION, now)])
        reply, _ignored = extract_actions(raw)  # heartbeat may suggest, never schedule
        if not reply or reply.strip().strip(".!¡ ").upper() == NADA:
            return
        text = f"💡 {reply}"
        await self._send(owner, text)
        await self._memory.append_message(Message(owner, "assistant", text, now))
        await self._store.kv_set(sent_key, str(sent + 1))
        await self._store.kv_set(recent_key, json.dumps([*recent, reply][-5:], ensure_ascii=False))

    async def _section(self, owner: str, now: datetime, recent: list[str]) -> str:
        items = await self._store.upcoming(owner, now + timedelta(hours=24))
        upcoming = "\n".join(f"- #{i.id} · {fmt_short(i.next_run_at, self._tz)} · {i.text}"
                             for i in items) or "Nada agendado."
        said = "\n".join(f"- {r}" for r in recent) or "Nada todavía."
        checklist = (self._checklist() or "").strip() or "Escribe solo si aporta valor real."
        return (f"## Latido (iniciativa propia)\nAhora: {fmt_long(now, self._tz)}. "
                f"Esto es un latido automático: nadie te escribió.\n\n"
                f"### Qué revisar\n{checklist}\n\n"
                f"### Próximas 24 h\n{upcoming}\n\n"
                f"### Lo último que dijiste por iniciativa propia (no lo repitas)\n{said}\n\n"
                f"Si no hay nada que realmente valga la pena, responde exactamente: {NADA}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/application/test_heartbeat.py tests/infrastructure/test_soul_loader.py tests/test_tone.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/schedule/heartbeat.py soul/HEARTBEAT.md src/ari/infrastructure/soul/soul_loader.py tests/application/test_heartbeat.py tests/infrastructure/test_soul_loader.py
git commit -m "feat: owner heartbeat with NADA contract, daily cap and quiet hours"
```

---

### Task 8: Scheduler loop, wiring, /recordatorios, revoke hook, capabilities, docs

**Files:**
- Create: `src/ari/application/schedule/scheduler.py`
- Modify: `src/ari/application/access/gate.py` (`on_revoke`)
- Modify: `src/ari/domain/agent/capabilities.py`
- Modify: `src/ari/main.py`
- Modify: `README.md`, `.env.example`
- Test: `tests/application/test_scheduler.py`, `tests/application/test_access_gate.py` (append), `tests/domain/test_capabilities.py`, `tests/infrastructure/test_bot_commands.py`, `tests/test_main_access.py` (append)

**Interfaces:**
- Consumes: everything above. `RunDueItems` (Task 5), `SystemNotices`, `MonitoredLLM` (Task 6), `Heartbeat` (Task 7), `ScheduleActions` (Task 4), `SqliteScheduleStore` (Task 3), `parse_window` (Task 1).
- Produces: `Scheduler(jobs: list[Callable[[], Awaitable[None]]], interval: float = 30.0)` with `start()`, `async stop()`, `async tick()`; `AccessGate(store, owner_ids, on_revoke=None)`; `/recordatorios` command; `build(settings, env, tz) -> Components`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_scheduler.py`:
```python
import asyncio

from ari.application.schedule.scheduler import Scheduler


async def test_tick_runs_every_job_and_isolates_failures():
    calls = []

    async def ok():
        calls.append("ok")

    async def boom():
        raise RuntimeError("x")

    await Scheduler([boom, ok]).tick()
    assert calls == ["ok"]


async def test_start_loops_until_stopped():
    calls = []

    async def job():
        calls.append(1)

    s = Scheduler([job], interval=0.01)
    s.start()
    await asyncio.sleep(0.05)
    await s.stop()
    n = len(calls)
    await asyncio.sleep(0.03)
    assert n >= 2 and len(calls) == n
```

Append to `tests/application/test_access_gate.py`:
```python
async def test_revoke_calls_on_revoke(store):
    revoked = []

    async def on_revoke(user_id):
        revoked.append(user_id)

    gate = AccessGate(store, owner_ids={OWNER}, on_revoke=on_revoke)
    code = _code_in((await gate.check("7", "juan")).reply)
    await gate.admin_command(f"/aprobar {code}", OWNER)
    await gate.admin_command("/revocar 7", OWNER)
    assert revoked == ["7"]
```

In `tests/domain/test_capabilities.py`, replace `test_menu_is_derived_from_registry` with:
```python
def test_menu_is_derived_from_registry():
    assert [c for c, _ in menu_commands(owner=False)] == ["start", "recordatorios"]
    owner = [c for c, _ in menu_commands(owner=True)]
    assert set(owner) == {"start", "recordatorios", "code", "aprobar", "revocar",
                          "accesos", "restart", "stop"}
    assert owner[0] == "start"


def test_proactivity_limitations_removed():
    text = " ".join(LIMITATIONS)
    assert "iniciativa propia" not in text and "recordatorios" not in text
```

In `tests/infrastructure/test_bot_commands.py`, in `test_default_menu_only_has_start_and_owners_get_full_menu` replace the two asserts about names with:
```python
    assert public == ["start", "recordatorios"] and isinstance(default_scope, BotCommandScopeDefault)
    assert isinstance(owner_scope, BotCommandScopeChat) and owner_scope.chat_id == 42
    assert {"start", "recordatorios", "code", "aprobar", "revocar", "accesos",
            "restart", "stop"} == set(owner)
```

Append to `tests/test_main_access.py` (uses the existing `_FakeApp`, `_update`, `_callback`, `wired` fixture):
```python
class _FakeActions:
    async def list_text(self, user_id):
        return f"lista de {user_id}"


async def test_recordatorios_requires_access_and_lists(wired):
    app, _ = wired
    app.bot_data["actions"] = _FakeActions()
    cb = _callback(app, telegram.ext.CommandHandler, "recordatorios")
    replies = []
    await cb(_update(7, "/recordatorios", replies), None)
    assert "código" in replies[0].lower()  # not approved yet
    owner_replies = []
    await cb(_update(42, "/recordatorios", owner_replies), None)
    assert owner_replies == ["lista de 42"]


async def test_background_send_splits_long_text(wired):
    app, _ = wired
    await main_mod._send_quietly(app.bot, "42", "x" * 5000)
    assert [len(t) for _, t in app.sent] == [4096, 904]


async def test_background_send_never_raises():
    class Broken:
        async def send_message(self, chat_id, text):
            raise RuntimeError("Forbidden")

    await main_mod._send_quietly(Broken(), "42", "hola")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_scheduler.py tests/application/test_access_gate.py tests/domain/test_capabilities.py tests/infrastructure/test_bot_commands.py tests/test_main_access.py -q`
Expected: FAIL (missing module, `on_revoke`, menu mismatch, no `recordatorios` handler, no `_send_quietly`).

- [ ] **Step 3: Implement the scheduler**

`src/ari/application/schedule/scheduler.py`:
```python
import asyncio
import contextlib
import logging

log = logging.getLogger("ari.scheduler")


class Scheduler:
    """Runs each job every ``interval`` seconds, in order, on the bot's loop.
    A failing job is logged and never stops the others or the loop."""

    def __init__(self, jobs, interval: float = 30.0):
        self._jobs, self._interval = list(jobs), interval
        self._task: asyncio.Task | None = None

    async def tick(self) -> None:
        for job in self._jobs:
            try:
                await job()
            except Exception:  # noqa: BLE001
                log.exception("scheduler job %r failed", job)

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._interval)
```

- [ ] **Step 4: Implement the revoke hook**

In `src/ari/application/access/gate.py`:
- `def __init__(self, store, owner_ids: set[str], on_revoke=None):` and `self._on_revoke = on_revoke  # async (user_id) -> None, e.g. cancel their schedules`.
- In `_revoke`, right after `await self._store.delete(rec.user_id)`:
```python
        if self._on_revoke is not None:
            await self._on_revoke(rec.user_id)
```

- [ ] **Step 5: Update the capabilities registry**

In `src/ari/domain/agent/capabilities.py`, insert after the progress capability (before `code`):
```python
    Capability(
        command="recordatorios", menu="Ver tus recordatorios y tareas",
        summary="Recordatorios y tareas programadas pedidas en lenguaje natural "
                "(«recuérdame mañana a las 9…», «cada lunes a las 8 resúmeme…»), "
                "puntuales o recurrentes; se cancelan pidiéndolo.",
        usage="/recordatorios lista los activos con su #número."),
    Capability(
        owner_only=True,
        summary="Latido cada hora (fuera del horario de silencio 22–07): revisas por tu "
                "cuenta si hay algo útil que decirle a tu creador; si no, no escribes."),
    Capability(
        owner_only=True,
        summary="Avisos del sistema a tu creador: solicitudes de acceso sin aprobar hace "
                "más de 12 h, fallas repetidas de Claude y tareas pausadas."),
```
In `LIMITATIONS`, delete the two entries `"No puedes escribir por iniciativa propia: solo respondes cuando te escriben."` and `"No puedes programar recordatorios ni tareas periódicas."`.

- [ ] **Step 6: Wire everything in `main.py`**

Add imports:
```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ari.application.schedule.heartbeat import Heartbeat
from ari.application.schedule.llm_health import MonitoredLLM
from ari.application.schedule.run_due_items import RunDueItems
from ari.application.schedule.schedule_actions import ScheduleActions
from ari.application.schedule.scheduler import Scheduler
from ari.application.schedule.system_notices import SystemNotices
from ari.domain.ports.gateway_port import IncomingMessage
from ari.domain.schedule.quiet_hours import parse_window
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
```
(and remove the function-local `from ari.domain.ports.gateway_port import IncomingMessage` inside `chat`).

Add module-level helpers above `build`:
```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _send_quietly(bot, chat_id: str, text: str) -> None:
    """Background sends (reminders, notices, heartbeat): split to Telegram's
    limit and never raise — a failed send must not break the scheduler."""
    for part in TelegramAdapter.split_text(text):
        try:
            await bot.send_message(chat_id=int(chat_id), text=part)
        except Exception as exc:  # noqa: BLE001
            logging.warning("could not send to %s: %s", chat_id, exc)
            return


@dataclasses.dataclass
class Components:
    handler: HandleMessage
    conn: object
    memory: SqliteMemoryAdapter
    llm: MonitoredLLM
    schedule_store: SqliteScheduleStore
    actions: ScheduleActions
    agent: AgentService
    soul: SoulLoader
```

Replace `build` with:
```python
async def build(settings: Settings, env: dict | None, tz) -> Components:
    embeddings = FastEmbedEmbeddings(settings.embedding_model)
    dim = len((await embeddings.embed(["probe"]))[0])
    conn = await connect(settings.db_path, embedding_dim=dim)
    memory = SqliteMemoryAdapter(conn, embedding_dim=dim)
    llm = MonitoredLLM(ClaudeCodeCliAdapter(model=settings.model,
                                            claude_bin=settings.claude_bin, cli_env=env))
    schedule_store = SqliteScheduleStore(conn)
    actions = ScheduleActions(schedule_store, tz, settings.max_items_per_user, clock=_utcnow)
    agent, soul = AgentService(), SoulLoader(settings.soul_dir)
    handler = HandleMessage(
        memory=memory, llm=llm, embeddings=embeddings, agent=agent,
        working_memory_size=settings.working_memory_size,
        recall_top_k=settings.recall_top_k,
        maintainer=MemoryMaintainer(memory, llm),
        soul=soul,
        is_owner=Authorizer(settings.owner_id_set).is_owner,
        actions=actions,
    )
    return Components(handler, conn, memory, llm, schedule_store, actions, agent, soul)
```

In `main()`, after `env = cli_env(settings)` add:
```python
    tz = ZoneInfo(settings.timezone)
    quiet = parse_window(settings.quiet_hours)
```
In `_post_init`, replace the first lines (`handler, conn = await build(...)` through the `gate` assignment) with:
```python
        c = await build(settings, env, tz)
        app.bot_data["handler"] = c.handler
        app.bot_data["conn"] = c.conn
        app.bot_data["actions"] = c.actions
        access_store = SqliteAccessStore(c.conn)
        app.bot_data["gate"] = AccessGate(access_store, settings.owner_id_set,
                                          on_revoke=c.schedule_store.cancel_user)
```
and at the end of `_post_init` (after `app.bot_data["confirm"] = confirm`) add:
```python
        # Proactivity: reminders/tasks, system notices, heartbeat.
        async def send(chat_id: str, text: str) -> None:
            await _send_quietly(app.bot, chat_id, text)

        notices = SystemNotices(c.schedule_store, access_store, settings.owner_id_set,
                                send, tz, quiet, _utcnow)
        c.llm.listener = notices

        async def run_task(item) -> str:
            out = await c.handler(IncomingMessage(item.user_id, item.chat_id, item.text),
                                  allow_actions=False)
            return out.text

        due = RunDueItems(c.schedule_store, send, run_task, notices.task_paused, tz, _utcnow)
        heartbeat = Heartbeat(
            llm=c.llm, memory=c.memory, store=c.schedule_store, agent=c.agent,
            soul=c.soul, checklist=SoulLoader(settings.soul_dir, "HEARTBEAT.md"),
            owners=settings.owner_id_set, send=send, tz=tz, quiet=quiet,
            interval_minutes=settings.heartbeat_minutes, clock=_utcnow)
        await c.schedule_store.reset_running()  # items interrupted by a crash/restart
        scheduler = Scheduler([due, notices.tick, heartbeat])
        scheduler.start()
        app.bot_data["scheduler"] = scheduler
```
In `_post_shutdown`, before closing the connection:
```python
        scheduler = app.bot_data.get("scheduler")
        if scheduler is not None:
            await scheduler.stop()
```
Add the handler (next to `_on_start`):
```python
    async def _on_reminders(update, _context) -> None:
        """/recordatorios: the sender's active reminders and tasks."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if await _admit(msg):
            await _reply_parts(msg, await app.bot_data["actions"].list_text(
                str(msg.from_user.id)))
```
and register it: `app.add_handler(CommandHandler("recordatorios", _on_reminders))` right after the `start` handler.

- [ ] **Step 7: Document**

`README.md` — add a section before `## Access control`:
```markdown
## Proactivity

- **Reminders & tasks in natural language:** "recuérdame mañana a las 9 llamar a
  Juan", "cada lunes a las 8 resúmeme mis pendientes", "cancela el 12". Ari
  confirms with the stored `#id` and time; `/recordatorios` lists them.
  Reminders send the text; tasks run as a normal Ari turn and send the result.
- **Heartbeat (owner only):** every `ARI_HEARTBEAT_MINUTES` Ari reviews
  [`soul/HEARTBEAT.md`](soul/HEARTBEAT.md), your memory and upcoming items and
  writes only if it's worthwhile (max 3/day).
- **System notices (owner only):** stale access requests, Claude CLI failing,
  paused tasks.
- **Quiet hours** (`ARI_QUIET_HOURS`, default 22–7 local): no heartbeat, notices
  are queued until morning. Your own reminders still arrive on time.
- Everything is stored in SQLite and survives `/restart` and outages.
```
Add to the configuration table:
```markdown
| `ARI_TIMEZONE` | no | `America/Guayaquil` | Local time for reminders, cron and quiet hours |
| `ARI_QUIET_HOURS` | no | `22-7` | Quiet window (local hours); empty = none |
| `ARI_HEARTBEAT_MINUTES` | no | `60` | Heartbeat interval; `0` disables it |
| `ARI_MAX_ITEMS_PER_USER` | no | `20` | Active reminders/tasks per user |
```
`.env.example` — append:
```bash
# Proactivity
# ARI_TIMEZONE=America/Guayaquil
# ARI_QUIET_HOURS=22-7
# ARI_HEARTBEAT_MINUTES=60
# ARI_MAX_ITEMS_PER_USER=20
```

- [ ] **Step 8: Run the whole fast suite**

Run: `python -m pytest -m "not slow" -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/ari/application/schedule/scheduler.py src/ari/application/access/gate.py src/ari/domain/agent/capabilities.py src/ari/main.py README.md .env.example tests/application/test_scheduler.py tests/application/test_access_gate.py tests/domain/test_capabilities.py tests/infrastructure/test_bot_commands.py tests/test_main_access.py
git commit -m "feat: wire proactivity (scheduler, /recordatorios, notices, heartbeat)"
```

---

### Task 9: Live verification against the real Claude CLI

**Files:**
- Create: `tests/test_proactivity_live.py`

**Interfaces:**
- Consumes: `ScheduleActions.context`, `extract_actions` (Task 4); `parse_action` (Task 2); `AgentService` (Task 4); `ClaudeCodeCliAdapter`; `claude_cli_env`; `SoulLoader`.

- [ ] **Step 1: Write the slow test**

`tests/test_proactivity_live.py`:
```python
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.schedule_actions import ScheduleActions, extract_actions
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.schedule.actions import CreateAction, parse_action
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.soul.soul_loader import SoulLoader

TZ = ZoneInfo("America/Guayaquil")


@pytest.mark.slow
async def test_real_claude_emits_valid_reminder_block():
    now = datetime.now(timezone.utc)
    conn = await connect(":memory:", embedding_dim=4)
    try:
        actions = ScheduleActions(SqliteScheduleStore(conn), TZ, 20, clock=lambda: now)
        system = AgentService().build_prompt(
            [], None, [], soul=SoulLoader("soul")(), is_owner=True,
            extra=await actions.context("u1"))
        s = Settings()
        llm = ClaudeCodeCliAdapter(cli_env=claude_cli_env(s.claude_oauth_token,
                                                          s.claude_config_dir))
        reply = await llm.complete(system, [Message("u1", "user",
                                                    "recuérdame en 5 minutos probar Ari",
                                                    now)])
    finally:
        await conn.close()
    _clean, blocks = extract_actions(reply)
    assert len(blocks) == 1, reply
    act = parse_action(json.loads(blocks[0]), now, TZ)
    assert isinstance(act, CreateAction) and act.at is not None
    assert timedelta(minutes=3) <= act.at - now <= timedelta(minutes=7)
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_proactivity_live.py -m slow -q`
Expected: PASS (needs the claude CLI logged in, or `ARI_CLAUDE_OAUTH_TOKEN` in `.env`). If it fails, read the `reply` in the assertion message and adjust the `_FORMAT` wording in `schedule_actions.py` — not the test.

- [ ] **Step 3: Run the full suite (fast + slow)**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_proactivity_live.py
git commit -m "test: live check that Claude emits a valid reminder action"
```

- [ ] **Step 5: Manual acceptance in Telegram (owner)**

After `/restart` + `dale`:
1. "recuérdame en 2 minutos probar Ari" → `✅ Recordatorio #N … ` and `⏰ Recordatorio: probar Ari` ~2 min later.
2. "cada lunes a las 8 resúmeme mis pendientes" → `🔁 Tarea #N (cada lunes 08:00) …`.
3. `/recordatorios` lists both; "cancela el N" → `🗑️ Cancelado #N`.
4. "recuérdame en 3 minutos X", then `/restart` + `dale` immediately → the reminder still arrives once.
