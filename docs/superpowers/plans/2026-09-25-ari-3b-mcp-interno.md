# Ari 3B — Internal MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ari's own actions (agenda, explicit memory, owner administration, owner→user messages) become real tools served by Ari's own stdio MCP server, with code-generated receipts and an outbox, replacing the `<ari-action>` text blocks.

**Architecture:** `python -m ari.mcp_server` (official `mcp` SDK v2, `MCPServer`) is added per turn to the CLI's MCP config by `ToolPolicy.turn(...)`, which writes a per-turn config file (0600, atomic, deleted after the turn) whose `ari` entry carries the actor/role/context/turn id in `env` — never chosen by the model. Tool logic lives in `AriTools` (application layer), permission-checked against a pure table in the domain, writing to Ari's SQLite; side effects needing the bot go to an `outbox` table and every change writes a `receipts` row that Ari appends to the reply.

**Tech Stack:** Python ≥3.11, `mcp>=2.2,<3` (`from mcp.server.mcpserver import MCPServer`), aiosqlite (WAL), Claude Code CLI, pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-09-25-ari-3b-mcp-interno-design.md`

## Global Constraints

- Python `>=3.11`; Windows, macOS and Linux. Only new dependency: `mcp>=2.2,<3`.
- **Verified SDK API (2026-09-25 probe, mcp 2.2.0):** `from mcp.server.mcpserver import MCPServer`; `server = MCPServer("ari")`; `@server.tool()` on `async def`/`def` functions (type hints → input schema, docstring → description); `server.run()` = stdio. In-process: `await server.list_tools()` → list of tools with `.name`; `await server.call_tool(name, args)` → result with `.content[0].text`. `mcp.server.fastmcp` does NOT exist in v2.
- **Verified CLI behavior:** MCP tools need `ToolSearch` in `--tools` and `--allowed-tools`; per-tool allow entries `mcp__ari__<tool>` work; `env` in the MCP config reaches the server process.
- Tool names (server `ari`): `agendar`, `listar_agenda`, `cancelar`, `recordar_dato`, `olvidar_dato`, `ver_datos`, `aprobar_acceso`, `revocar_acceso`, `ver_accesos`, `enviar_mensaje`.
- Permissions (context × role) exactly as spec §4; enforced by `--allowed-tools` AND inside the server.
- Identity/context only from the server env (`ARI_ACTOR_ID`, `ARI_ACTOR_CHAT`, `ARI_ACTOR_NAME`, `ARI_ROLE`, `ARI_CONTEXT`, `ARI_TURN_ID`, plus `ARI_DB_PATH`, `ARI_TIMEZONE`, `ARI_MAX_ITEMS`, `ARI_OWNER_IDS`); no tool takes a caller id.
- Receipt texts and the message/approval formats are exactly those of spec §6.
- All user-facing text: español neutro, **tuteo** (`tests/test_tone.py`).
- `toolset` is passed to an LLM only when not None.
- Run tests with `.venv/Scripts/python.exe -m pytest …` (Windows) / `.venv/bin/python -m pytest …`; written below as `python -m pytest`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The flusher runs concurrently** (after a chat turn and on the scheduler tick at the same moment): an outbox message must be sent exactly once → test in Task 8.
2. **A turn that times out after a tool already ran**: the receipt must still reach the user and the outbox must still be flushed → test in Task 8.
3. **`@username` with different case or a leading `@`** must resolve (`@Juan` = `juan`) → test in Task 5.
4. **The per-turn config file must be deleted even when the LLM call raises** → test in Task 7.
5. **The server process started without any actor env** (e.g. `tools/list` handshake) must not crash; actor is only required when a tool runs → test in Task 6.

---

### Task 1: Dependency, permission table, display name

**Files:**
- Modify: `pyproject.toml` (add `"mcp>=2.2,<3"` to `dependencies`)
- Create: `src/ari/domain/tools/ari_permissions.py`
- Modify: `src/ari/domain/ports/gateway_port.py` (`IncomingMessage.display_name`)
- Modify: `src/ari/infrastructure/gateway/telegram_adapter.py` (fill `display_name`)
- Test: `tests/domain/test_ari_permissions.py`, `tests/infrastructure/test_telegram_adapter.py` (append)

**Interfaces:**
- Produces: `CHAT = "chat"`, `TASK = "task"`, `HEARTBEAT = "heartbeat"`, `ARI_SERVER = "ari"`, `ARI_TOOLS: tuple[str, ...]` (the 10 names, in the order listed in Global Constraints), `allowed_ari_tools(is_owner: bool, context: str) -> tuple[str, ...]` (in `ARI_TOOLS` order); `IncomingMessage(user_id, chat_id, text, display_name: str = "")`.

- [ ] **Step 1: Add the dependency and install**

In `pyproject.toml` `dependencies`, append `"mcp>=2.2,<3",` after `"tzdata>=2024.1",`. Run `python -m pip install -e ".[dev]"`. Expected: installs `mcp` 2.x.

- [ ] **Step 2: Write the failing tests**

`tests/domain/test_ari_permissions.py`:
```python
import pytest

from ari.domain.tools.ari_permissions import (
    ARI_TOOLS, CHAT, HEARTBEAT, TASK, allowed_ari_tools)

USER_CHAT = {"agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos"}


def test_catalogue():
    assert ARI_TOOLS == ("agendar", "listar_agenda", "cancelar", "recordar_dato",
                         "olvidar_dato", "ver_datos", "aprobar_acceso", "revocar_acceso",
                         "ver_accesos", "enviar_mensaje")


def test_chat_permissions():
    assert set(allowed_ari_tools(False, CHAT)) == USER_CHAT
    assert allowed_ari_tools(True, CHAT) == ARI_TOOLS


@pytest.mark.parametrize("owner", [False, True])
def test_task_is_read_only(owner):
    assert allowed_ari_tools(owner, TASK) == ("listar_agenda", "ver_datos")


def test_heartbeat_read_only_plus_owner_accesses():
    assert allowed_ari_tools(False, HEARTBEAT) == ("listar_agenda", "ver_datos")
    assert allowed_ari_tools(True, HEARTBEAT) == ("listar_agenda", "ver_datos", "ver_accesos")


def test_unknown_context_gets_nothing():
    assert allowed_ari_tools(True, "otro") == ()
```

Append to `tests/infrastructure/test_telegram_adapter.py`:
```python
def test_to_incoming_carries_display_name():
    from types import SimpleNamespace
    from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter
    msg = SimpleNamespace(text="hola", chat_id=5,
                          from_user=SimpleNamespace(id=7, first_name="Juan", username="juanp"))
    inc = TelegramAdapter.to_incoming(SimpleNamespace(effective_message=msg))
    assert inc.display_name == "Juan"
    msg.from_user.first_name = None
    assert TelegramAdapter.to_incoming(SimpleNamespace(effective_message=msg)).display_name == "juanp"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/domain/test_ari_permissions.py tests/infrastructure/test_telegram_adapter.py -q`
Expected: FAIL (`ModuleNotFoundError`, no `display_name`).

- [ ] **Step 4: Implement**

`src/ari/domain/tools/ari_permissions.py`:
```python
"""Which of Ari's own tools each role may use in each context (pure)."""

CHAT, TASK, HEARTBEAT = "chat", "task", "heartbeat"
ARI_SERVER = "ari"
ARI_TOOLS = ("agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "aprobar_acceso", "revocar_acceso", "ver_accesos",
             "enviar_mensaje")

_READ = {"listar_agenda", "ver_datos"}
_USER_CHAT = _READ | {"agendar", "cancelar", "recordar_dato", "olvidar_dato"}


def allowed_ari_tools(is_owner: bool, context: str) -> tuple[str, ...]:
    """Scheduled tasks and the heartbeat are read-only: nothing autonomous can
    schedule, remember, approve or message (no self-loops, no injected actions)."""
    if context == CHAT:
        allowed = set(ARI_TOOLS) if is_owner else _USER_CHAT
    elif context == TASK:
        allowed = _READ
    elif context == HEARTBEAT:
        allowed = _READ | ({"ver_accesos"} if is_owner else set())
    else:
        allowed = set()
    return tuple(t for t in ARI_TOOLS if t in allowed)
```

`src/ari/domain/ports/gateway_port.py` — `IncomingMessage` becomes:
```python
@dataclass(frozen=True, slots=True)
class IncomingMessage:
    user_id: str
    chat_id: str
    text: str
    display_name: str = ""  # used to sign messages Ari sends on this user's behalf
```

`src/ari/infrastructure/gateway/telegram_adapter.py` — in `to_incoming`, return:
```python
        user = msg.from_user
        return IncomingMessage(
            user_id=str(user.id), chat_id=str(msg.chat_id), text=msg.text,
            display_name=getattr(user, "first_name", None) or getattr(user, "username", None) or "")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest -m "not slow" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ari/domain/tools/ari_permissions.py src/ari/domain/ports/gateway_port.py src/ari/infrastructure/gateway/telegram_adapter.py tests/domain/test_ari_permissions.py tests/infrastructure/test_telegram_adapter.py
git commit -m "feat: ari tool permission table, display name, mcp dependency"
```

---

### Task 2: Persistence — receipts, outbox, delete_fact, open_existing

**Files:**
- Modify: `src/ari/infrastructure/persistence/db.py` (schema + `open_existing`)
- Create: `src/ari/infrastructure/persistence/sqlite_turn_log.py`
- Modify: `src/ari/infrastructure/memory/sqlite_memory_adapter.py` (`delete_fact`)
- Modify: `src/ari/domain/memory/memory_port.py` (declare `delete_fact`)
- Modify: `tests/fakes.py` (`FakeMemory.delete_fact`)
- Test: `tests/infrastructure/test_sqlite_turn_log.py`, `tests/infrastructure/test_sqlite_memory.py` (append)

**Interfaces:**
- Produces:
  - `open_existing(db_path: str) -> aiosqlite.Connection` (no sqlite-vec; runs the plain schema; `row_factory = aiosqlite.Row`; busy_timeout 5000).
  - `SqliteTurnLog(conn)`: `add_receipt(turn_id, text)`, `receipts(turn_id) -> list[str]` (insertion order), `purge_receipts(before: datetime) -> int`, `outbox_add(chat_id, text) -> int`, `outbox_pending(limit: int = 50) -> list[tuple[int, str, str, int]]` (`(id, chat_id, text, attempts)`, unsent, oldest first), `outbox_mark_sent(item_id, now: datetime)`, `outbox_mark_failed(item_id) -> int` (new attempts), `outbox_drop(item_id)`.
  - `SqliteMemoryAdapter.delete_fact(user_id, key) -> bool` (True if something was deleted).

- [ ] **Step 1: Write the failing tests**

`tests/infrastructure/test_sqlite_turn_log.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest

from ari.infrastructure.persistence.db import connect, open_existing
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog

T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def log(tmp_path):
    db = str(tmp_path / "ari.db")
    first = await connect(db, embedding_dim=4)  # creates the full schema like main does
    await first.close()
    conn = await open_existing(db)
    yield SqliteTurnLog(conn)
    await conn.close()


async def test_receipts_per_turn_in_order(log):
    await log.add_receipt("t1", "✅ uno")
    await log.add_receipt("t2", "otro turno")
    await log.add_receipt("t1", "🗑️ dos")
    assert await log.receipts("t1") == ["✅ uno", "🗑️ dos"]
    assert await log.receipts("nope") == []


async def test_purge_receipts(log):
    await log.add_receipt("t1", "x")
    assert await log.purge_receipts(datetime.now(timezone.utc) + timedelta(seconds=5)) == 1
    assert await log.receipts("t1") == []


async def test_outbox_lifecycle(log):
    a = await log.outbox_add("7", "hola")
    b = await log.outbox_add("8", "chao")
    assert [p[:3] for p in await log.outbox_pending()] == [(a, "7", "hola"), (b, "8", "chao")]
    await log.outbox_mark_sent(a, T0)
    assert await log.outbox_mark_failed(b) == 1
    assert await log.outbox_mark_failed(b) == 2
    assert [p[0] for p in await log.outbox_pending()] == [b]
    await log.outbox_drop(b)
    assert await log.outbox_pending() == []


async def test_open_existing_does_not_need_sqlite_vec(tmp_path):
    conn = await open_existing(str(tmp_path / "fresh.db"))  # plain tables only
    rows = await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    await conn.close()
    assert {"schedules", "facts", "access", "receipts", "outbox"} <= names
```

Append to `tests/infrastructure/test_sqlite_memory.py`:
```python
async def test_delete_fact(adapter):
    await adapter.upsert_fact("u1", "color", "azul")
    assert await adapter.delete_fact("u1", "color") is True
    assert await adapter.delete_fact("u1", "color") is False
    assert await adapter.get_facts("u1") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/infrastructure/test_sqlite_turn_log.py tests/infrastructure/test_sqlite_memory.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/ari/infrastructure/persistence/db.py`, append to `_SCHEMA` (before the closing `"""`):
```sql

CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_receipts_turn ON receipts(turn_id);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, sent_at TEXT);
```
and add, after `connect`:
```python
async def open_existing(db_path: str) -> aiosqlite.Connection:
    """Light connection for Ari's MCP server process: no sqlite-vec, no vector
    table — only the plain tables it reads/writes (schedules, facts, access,
    receipts, outbox, kv). WAL is a persistent DB property set by ``connect``."""
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.executescript(_SCHEMA)
    await conn.commit()
    return conn
```

`src/ari/infrastructure/persistence/sqlite_turn_log.py`:
```python
import asyncio
from datetime import datetime, timezone

import aiosqlite


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class SqliteTurnLog:
    """Per-turn receipts (code-generated confirmations) and the outbox of
    messages Ari's MCP server asks the bot to send."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def _write(self, sql: str, params: tuple) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur

    async def add_receipt(self, turn_id: str, text: str) -> None:
        await self._write("INSERT INTO receipts (turn_id, text, created_at) VALUES (?, ?, ?)",
                          (turn_id, text, _iso(datetime.now(timezone.utc))))

    async def receipts(self, turn_id: str) -> list[str]:
        rows = await self._conn.execute_fetchall(
            "SELECT text FROM receipts WHERE turn_id = ? ORDER BY id", (turn_id,))
        return [r["text"] for r in rows]

    async def purge_receipts(self, before: datetime) -> int:
        cur = await self._write("DELETE FROM receipts WHERE created_at < ?", (_iso(before),))
        return cur.rowcount

    async def outbox_add(self, chat_id: str, text: str) -> int:
        cur = await self._write("INSERT INTO outbox (chat_id, text, created_at) VALUES (?, ?, ?)",
                                (chat_id, text, _iso(datetime.now(timezone.utc))))
        return cur.lastrowid

    async def outbox_pending(self, limit: int = 50) -> list[tuple[int, str, str, int]]:
        rows = await self._conn.execute_fetchall(
            "SELECT id, chat_id, text, attempts FROM outbox WHERE sent_at IS NULL "
            "ORDER BY id LIMIT ?", (limit,))
        return [(r["id"], r["chat_id"], r["text"], r["attempts"]) for r in rows]

    async def outbox_mark_sent(self, item_id: int, now: datetime) -> None:
        await self._write("UPDATE outbox SET sent_at = ? WHERE id = ?", (_iso(now), item_id))

    async def outbox_mark_failed(self, item_id: int) -> int:
        await self._write("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (item_id,))
        rows = await self._conn.execute_fetchall(
            "SELECT attempts FROM outbox WHERE id = ?", (item_id,))
        return rows[0]["attempts"] if rows else 0

    async def outbox_drop(self, item_id: int) -> None:
        await self._write("DELETE FROM outbox WHERE id = ?", (item_id,))
```

`SqliteMemoryAdapter` — add after `upsert_fact`:
```python
    async def delete_fact(self, user_id: str, key: str) -> bool:
        async with self._write_lock:
            cur = await self._conn.execute(
                "DELETE FROM facts WHERE user_id = ? AND key = ?", (user_id, key))
            await self._conn.commit()
            return cur.rowcount > 0
```
`MemoryPort` — add `async def delete_fact(self, user_id: str, key: str) -> bool: ...`.
`tests/fakes.py` `FakeMemory` (facts live in `self._facts: dict[(user_id, key), Fact]`) — add after `upsert_fact`:
```python
    async def delete_fact(self, user_id, key):
        return self._facts.pop((user_id, key), None) is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m "not slow" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/persistence src/ari/infrastructure/memory/sqlite_memory_adapter.py src/ari/domain/memory/memory_port.py tests/fakes.py tests/infrastructure/test_sqlite_turn_log.py tests/infrastructure/test_sqlite_memory.py
git commit -m "feat: receipts and outbox tables, delete_fact, light DB connection"
```

---

### Task 3: Agenda formatting and retiring `<ari-action>` from the prompt

**Files:**
- Create: `src/ari/application/schedule/agenda_format.py`
- Modify: `src/ari/application/schedule/schedule_actions.py`
- Modify: `tests/application/test_schedule_actions.py` (rewrite)
- Delete: `tests/test_proactivity_live.py` (it tested the retired block format; Task 9 replaces it)

**Interfaces:**
- Consumes: `describe_cron`, `fmt_short`, `fmt_long`, `ScheduleItem`, `PAUSED`, `REMINDER`.
- Produces:
  - `item_line(item: ScheduleItem, tz) -> str` (`"#12 · sáb 26/09 09:00 · llamar a Juan"`, recurring `"#13 · cada lunes 08:00 (próxima lun 28/09 08:00) · resumen"`, paused suffix `" · ⏸️ pausada"`).
  - `created_receipt(item_id: int, kind: str, text: str, next_run: datetime, cron: str | None, tz) -> str` (spec §6 formats).
  - `ScheduleActions(store, tz, max_items, clock)` keeps `context(user_id) -> str` (now: date/time + tools hint, no listing, no block format) and `list_text(user_id)`; `apply` is removed. `extract_actions` stays (defensive stripping).

- [ ] **Step 1: Rewrite the tests**

Replace `tests/application/test_schedule_actions.py` with:
```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.agenda_format import created_receipt, item_line
from ari.application.schedule.schedule_actions import ScheduleActions, extract_actions
from ari.domain.schedule.entities import PAUSED, REMINDER, TASK, ScheduleItem
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # vie 15:00 local
AT = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)  # sáb 09:00 local


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


def test_extract_strips_blocks_including_unclosed():
    clean, blocks = extract_actions('Listo. <ari-action>{"a":1}</ari-action>\n<ari-action>{"type":')
    assert clean == "Listo." and len(blocks) == 1


def test_item_line_formats():
    one = ScheduleItem(12, "u", "c", REMINDER, "llamar a Juan", AT, None, "active")
    assert item_line(one, TZ) == "#12 · sáb 26/09 09:00 · llamar a Juan"
    rec = ScheduleItem(13, "u", "c", TASK, "resumen",
                       datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc), "0 8 * * 1", PAUSED)
    assert item_line(rec, TZ) == ("#13 · cada lunes 08:00 (próxima lun 28/09 08:00) · resumen"
                                  " · ⏸️ pausada")


def test_created_receipts():
    assert created_receipt(12, REMINDER, "llamar a Juan", AT, None, TZ) == \
        "✅ Recordatorio #12: llamar a Juan — sáb 26/09 09:00"
    nxt = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
    assert created_receipt(13, TASK, "resumen", nxt, "0 8 * * 1", TZ) == \
        "🔁 Tarea #13 (cada lunes 08:00): resumen — próxima: lun 28/09 08:00"


async def test_context_has_time_and_tools_hint_but_no_block_format(store):
    ctx = await ScheduleActions(store, TZ, 20, clock=lambda: NOW).context("u1")
    assert "viernes 25/09/2026 15:00 (America/Guayaquil)" in ctx
    assert "agendar" in ctx and "listar_agenda" in ctx
    assert "<ari-action>" not in ctx


async def test_list_text(store):
    actions = ScheduleActions(store, TZ, 20, clock=lambda: NOW)
    assert "No tienes" in await actions.list_text("u1")
    await store.add("u1", "c1", REMINDER, "llamar a Juan", AT, None)
    assert "#1 · sáb 26/09 09:00 · llamar a Juan" in await actions.list_text("u1")
```

Delete `tests/test_proactivity_live.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_schedule_actions.py -q`
Expected: FAIL (`ModuleNotFoundError: agenda_format`; context still has the block format).

- [ ] **Step 3: Implement**

`src/ari/application/schedule/agenda_format.py`:
```python
from datetime import datetime

from ari.domain.schedule.actions import describe_cron
from ari.domain.schedule.entities import PAUSED, REMINDER, ScheduleItem
from ari.domain.schedule.timefmt import fmt_short


def _when(next_run: datetime, cron: str | None, tz) -> str:
    if cron:
        return f"{describe_cron(cron)} (próxima {fmt_short(next_run, tz)})"
    return fmt_short(next_run, tz)


def item_line(item: ScheduleItem, tz) -> str:
    paused = " · ⏸️ pausada" if item.status == PAUSED else ""
    return f"#{item.id} · {_when(item.next_run_at, item.cron, tz)} · {item.text}{paused}"


def created_receipt(item_id: int, kind: str, text: str, next_run: datetime,
                    cron: str | None, tz) -> str:
    label = "Recordatorio" if kind == REMINDER else "Tarea"
    when = fmt_short(next_run, tz)
    if cron:
        return f"🔁 {label} #{item_id} ({describe_cron(cron)}): {text} — próxima: {when}"
    return f"✅ {label} #{item_id}: {text} — {when}"
```

`src/ari/application/schedule/schedule_actions.py` becomes:
```python
import re
from datetime import datetime, timezone

from ari.application.schedule.agenda_format import item_line
from ari.domain.schedule.timefmt import fmt_long

_BLOCK = re.compile(r"<ari-action>(.*?)</ari-action>", re.S | re.I)
_DANGLING = re.compile(r"<ari-action>.*\Z", re.S | re.I)

_TOOLS_HINT = """## Tu agenda y tus datos
Para recordatorios, tareas y datos del usuario usa las herramientas del servidor «ari»:
agendar, listar_agenda, cancelar, recordar_dato, olvidar_dato y ver_datos (si hablas con
tu creador, también aprobar_acceso, revocar_acceso, ver_accesos y enviar_mensaje).
Calcula «at» a partir de la fecha y hora actual de arriba. El sistema agrega al final la
confirmación exacta de lo que hiciste: no inventes números ni horas."""


def extract_actions(reply: str) -> tuple[str, list[str]]:
    """Split a reply into (text the user sees, raw JSON of each legacy action block).
    Blocks are no longer honored; this only keeps stray ones out of sight."""
    blocks = [b.strip() for b in _BLOCK.findall(reply)]
    clean = _DANGLING.sub("", _BLOCK.sub("", reply))  # an unclosed block never leaks
    return clean.strip(), blocks


class ScheduleActions:
    """Prompt context about time and agenda tools, and the /recordatorios listing."""

    def __init__(self, store, tz, max_items: int,
                 clock=lambda: datetime.now(timezone.utc)):
        self._store, self._tz, self._max, self._clock = store, tz, max_items, clock

    async def context(self, user_id: str) -> str:
        return (f"## Fecha y hora actual\n{fmt_long(self._clock(), self._tz)} "
                f"({self._tz.key})\n\n{_TOOLS_HINT}")

    async def list_text(self, user_id: str) -> str:
        items = await self._store.list_for_user(user_id)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "Tus recordatorios y tareas:\n" + "\n".join(item_line(i, self._tz) for i in items)
```

In `src/ari/application/handle_message.py`, remove the `apply` block (the `if self._actions is not None: reply = await self._actions.apply(...)` lines) and replace it with:
```python
        reply, _legacy = extract_actions(reply)  # stray legacy blocks never reach the user
```
adding `from ari.application.schedule.schedule_actions import extract_actions`.

In `tests/application/test_handle_message.py`, delete the `_FakeActions` class and `test_actions_context_in_prompt_and_reply_processed`, and remove the `apply` method from `_BrokenContextActions`. Add:
```python
class _ContextOnly:
    async def context(self, user_id):
        return "## CONTEXTO-AGENDA"


async def test_actions_context_in_prompt_and_legacy_blocks_hidden():
    llm = FakeLLM(reply='hecho <ari-action>{"type":"cancel","id":1}</ari-action>')
    handler = HandleMessage(memory=FakeMemory(), llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), actions=_ContextOnly())
    out = await handler(IncomingMessage("u1", "c1", "hola"))
    assert "## CONTEXTO-AGENDA" in llm.calls[0][0]
    assert out.text == "hecho"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m "not slow" -q`
Expected: PASS (`test_tone.py` included).

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/schedule src/ari/application/handle_message.py tests/application/test_schedule_actions.py tests/application/test_handle_message.py
git rm tests/test_proactivity_live.py
git commit -m "refactor: agenda formatting helpers; retire <ari-action> from the prompt"
```

---

### Task 4: AriTools — actor, agenda and memory

**Files:**
- Create: `src/ari/application/ari_tools.py`
- Test: `tests/application/test_ari_tools_agenda.py`

**Interfaces:**
- Consumes: `allowed_ari_tools`, `CHAT`/`TASK`/`HEARTBEAT` (Task 1); `SqliteTurnLog` (Task 2); `created_receipt`, `item_line` (Task 3); `parse_action`, `ActionError`, `next_cron_run` (domain); `SqliteScheduleStore`; memory `get_facts/upsert_fact/delete_fact`.
- Produces:
  - `@dataclass(frozen=True) Actor(user_id: str, chat_id: str, name: str, is_owner: bool, context: str, turn_id: str)`
  - `DENIED = "No permitido en este contexto."`
  - `AriTools(actor, *, schedule, memory, turn_log, tz, max_items: int, clock, gate=None, access=None)` with async methods returning `str`: `agendar(tipo, texto, at=None, cron=None)`, `listar_agenda()`, `cancelar(id)`, `recordar_dato(clave, valor)`, `olvidar_dato(clave)`, `ver_datos()` (Task 5 adds the admin/message methods to this same class).

- [ ] **Step 1: Write the failing tests**

`tests/application/test_ari_tools_agenda.py`:
```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.schedule.entities import CANCELLED, REMINDER
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # vie 15:00 local


@pytest.fixture
async def deps():
    conn = await connect(":memory:", embedding_dim=4)
    yield (SqliteScheduleStore(conn), SqliteMemoryAdapter(conn, embedding_dim=4),
           SqliteTurnLog(conn))
    await conn.close()


def _tools(deps, user="u1", owner=False, context=CHAT, turn="t1", max_items=20):
    schedule, memory, log = deps
    actor = Actor(user, f"c-{user}", "Gabriel", owner, context, turn)
    return AriTools(actor, schedule=schedule, memory=memory, turn_log=log, tz=TZ,
                    max_items=max_items, clock=lambda: NOW)


async def test_agendar_stores_for_actor_and_writes_receipt(deps):
    schedule, _, log = deps
    out = await _tools(deps).agendar("recordatorio", "llamar a Juan",
                                     at="2026-09-26T09:00:00-05:00")
    assert out == "✅ Recordatorio #1: llamar a Juan — sáb 26/09 09:00"
    item = await schedule.get(1)
    assert (item.user_id, item.chat_id, item.kind) == ("u1", "c-u1", REMINDER)
    assert await log.receipts("t1") == [out]


async def test_agendar_recurring_task(deps):
    out = await _tools(deps).agendar("tarea", "resumen", cron="0 8 * * 1")
    assert out == "🔁 Tarea #1 (cada lunes 08:00): resumen — próxima: lun 28/09 08:00"


async def test_agendar_invalid_explains_and_writes_nothing(deps):
    _, _, log = deps
    out = await _tools(deps).agendar("recordatorio", "x", at="2020-01-01T00:00")
    assert out.startswith("No pude agendarlo: la fecha ya pasó")
    assert await log.receipts("t1") == []
    assert (await _tools(deps).agendar("otra", "x", at="2026-09-26T09:00")).startswith(
        "No pude agendarlo")


async def test_cap(deps):
    t = _tools(deps, max_items=1)
    await t.agendar("recordatorio", "a", at="2026-09-26T09:00")
    assert "ya tienes 1" in await t.agendar("recordatorio", "b", at="2026-09-26T10:00")


async def test_listar_and_cancel_only_own(deps):
    schedule, _, log = deps
    await _tools(deps).agendar("recordatorio", "mío", at="2026-09-26T09:00")
    assert "#1 · sáb 26/09 09:00 · mío" in await _tools(deps).listar_agenda()
    assert "No encontré el #1" in await _tools(deps, user="u2").cancelar(1)
    assert await _tools(deps, turn="t2").cancelar(1) == "🗑️ Cancelado #1: mío"
    assert (await schedule.get(1)).status == CANCELLED
    assert await log.receipts("t2") == ["🗑️ Cancelado #1: mío"]
    assert "No tienes" in await _tools(deps).listar_agenda()


async def test_memory_tools(deps):
    t = _tools(deps)
    assert await t.recordar_dato("color favorito", "azul") == "🧠 Guardé: color favorito = azul"
    assert "color favorito: azul" in await t.ver_datos()
    assert await t.olvidar_dato("color favorito") == "🧹 Olvidé: color favorito"
    assert "No tenía guardado" in await t.olvidar_dato("color favorito")
    assert "No tengo datos" in await t.ver_datos()
    assert "falta" in await t.recordar_dato("", "x")


@pytest.mark.parametrize("context", [TASK, HEARTBEAT])
async def test_write_tools_denied_outside_chat(deps, context):
    t = _tools(deps, context=context, owner=True)
    assert await t.agendar("recordatorio", "x", at="2026-09-26T09:00") == DENIED
    assert await t.cancelar(1) == DENIED
    assert await t.recordar_dato("a", "b") == DENIED
    assert await t.olvidar_dato("a") == DENIED
    assert "No tienes" in await t.listar_agenda()  # read-only still works
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_ari_tools_agenda.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`src/ari/application/ari_tools.py`:
```python
"""Logic behind Ari's own MCP tools. Every method acts for the Actor given by
Ari (never chosen by the model), checks the permission table, validates with the
domain rules, and records a code-generated receipt for each change."""
import logging
from dataclasses import dataclass

from ari.application.schedule.agenda_format import created_receipt, item_line
from ari.domain.schedule.actions import ActionError, next_cron_run, parse_action
from ari.domain.schedule.entities import ACTIVE, CANCELLED, PAUSED, REMINDER, RUNNING, TASK
from ari.domain.tools.ari_permissions import allowed_ari_tools

log = logging.getLogger("ari.tools")

DENIED = "No permitido en este contexto."
_KINDS = {"recordatorio": REMINDER, "tarea": TASK}


@dataclass(frozen=True)
class Actor:
    user_id: str
    chat_id: str
    name: str
    is_owner: bool
    context: str
    turn_id: str


class AriTools:
    def __init__(self, actor: Actor, *, schedule, memory, turn_log, tz, max_items: int,
                 clock, gate=None, access=None):
        self._a, self._schedule, self._memory, self._log = actor, schedule, memory, turn_log
        self._tz, self._max, self._clock = tz, max_items, clock
        self._gate, self._access = gate, access

    def _allowed(self, tool: str) -> bool:
        if tool in allowed_ari_tools(self._a.is_owner, self._a.context):
            return True
        log.warning("denied %s for %s in context %s", tool, self._a.user_id, self._a.context)
        return False

    async def _receipt(self, text: str) -> str:
        await self._log.add_receipt(self._a.turn_id, text)
        return text

    # ---- agenda -----------------------------------------------------------

    async def agendar(self, tipo: str, texto: str, at: str | None = None,
                      cron: str | None = None) -> str:
        if not self._allowed("agendar"):
            return DENIED
        kind = _KINDS.get((tipo or "").strip().lower())
        if kind is None:
            return "No pude agendarlo: el tipo debe ser «recordatorio» o «tarea»."
        now = self._clock()
        try:
            action = parse_action({"type": kind, "text": texto, "at": at, "cron": cron},
                                  now, self._tz)
        except ActionError as exc:
            return f"No pude agendarlo: {exc}."
        if await self._schedule.count_active(self._a.user_id) >= self._max:
            return (f"No pude agendarlo: ya tienes {self._max} recordatorios o tareas "
                    "activos. Cancela alguno primero.")
        next_run = action.at or next_cron_run(action.cron, now, self._tz)
        item_id = await self._schedule.add(self._a.user_id, self._a.chat_id, action.kind,
                                           action.text, next_run, action.cron)
        return await self._receipt(created_receipt(item_id, action.kind, action.text,
                                                   next_run, action.cron, self._tz))

    async def listar_agenda(self) -> str:
        if not self._allowed("listar_agenda"):
            return DENIED
        items = await self._schedule.list_for_user(self._a.user_id)
        if not items:
            return "No tienes recordatorios ni tareas activos."
        return "\n".join(item_line(i, self._tz) for i in items)

    async def cancelar(self, id: int) -> str:
        if not self._allowed("cancelar"):
            return DENIED
        item = await self._schedule.get(int(id))
        if (item is None or item.user_id != self._a.user_id
                or item.status not in (ACTIVE, PAUSED, RUNNING)):
            return f"No encontré el #{id} entre tus recordatorios."
        await self._schedule.set_status(item.id, CANCELLED)
        return await self._receipt(f"🗑️ Cancelado #{item.id}: {item.text}")

    # ---- explicit memory --------------------------------------------------

    async def recordar_dato(self, clave: str, valor: str) -> str:
        if not self._allowed("recordar_dato"):
            return DENIED
        clave, valor = (clave or "").strip(), (valor or "").strip()
        if not clave or not valor:
            return "No pude guardarlo: falta la clave o el valor."
        if len(clave) > 100 or len(valor) > 500:
            return "No pude guardarlo: es demasiado largo."
        await self._memory.upsert_fact(self._a.user_id, clave, valor)
        return await self._receipt(f"🧠 Guardé: {clave} = {valor}")

    async def olvidar_dato(self, clave: str) -> str:
        if not self._allowed("olvidar_dato"):
            return DENIED
        clave = (clave or "").strip()
        if not await self._memory.delete_fact(self._a.user_id, clave):
            return f"No tenía guardado «{clave}»."
        return await self._receipt(f"🧹 Olvidé: {clave}")

    async def ver_datos(self) -> str:
        if not self._allowed("ver_datos"):
            return DENIED
        facts = await self._memory.get_facts(self._a.user_id)
        if not facts:
            return "No tengo datos guardados de ti."
        return "\n".join(f"- {f.key}: {f.value}" for f in facts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/application/test_ari_tools_agenda.py -q` then `python -m pytest -m "not slow" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_agenda.py
git commit -m "feat: AriTools agenda and explicit memory with permissions and receipts"
```

---

### Task 5: AriTools — owner administration and messages

**Files:**
- Modify: `src/ari/application/ari_tools.py`
- Test: `tests/application/test_ari_tools_admin.py`

**Interfaces:**
- Consumes: `AccessGate(store, owner_ids, on_revoke=)` with `admin_command(text, user_id) -> GateResult(reply, notifications)`; `normalize_code`; `SqliteAccessStore.list_all()`; `APPROVED`, `PENDING`; `SqliteTurnLog.outbox_add`.
- Produces: `AriTools.aprobar_acceso(codigo_o_usuario)`, `revocar_acceso(usuario)`, `ver_accesos()`, `enviar_mensaje(destinatario, texto)` — all `-> str`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_ari_tools_admin.py`:
```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.access.gate import AccessGate
from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.access.entities import APPROVED
from ari.domain.schedule.entities import CANCELLED, REMINDER
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
OWNER = "42"


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    schedule, access = SqliteScheduleStore(conn), SqliteAccessStore(conn)
    await access.create_pending("7", "Juan", "K7QMX3PA")
    await access.create_pending("8", "pedro", "P3DR0XYZ")
    await access.set_status("8", APPROVED)
    yield conn, schedule, access, SqliteTurnLog(conn)
    await conn.close()


def _tools(env, user=OWNER, owner=True, context=CHAT, name="Gabriel"):
    conn, schedule, access, log = env
    gate = AccessGate(access, {OWNER}, on_revoke=schedule.cancel_user)
    actor = Actor(user, user, name, owner, context, "t1")
    return AriTools(actor, schedule=schedule, memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                    turn_log=log, tz=TZ, max_items=20, clock=lambda: NOW,
                    gate=gate, access=access)


async def test_approve_by_username_case_insensitive(env):
    _, _, access, log = env
    out = await _tools(env).aprobar_acceso("@juan")
    assert "ya tiene acceso" in out
    assert (await access.get("7")).status == APPROVED
    assert await log.receipts("t1") == ["✅ Aprobé a @Juan (id 7)"]
    assert await log.outbox_pending() == [(1, "7", "¡Ya tienes acceso! Escríbeme cuando quieras.", 0)]


async def test_approve_by_code(env):
    out = await _tools(env).aprobar_acceso("k7qm-x3pa")
    assert "ya tiene acceso" in out


async def test_approve_unknown(env):
    assert "No encontré" in await _tools(env).aprobar_acceso("@nadie")


async def test_revoke_by_username_cancels_items(env):
    _, schedule, access, log = env
    item = await schedule.add("8", "8", REMINDER, "x", NOW, None)
    out = await _tools(env).revocar_acceso("@Pedro")
    assert "revocado" in out.lower()
    assert await access.get("8") is None
    assert (await schedule.get(item)).status == CANCELLED
    assert await log.receipts("t1") == ["⛔ Revoqué a @pedro (id 8)"]


async def test_ver_accesos(env):
    out = await _tools(env).ver_accesos()
    assert "Juan" in out and "pedro" in out


async def test_message_to_approved_user_is_signed(env):
    _, _, _, log = env
    out = await _tools(env).enviar_mensaje("@PEDRO", "la reunión es a las 5")
    assert out == "📨 Enviado a @pedro (id 8)"
    assert await log.outbox_pending() == [
        (1, "8", "📨 De Gabriel (vía Ari): la reunión es a las 5", 0)]
    assert await log.receipts("t1") == [out]


async def test_message_only_to_approved_users(env):
    assert "No encontré" in await _tools(env).enviar_mensaje("@juan", "hola")  # pending
    assert "No encontré" in await _tools(env).enviar_mensaje("999", "hola")
    assert "texto" in await _tools(env).enviar_mensaje("@pedro", "")


async def test_ambiguous_username(env):
    _, _, access, _ = env
    await access.create_pending("9", "PEDRO", "ABCDEFGH")
    await access.set_status("9", APPROVED)
    assert "varios" in await _tools(env).enviar_mensaje("@pedro", "hola")
    assert "📨 Enviado" in await _tools(env).enviar_mensaje("9", "hola")  # by id works


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_admin_and_messages_denied(env, owner, context):
    t = _tools(env, user=OWNER if owner else "8", owner=owner, context=context)
    assert await t.aprobar_acceso("@juan") == DENIED
    assert await t.revocar_acceso("@pedro") == DENIED
    assert await t.enviar_mensaje("@pedro", "hola") == DENIED


async def test_heartbeat_owner_can_list_accesses(env):
    assert "Juan" in await _tools(env, context=HEARTBEAT).ver_accesos()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_ari_tools_admin.py -q`
Expected: FAIL (`AttributeError: 'AriTools' object has no attribute 'aprobar_acceso'`).

- [ ] **Step 3: Implement**

Add to `src/ari/application/ari_tools.py` imports:
```python
from ari.application.access.gate import normalize_code
from ari.domain.access.entities import APPROVED, PENDING
```
Add module helpers:
```python
def _who(rec) -> str:
    return f"@{rec.username} (id {rec.user_id})" if rec.username else f"id {rec.user_id}"


def _match(records, arg: str):
    """Records matching '@username' (case-insensitive) or a numeric id."""
    target = (arg or "").strip()
    if target.startswith("@"):
        name = target[1:].lower()
        return [r for r in records if (r.username or "").lower() == name]
    return [r for r in records if r.user_id == target]
```
Add methods to `AriTools`:
```python
    # ---- owner administration --------------------------------------------

    async def _resolve(self, arg: str, statuses: set[str]):
        records = [r for r in await self._access.list_all() if r.status in statuses]
        found = _match(records, arg)
        if not found:
            return None, f"No encontré a {arg or 'ese usuario'}."
        if len(found) > 1:
            return None, f"Hay varios usuarios que coinciden con {arg}; usa su id."
        return found[0], None

    async def _deliver(self, result) -> None:
        for chat_id, text in result.notifications:
            await self._log.outbox_add(chat_id, text)

    async def aprobar_acceso(self, codigo_o_usuario: str) -> str:
        if not self._allowed("aprobar_acceso"):
            return DENIED
        arg = (codigo_o_usuario or "").strip()
        if arg.startswith("@"):
            rec, error = await self._resolve(arg, {PENDING})
            if error:
                return f"No encontré una solicitud pendiente de {arg}."
            code = rec.code
        else:
            code = normalize_code(arg)
        result = await self._gate.admin_command(f"/aprobar {code}", self._a.user_id)
        await self._deliver(result)
        if result.notifications:  # only a real approval notifies the user
            rec = await self._access.find_by_code(code)
            await self._receipt(f"✅ Aprobé a {_who(rec)}")
        return result.reply

    async def revocar_acceso(self, usuario: str) -> str:
        if not self._allowed("revocar_acceso"):
            return DENIED
        rec, error = await self._resolve(usuario, {PENDING, APPROVED})
        if error:
            return error
        result = await self._gate.admin_command(f"/revocar {rec.user_id}", self._a.user_id)
        await self._receipt(f"⛔ Revoqué a {_who(rec)}")
        return result.reply

    async def ver_accesos(self) -> str:
        if not self._allowed("ver_accesos"):
            return DENIED
        return (await self._gate.admin_command("/accesos", self._a.user_id)).reply

    async def enviar_mensaje(self, destinatario: str, texto: str) -> str:
        if not self._allowed("enviar_mensaje"):
            return DENIED
        texto = (texto or "").strip()
        if not texto or len(texto) > 1000:
            return "No pude enviarlo: el texto debe tener entre 1 y 1000 caracteres."
        rec, error = await self._resolve(destinatario, {APPROVED})
        if error:
            return error
        await self._log.outbox_add(rec.user_id,
                                   f"📨 De {self._a.name or 'tu contacto'} (vía Ari): {texto}")
        return await self._receipt(f"📨 Enviado a {_who(rec)}")
```
Note: `aprobar_acceso` by code relies on `AccessGate._approve` (it returns notifications only when it actually approved); for an unknown `@username` the "No encontré una solicitud pendiente" message is returned without calling the gate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/application/test_ari_tools_admin.py tests/application/test_ari_tools_agenda.py -q` then `python -m pytest -m "not slow" -q`
Expected: PASS. (`test_approve_unknown` passes because `@nadie` resolves to nothing → "No encontré una solicitud pendiente de @nadie.")

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_admin.py
git commit -m "feat: owner admin and signed messages as Ari tools"
```

---

### Task 6: The MCP server process

**Files:**
- Create: `src/ari/mcp_server/__init__.py`
- Create: `src/ari/mcp_server/server.py`
- Create: `src/ari/mcp_server/__main__.py`
- Test: `tests/infrastructure/test_mcp_server.py`

**Interfaces:**
- Consumes: `AriTools`, `Actor`, `ARI_TOOLS`; `open_existing`; stores; `AccessGate`.
- Produces:
  - `actor_from_env(env: Mapping[str, str]) -> Actor` (raises `RuntimeError` naming the missing variable).
  - `build_server(get_tools: Callable[[], Awaitable[AriTools]]) -> MCPServer` registering exactly the 10 tools (names = `ARI_TOOLS`).
  - `python -m ari.mcp_server` runs it on stdio, building `AriTools` lazily from the env on the first tool call.

- [ ] **Step 1: Write the failing tests**

`tests/infrastructure/test_mcp_server.py`:
```python
import asyncio
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import Actor, AriTools
from ari.domain.tools.ari_permissions import ARI_TOOLS, CHAT
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.mcp_server.server import actor_from_env, build_server

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
ENV = {"ARI_ACTOR_ID": "42", "ARI_ACTOR_CHAT": "42", "ARI_ACTOR_NAME": "Gabriel",
       "ARI_ROLE": "owner", "ARI_CONTEXT": "chat", "ARI_TURN_ID": "t1"}


def test_actor_from_env():
    assert actor_from_env(ENV) == Actor("42", "42", "Gabriel", True, CHAT, "t1")
    assert actor_from_env({**ENV, "ARI_ROLE": "user"}).is_owner is False
    with pytest.raises(RuntimeError, match="ARI_TURN_ID"):
        actor_from_env({k: v for k, v in ENV.items() if k != "ARI_TURN_ID"})


async def test_server_registers_exactly_the_catalogue_and_calls_tools():
    conn = await connect(":memory:", embedding_dim=4)
    tools = AriTools(actor_from_env(ENV), schedule=SqliteScheduleStore(conn),
                     memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                     turn_log=SqliteTurnLog(conn), tz=TZ, max_items=20, clock=lambda: NOW)

    async def get_tools():
        return tools

    server = build_server(get_tools)
    try:
        assert {t.name for t in await server.list_tools()} == set(ARI_TOOLS)
        result = await server.call_tool("recordar_dato", {"clave": "color", "valor": "azul"})
        assert result.content[0].text == "🧠 Guardé: color = azul"
    finally:
        await conn.close()


async def test_stdio_handshake_lists_tools_without_actor_env():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "ari.mcp_server",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    async def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def read_id(wanted):
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
            assert line, (await proc.stderr.read()).decode(errors="replace")[-2000:]
            msg = json.loads(line)
            if msg.get("id") == wanted:
                return msg

    try:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}}})
        assert "result" in await read_id(1)
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = await read_id(2)
        assert {t["name"] for t in listed["result"]["tools"]} == set(ARI_TOOLS)
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/infrastructure/test_mcp_server.py -q`
Expected: FAIL (`ModuleNotFoundError: ari.mcp_server`).

- [ ] **Step 3: Implement**

`src/ari/mcp_server/__init__.py`: empty.

`src/ari/mcp_server/server.py`:
```python
"""Ari's own MCP server (stdio). Launched by the Claude CLI once per turn; the
actor, role and context come only from the env Ari wrote in the turn config."""
from collections.abc import Awaitable, Callable, Mapping

from mcp.server.mcpserver import MCPServer

from ari.application.ari_tools import Actor, AriTools

_REQUIRED = ("ARI_ACTOR_ID", "ARI_ACTOR_CHAT", "ARI_ROLE", "ARI_CONTEXT", "ARI_TURN_ID")


def actor_from_env(env: Mapping[str, str]) -> Actor:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(f"Ari MCP server started without {', '.join(missing)}")
    return Actor(env["ARI_ACTOR_ID"], env["ARI_ACTOR_CHAT"], env.get("ARI_ACTOR_NAME", ""),
                 env["ARI_ROLE"] == "owner", env["ARI_CONTEXT"], env["ARI_TURN_ID"])


def build_server(get_tools: Callable[[], Awaitable[AriTools]]) -> MCPServer:
    server = MCPServer("ari")

    @server.tool()
    async def agendar(tipo: str, texto: str, at: str | None = None,
                      cron: str | None = None) -> str:
        """Agenda un recordatorio (se envía el texto a la hora indicada) o una tarea
        (a esa hora ejecutas la instrucción). tipo: "recordatorio" o "tarea". Usa
        at (fecha y hora local ISO, p. ej. 2026-09-26T09:00) para una vez, o cron
        (5 campos, hora local, mínimo cada 1 hora) para repetir."""
        return await (await get_tools()).agendar(tipo, texto, at, cron)

    @server.tool()
    async def listar_agenda() -> str:
        """Lista los recordatorios y tareas activos del usuario con su #número."""
        return await (await get_tools()).listar_agenda()

    @server.tool()
    async def cancelar(id: int) -> str:
        """Cancela un recordatorio o tarea del usuario por su #número."""
        return await (await get_tools()).cancelar(id)

    @server.tool()
    async def recordar_dato(clave: str, valor: str) -> str:
        """Guarda o corrige un dato estable del usuario (p. ej. clave "hija", valor "Ana")."""
        return await (await get_tools()).recordar_dato(clave, valor)

    @server.tool()
    async def olvidar_dato(clave: str) -> str:
        """Borra un dato guardado del usuario por su clave."""
        return await (await get_tools()).olvidar_dato(clave)

    @server.tool()
    async def ver_datos() -> str:
        """Muestra los datos guardados del usuario."""
        return await (await get_tools()).ver_datos()

    @server.tool()
    async def aprobar_acceso(codigo_o_usuario: str) -> str:
        """(Solo el creador) Aprueba una solicitud de acceso por su código o por @usuario."""
        return await (await get_tools()).aprobar_acceso(codigo_o_usuario)

    @server.tool()
    async def revocar_acceso(usuario: str) -> str:
        """(Solo el creador) Quita el acceso a un usuario por @usuario o id."""
        return await (await get_tools()).revocar_acceso(usuario)

    @server.tool()
    async def ver_accesos() -> str:
        """(Solo el creador) Lista las solicitudes pendientes y los usuarios aprobados."""
        return await (await get_tools()).ver_accesos()

    @server.tool()
    async def enviar_mensaje(destinatario: str, texto: str) -> str:
        """(Solo el creador) Envía un mensaje firmado a un usuario aprobado, por @usuario o id."""
        return await (await get_tools()).enviar_mensaje(destinatario, texto)

    return server
```

`src/ari/mcp_server/__main__.py`:
```python
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ari.application.access.gate import AccessGate
from ari.application.ari_tools import AriTools
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import open_existing
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.mcp_server.server import actor_from_env, build_server

_tools: AriTools | None = None


async def _get_tools() -> AriTools:
    """Built on the first tool call, so a bare handshake needs no actor env."""
    global _tools
    if _tools is None:
        env = os.environ
        conn = await open_existing(env["ARI_DB_PATH"])
        schedule, access = SqliteScheduleStore(conn), SqliteAccessStore(conn)
        owners = {o.strip() for o in env.get("ARI_OWNER_IDS", "").split(",") if o.strip()}
        _tools = AriTools(
            actor_from_env(env), schedule=schedule,
            memory=SqliteMemoryAdapter(conn, embedding_dim=1),  # facts only, no vectors
            turn_log=SqliteTurnLog(conn), tz=ZoneInfo(env.get("ARI_TIMEZONE", "UTC")),
            max_items=int(env.get("ARI_MAX_ITEMS", "20")),
            clock=lambda: datetime.now(timezone.utc),
            gate=AccessGate(access, owners, on_revoke=schedule.cancel_user), access=access)
    return _tools


def main() -> None:
    build_server(_get_tools).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/infrastructure/test_mcp_server.py -q` then `python -m pytest -m "not slow" -q`
Expected: PASS. If the stdio handshake reports a protocol-version mismatch, read the server's `initialize` response for its supported version and use it in the test — do not skip the test.

- [ ] **Step 5: Commit**

```bash
git add src/ari/mcp_server tests/infrastructure/test_mcp_server.py
git commit -m "feat: Ari's own MCP server over stdio"
```

---

### Task 7: Per-turn tool configuration

**Files:**
- Create: `src/ari/infrastructure/tools/files.py`
- Create: `src/ari/infrastructure/tools/turn_config.py`
- Modify: `src/ari/infrastructure/tools/mcp_registry.py` (`resolved()`, use `atomic_write_json`)
- Modify: `src/ari/domain/tools/toolset.py` (`Turn`)
- Modify: `src/ari/application/tools/tool_policy.py` (`AriServerSpec`, `turn()`, `no_turn()`)
- Test: `tests/infrastructure/test_turn_config.py`, `tests/application/test_tool_policy_turn.py`

**Interfaces:**
- Consumes: `allowed_ari_tools`, `ARI_SERVER`, `CHAT` (Task 1).
- Produces:
  - `atomic_write_json(path: str, obj) -> None` (0600 from creation, temp + `os.replace`, raises `OSError`).
  - `TurnConfigWriter(out_dir)`: `write(servers: dict) -> str | None` (`turn-<hex>.json`), `remove(path) -> None`.
  - `McpRegistry.resolved(is_owner) -> dict[str, dict]`.
  - `@dataclass(frozen=True) Turn(toolset: Toolset, view: ToolsView, turn_id: str)`.
  - `@dataclass(frozen=True) AriServerSpec(command: str, args: tuple[str, ...], base_env: dict)`.
  - `ToolPolicy(registry, is_owner, ari: AriServerSpec | None = None, writer=None)`; `ToolPolicy.turn(user_id, context=CHAT, actor_name="", chat_id=None)` async context manager yielding `Turn`; `no_turn()` async context manager yielding `None`.

- [ ] **Step 1: Write the failing tests**

`tests/infrastructure/test_turn_config.py`:
```python
import json
import os

from ari.infrastructure.tools.turn_config import TurnConfigWriter


def test_write_and_remove(tmp_path):
    w = TurnConfigWriter(str(tmp_path / "mcp"))
    path = w.write({"ari": {"command": "python", "env": {"ARI_TURN_ID": "t1"}}})
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["mcpServers"]["ari"]["env"]["ARI_TURN_ID"] == "t1"
    assert os.path.basename(path).startswith("turn-")
    assert [p for p in os.listdir(tmp_path / "mcp") if p.endswith(".tmp")] == []
    if os.name != "nt":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    w.remove(path)
    assert not os.path.exists(path)
    w.remove(path)  # idempotent


def test_write_failure_returns_none(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert TurnConfigWriter(str(blocker)).write({"a": {}}) is None  # out_dir is a file
```

`tests/application/test_tool_policy_turn.py`:
```python
import json

import pytest

from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy, no_turn
from ari.domain.tools.ari_permissions import CHAT, TASK
from ari.infrastructure.tools.turn_config import TurnConfigWriter


class FakeRegistry:
    def __init__(self, owner=None):
        self._owner = owner or {}

    def resolved(self, is_owner):
        return dict(self._owner) if is_owner else {}

    def servers_for(self, is_owner):
        names = tuple(self.resolved(is_owner))
        return names, None

    def descriptions(self, is_owner):
        return [(n, "desc") for n in self.resolved(is_owner)]


SPEC = AriServerSpec("py", ("-m", "ari.mcp_server"), {"ARI_DB_PATH": "/db"})


def _policy(tmp_path, owner_servers=None):
    return ToolPolicy(FakeRegistry(owner_servers), is_owner=lambda uid: uid == "42",
                      ari=SPEC, writer=TurnConfigWriter(str(tmp_path)))


async def test_owner_chat_turn(tmp_path):
    policy = _policy(tmp_path, {"google": {"command": "uvx"}})
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        t = turn.toolset
        assert "ToolSearch" in t.builtin_tools
        assert "mcp__google" in t.allowed_tools
        assert "mcp__ari__enviar_mensaje" in t.allowed_tools
        with open(t.mcp_config_path, encoding="utf-8") as f:
            servers = json.load(f)["mcpServers"]
        env = servers["ari"]["env"]
        assert servers["ari"]["command"] == "py" and servers["ari"]["args"] == ["-m", "ari.mcp_server"]
        assert env == {"ARI_DB_PATH": "/db", "ARI_ACTOR_ID": "42", "ARI_ACTOR_CHAT": "42",
                       "ARI_ACTOR_NAME": "Gabriel", "ARI_ROLE": "owner", "ARI_CONTEXT": "chat",
                       "ARI_TURN_ID": turn.turn_id}
        path = t.mcp_config_path
    import os
    assert not os.path.exists(path)


async def test_user_task_turn_is_read_only_and_has_no_owner_servers(tmp_path):
    policy = _policy(tmp_path, {"google": {"command": "uvx"}})
    async with policy.turn("7", TASK) as turn:
        allowed = turn.toolset.allowed_tools
        assert "mcp__google" not in allowed
        assert [a for a in allowed if a.startswith("mcp__ari__")] == [
            "mcp__ari__listar_agenda", "mcp__ari__ver_datos"]


async def test_config_deleted_even_when_the_body_raises(tmp_path):
    policy = _policy(tmp_path)
    with pytest.raises(RuntimeError):
        async with policy.turn("42") as turn:
            path = turn.toolset.mcp_config_path
            raise RuntimeError("llm exploded")
    import os
    assert not os.path.exists(path)


async def test_without_ari_spec_falls_back_to_3a_behaviour(tmp_path):
    policy = ToolPolicy(FakeRegistry(), is_owner=lambda uid: False)
    async with policy.turn("7") as turn:
        assert turn.toolset.allowed_tools == ("WebSearch", "WebFetch")
        assert turn.turn_id


async def test_writer_failure_falls_back_to_web_only(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    policy = ToolPolicy(FakeRegistry(), is_owner=lambda uid: True, ari=SPEC,
                        writer=TurnConfigWriter(str(blocker)))
    async with policy.turn("42") as turn:
        assert turn.toolset.allowed_tools == ("WebSearch", "WebFetch")


async def test_no_turn_yields_none():
    async with no_turn() as turn:
        assert turn is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/infrastructure/test_turn_config.py tests/application/test_tool_policy_turn.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`src/ari/infrastructure/tools/files.py`:
```python
import json
import os


def atomic_write_json(path: str, obj) -> None:
    """Write JSON readable only by the owner from the first byte (secrets), and
    atomically (a reader never sees a half-written file). Raises OSError."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
```

In `src/ari/infrastructure/tools/mcp_registry.py`:
- import `from ari.infrastructure.tools.files import atomic_write_json`;
- in `_write`, replace the block from `tmp = path + ".tmp"` through the inner `raise` with:
```python
        try:
            atomic_write_json(path, {"mcpServers": {n: self._resolved[n] for n in names}})
        except OSError as exc:
            log.error("could not write %s: %s", path, exc)
            return None, False
        return path, True
```
- add the public method (next to `servers_for`):
```python
    def resolved(self, is_owner: bool) -> dict[str, dict]:
        """CLI-ready configs of the role's usable servers (secrets resolved)."""
        self._refresh()
        return {n: dict(self._resolved[n]) for n in self._names(is_owner)}
```

`src/ari/infrastructure/tools/turn_config.py`:
```python
import logging
import os
import uuid

from ari.infrastructure.tools.files import atomic_write_json

log = logging.getLogger("ari.mcp")


class TurnConfigWriter:
    """One MCP config file per Claude call (it carries that turn's identity);
    removed as soon as the call ends."""

    def __init__(self, out_dir: str):
        self._out_dir = out_dir

    def write(self, servers: dict) -> str | None:
        path = os.path.join(self._out_dir, f"turn-{uuid.uuid4().hex}.json")
        try:
            atomic_write_json(path, {"mcpServers": servers})
        except OSError as exc:
            log.error("could not write turn config %s: %s", path, exc)
            return None
        return path

    def remove(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.error("could not remove turn config %s: %s", path, exc)
```

`src/ari/domain/tools/toolset.py` — append:
```python
@dataclass(frozen=True)
class Turn:
    """Tools for one Claude call plus the id Ari's MCP server tags receipts with."""
    toolset: Toolset
    view: ToolsView
    turn_id: str
```

`src/ari/application/tools/tool_policy.py` — add imports and code:
```python
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from ari.domain.tools.ari_permissions import ARI_SERVER, CHAT, allowed_ari_tools
from ari.domain.tools.toolset import Turn


@dataclass(frozen=True)
class AriServerSpec:
    command: str
    args: tuple[str, ...]
    base_env: dict = field(default_factory=dict)


@asynccontextmanager
async def no_turn():
    yield None
```
`ToolPolicy.__init__(self, registry, is_owner, ari: AriServerSpec | None = None, writer=None)` storing `self._ari, self._writer`, and the method:
```python
    @asynccontextmanager
    async def turn(self, user_id: str, context: str = CHAT, actor_name: str = "",
                   chat_id: str | None = None):
        """Tools for one call. With Ari's server configured, writes a per-turn MCP
        config whose ``ari`` entry carries who is acting — the model can't change
        it — and always deletes it when the call ends."""
        turn_id = uuid.uuid4().hex
        view = self.view(user_id)
        if self._ari is None or self._writer is None:
            yield Turn(self.for_user(user_id), view, turn_id)
            return
        owner = self._is_owner(user_id)
        servers = self._registry.resolved(owner)
        servers[ARI_SERVER] = {
            "command": self._ari.command, "args": list(self._ari.args),
            "env": {**self._ari.base_env, "ARI_ACTOR_ID": user_id,
                    "ARI_ACTOR_CHAT": chat_id or user_id, "ARI_ACTOR_NAME": actor_name,
                    "ARI_ROLE": "owner" if owner else "user", "ARI_CONTEXT": context,
                    "ARI_TURN_ID": turn_id}}
        path = self._writer.write(servers)
        if path is None:  # can't write the config: web only, never a stale file
            yield Turn(Toolset(WEB_TOOLS, WEB_TOOLS), view, turn_id)
            return
        builtin = (*WEB_TOOLS, TOOL_SEARCH)
        allowed = (*builtin,
                   *(f"mcp__{n}" for n in servers if n != ARI_SERVER),
                   *(f"mcp__{ARI_SERVER}__{t}" for t in allowed_ari_tools(owner, context)))
        try:
            yield Turn(Toolset(builtin, allowed, path), view, turn_id)
        finally:
            self._writer.remove(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/infrastructure/test_turn_config.py tests/application/test_tool_policy_turn.py tests/infrastructure/test_mcp_registry.py tests/application/test_tool_policy.py -q` then `python -m pytest -m "not slow" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/tools src/ari/domain/tools/toolset.py src/ari/application/tools/tool_policy.py tests/infrastructure/test_turn_config.py tests/application/test_tool_policy_turn.py
git commit -m "feat: per-turn MCP config carrying the actor for Ari's server"
```

---

### Task 8: Turn integration, outbox flusher, wiring, capabilities, docs

**Files:**
- Create: `src/ari/application/outbox.py`
- Modify: `src/ari/application/handle_message.py`
- Modify: `src/ari/application/schedule/heartbeat.py`
- Modify: `src/ari/main.py`
- Modify: `src/ari/domain/agent/capabilities.py`
- Modify: `README.md`
- Test: `tests/application/test_outbox.py`, `tests/application/test_handle_message.py`, `tests/application/test_heartbeat.py`, `tests/test_main_access.py` (append)

**Interfaces:**
- Consumes: `ToolPolicy.turn`, `no_turn`, `Turn` (Task 7); `SqliteTurnLog` (Task 2); `CHAT`/`TASK`/`HEARTBEAT`.
- Produces:
  - `OutboxFlusher(turn_log, send, clock, max_attempts=3)`; `await flusher()` — `send(chat_id, text) -> bool`; serialized by an internal lock; purges receipts older than 1 day.
  - `HandleMessage(..., tools=None, turn_log=None, after_turn=None)`.
  - `main._send_checked(bot, chat_id, text) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/application/test_outbox.py`:
```python
import asyncio
from datetime import datetime, timezone

import pytest

from ari.application.outbox import OutboxFlusher
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def log():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteTurnLog(conn)
    await conn.close()


async def test_sends_once_even_when_flushed_concurrently(log):
    await log.outbox_add("7", "hola")
    sent = []

    async def send(chat_id, text):
        await asyncio.sleep(0.05)
        sent.append((chat_id, text))
        return True

    flusher = OutboxFlusher(log, send, clock=lambda: NOW)
    await asyncio.gather(flusher(), flusher())
    assert sent == [("7", "hola")]
    assert await log.outbox_pending() == []


async def test_failed_send_retries_then_drops(log):
    await log.outbox_add("7", "hola")

    async def send(chat_id, text):
        return False

    flusher = OutboxFlusher(log, send, clock=lambda: NOW, max_attempts=3)
    await flusher()
    await flusher()
    assert len(await log.outbox_pending()) == 1
    await flusher()
    assert await log.outbox_pending() == []
```

In `tests/application/test_handle_message.py`, replace the `_FakePolicy` class and `test_chat_uses_sender_toolset_and_view_but_maintenance_gets_none` with:
```python
from contextlib import asynccontextmanager

from ari.domain.tools.toolset import Turn


class _FakePolicy:
    def __init__(self):
        self.turns = []

    @asynccontextmanager
    async def turn(self, user_id, context="chat", actor_name="", chat_id=None):
        self.turns.append((user_id, context, actor_name, chat_id))
        yield Turn(Toolset(("WebSearch",), ("WebSearch",), f"/cfg/{user_id}.json"),
                   ToolsView(f"## Tus herramientas y conexiones\n- 🌐 web ({user_id})",
                             True, False), f"turn-{user_id}")


class _FakeTurnLog:
    def __init__(self, receipts):
        self._r = receipts

    async def receipts(self, turn_id):
        return self._r.get(turn_id, [])


async def test_chat_uses_sender_toolset_and_view_but_maintenance_gets_none():
    llm = FakeLLM(reply="hola")
    ran = []
    policy = _FakePolicy()
    handler = HandleMessage(memory=FakeMemory(), llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), tools=policy,
                            maintainer=_RecordingMaintainer(llm),
                            scheduler=lambda coro: ran.append(coro))
    await handler(IncomingMessage("u9", "c9", "busca algo", display_name="Ana"))
    for coro in ran:
        await coro
    assert policy.turns == [("u9", "chat", "Ana", "c9")]
    assert llm.toolsets[0].mcp_config_path == "/cfg/u9.json"
    assert "🌐 web (u9)" in llm.calls[0][0]
    assert llm.toolsets[1:] == [None]


async def test_receipts_appended_and_after_turn_called():
    flushed = []

    async def after_turn():
        flushed.append(True)

    handler = HandleMessage(memory=FakeMemory(), llm=FakeLLM(reply="Listo."),
                            embeddings=FakeEmbeddings(), agent=AgentService(),
                            tools=_FakePolicy(),
                            turn_log=_FakeTurnLog({"turn-u1": ["✅ Recordatorio #1: x — sáb 26/09 09:00"]}),
                            after_turn=after_turn)
    out = await handler(IncomingMessage("u1", "c1", "recuérdame x"))
    assert out.text == "Listo.\n\n✅ Recordatorio #1: x — sáb 26/09 09:00"
    assert flushed == [True]


async def test_timeout_after_a_tool_ran_still_shows_receipt_and_flushes():
    class SlowLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise LLMTimeoutError("claude timed out after 180s")

    flushed = []

    async def after_turn():
        flushed.append(True)

    from ari.application.handle_message import TIMEOUT_REPLY
    out = await HandleMessage(memory=FakeMemory(), llm=SlowLLM(), embeddings=FakeEmbeddings(),
                              agent=AgentService(), tools=_FakePolicy(),
                              turn_log=_FakeTurnLog({"turn-u1": ["🧠 Guardé: a = b"]}),
                              after_turn=after_turn)(IncomingMessage("u1", "c1", "hola"))
    assert out.text == f"{TIMEOUT_REPLY}\n\n🧠 Guardé: a = b"
    assert flushed == [True]


async def test_scheduled_run_uses_task_context():
    policy = _FakePolicy()
    await HandleMessage(memory=FakeMemory(), llm=FakeLLM(reply="ok"), embeddings=FakeEmbeddings(),
                        agent=AgentService(), tools=policy)(
        IncomingMessage("u1", "c1", "resumen"), allow_actions=False)
    assert policy.turns[0][1] == "task"
```

In `tests/application/test_heartbeat.py`, replace `_OwnerPolicy` and `test_heartbeat_uses_owner_toolset` with:
```python
from contextlib import asynccontextmanager

from ari.domain.tools.toolset import Toolset, ToolsView, Turn


class _OwnerPolicy:
    def __init__(self):
        self.contexts = []

    @asynccontextmanager
    async def turn(self, user_id, context="chat", actor_name="", chat_id=None):
        self.contexts.append(context)
        yield Turn(Toolset(("WebSearch",), ("WebSearch", "mcp__google"), "/cfg/owner.json"),
                   ToolsView("## Tus herramientas y conexiones\n- 🔌 google: Gmail", True, True),
                   "turn-hb")


async def test_heartbeat_uses_owner_turn_in_heartbeat_context(store):
    clock = Clock(DAY)
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    llm, mem, policy = FakeLLM(reply="NADA"), FakeMemory(), _OwnerPolicy()
    hb = Heartbeat(llm=llm, memory=mem, store=store, agent=AgentService(),
                   soul=lambda: "Soy Ari", checklist=lambda: "- revisa correos",
                   owners={"42"}, send=send, tz=TZ, quiet=(22, 7), interval_minutes=60,
                   clock=clock, tools=policy)
    await hb()
    clock.now += timedelta(hours=1)
    await hb()
    assert policy.contexts == ["heartbeat"]
    assert llm.toolsets[0].mcp_config_path == "/cfg/owner.json"
    assert "🔌 google: Gmail" in llm.calls[0][0]
```

Append to `tests/test_main_access.py`:
```python
async def test_send_checked_reports_success_and_failure():
    class Ok:
        def __init__(self):
            self.parts = []

        async def send_message(self, chat_id, text):
            self.parts.append(text)

    class Broken:
        async def send_message(self, chat_id, text):
            raise RuntimeError("Forbidden")

    ok = Ok()
    assert await main_mod._send_checked(ok, "7", "x" * 5000) is True
    assert [len(p) for p in ok.parts] == [4096, 904]
    assert await main_mod._send_checked(Broken(), "7", "hola") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/application/test_outbox.py tests/application/test_handle_message.py tests/application/test_heartbeat.py tests/test_main_access.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the flusher**

`src/ari/application/outbox.py`:
```python
import asyncio
import logging
from datetime import timedelta

log = logging.getLogger("ari.outbox")


class OutboxFlusher:
    """Sends what Ari's MCP server queued (messages, approval notices). Runs right
    after each turn and on every scheduler tick; a lock makes concurrent runs
    send each message once."""

    def __init__(self, turn_log, send, clock, max_attempts: int = 3,
                 keep_receipts: timedelta = timedelta(days=1)):
        self._log, self._send, self._clock = turn_log, send, clock
        self._max, self._keep = max_attempts, keep_receipts
        self._lock = asyncio.Lock()

    async def __call__(self) -> None:
        async with self._lock:
            for item_id, chat_id, text, _attempts in await self._log.outbox_pending():
                if await self._send(chat_id, text):
                    await self._log.outbox_mark_sent(item_id, self._clock())
                    continue
                if await self._log.outbox_mark_failed(item_id) >= self._max:
                    log.warning("dropping outbox message %s to %s after %s attempts",
                                item_id, chat_id, self._max)
                    await self._log.outbox_drop(item_id)
            await self._log.purge_receipts(self._clock() - self._keep)
```

- [ ] **Step 4: Implement the turn integration in `HandleMessage`**

In `src/ari/application/handle_message.py`:
- imports: `from ari.application.tools.tool_policy import no_turn` and `from ari.domain.tools.ari_permissions import CHAT, TASK`.
- `__init__` gains `turn_log=None, after_turn=None` → `self._turn_log = turn_log  # SqliteTurnLog | None` and `self._after_turn = after_turn  # async () -> None, e.g. OutboxFlusher`.
- Replace everything from `toolset = self._tools.for_user(...)` down to (and including) the `reply, _legacy = extract_actions(reply)` line with:
```python
        context = CHAT if allow_actions else TASK
        turn_cm = (self._tools.turn(incoming.user_id, context, incoming.display_name,
                                    incoming.chat_id) if self._tools else no_turn())
        timed_out = False
        try:
            async with turn_cm as turn:
                system = self._agent.build_prompt(
                    facts, summary, recalls, soul=self._soul(),
                    is_owner=self._is_owner(incoming.user_id), extra=extra,
                    tools=turn.view if turn else None)
                try:
                    reply = await self._llm.complete(
                        system, history,
                        **({"toolset": turn.toolset} if turn is not None else {}))
                except LLMTimeoutError:
                    log.warning("chat call timed out for %s", incoming.user_id)
                    if not allow_actions:
                        # Scheduled task run: let RunDueItems count it as a failure.
                        raise
                    reply, timed_out = TIMEOUT_REPLY, True
            reply, _legacy = extract_actions(reply)  # stray legacy blocks stay hidden
            receipts = await self._receipts(turn)
            if receipts:
                reply = "\n\n".join(p for p in (reply, "\n".join(receipts)) if p)
            if timed_out:
                return OutgoingMessage(incoming.chat_id, reply)
        finally:
            if self._after_turn is not None:
                await self._safe(self._after_turn(), None)
```
- Add the helper method:
```python
    async def _receipts(self, turn) -> list[str]:
        if turn is None or self._turn_log is None:
            return []
        return await self._safe(self._turn_log.receipts(turn.turn_id), [])
```
(The existing code after this point — storing the assistant message, recall and maintainer scheduling, and the final `return OutgoingMessage(incoming.chat_id, reply)` — stays unchanged.)

- [ ] **Step 5: Heartbeat uses a heartbeat-context turn**

In `src/ari/application/schedule/heartbeat.py` `_beat`, replace the `view = …`, `toolset = …`, `system = …`, `history = …` and `raw = …` lines with:
```python
        turn_cm = (self._tools.turn(owner, HEARTBEAT, "", owner) if self._tools
                   else no_turn())
        async with turn_cm as turn:
            system = self._agent.build_prompt(
                await self._memory.get_facts(owner), await self._memory.get_summary(owner), [],
                soul=self._soul(), is_owner=True, extra=await self._section(owner, now, recent),
                tools=turn.view if turn else None)
            history = await self._memory.recent_messages(owner, 10)
            raw = await self._llm.complete(
                system, [*history, Message(owner, "user", _INSTRUCTION, now)],
                **({"toolset": turn.toolset} if turn is not None else {}))
```
with imports `from ari.application.tools.tool_policy import no_turn` and `from ari.domain.tools.ari_permissions import HEARTBEAT`.

- [ ] **Step 6: Wire in `main.py`**

- imports: `import sys`, `from ari.application.outbox import OutboxFlusher`, `from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy`, `from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog`, `from ari.infrastructure.tools.turn_config import TurnConfigWriter`.
- Replace `_send_quietly` with:
```python
async def _send_checked(bot, chat_id: str, text: str) -> bool:
    """Send split to Telegram's limit; True if every part went out. Never raises."""
    for part in TelegramAdapter.split_text(text):
        try:
            await bot.send_message(chat_id=int(chat_id), text=part)
        except Exception as exc:  # noqa: BLE001
            logging.warning("could not send to %s: %s", chat_id, exc)
            return False
    return True


async def _send_quietly(bot, chat_id: str, text: str) -> None:
    """Background sends (reminders, notices, heartbeat): best effort, never raise."""
    await _send_checked(bot, chat_id, text)
```
- `Components` gains `turn_log: SqliteTurnLog`.
- In `build`, create and pass:
```python
    turn_log = SqliteTurnLog(conn)
    ari_spec = AriServerSpec(sys.executable, ("-m", "ari.mcp_server"), {
        "ARI_DB_PATH": os.path.abspath(settings.db_path),
        "ARI_TIMEZONE": settings.timezone,
        "ARI_MAX_ITEMS": str(settings.max_items_per_user),
        "ARI_OWNER_IDS": ",".join(sorted(settings.owner_id_set)),
    })
    tools = ToolPolicy(registry, Authorizer(settings.owner_id_set).is_owner, ari=ari_spec,
                       writer=TurnConfigWriter(os.path.join(settings.claude_config_dir, "mcp")))
```
  (replacing the previous `tools = ToolPolicy(...)` line) and `HandleMessage(..., tools=tools, turn_log=turn_log)`; return `Components(..., tools, turn_log)`.
- In `_post_init`, after `send` is defined:
```python
        async def send_checked(chat_id: str, text: str) -> bool:
            return await _send_checked(app.bot, chat_id, text)

        flusher = OutboxFlusher(c.turn_log, send_checked, _utcnow)
        c.handler._after_turn = flusher  # flush right after every chat/task turn
```
  and add `flusher` to the scheduler jobs: `Scheduler([due, notices.tick, heartbeat, flusher])`.
- In `_dispatch`'s `chat()`, build the scoped message with the name:
```python
            scoped_inc = IncomingMessage(
                user_id=inc.user_id, chat_id=inc.chat_id, text=chat_text,
                display_name=inc.display_name)
```

(Setting `c.handler._after_turn` after construction is intentional: the flusher needs the bot, which only exists in `_post_init`.)

- [ ] **Step 7: Capabilities and docs**

In `src/ari/domain/agent/capabilities.py`: update the `recordatorios` capability summary to
`"Recordatorios y tareas programadas pedidas en lenguaje natural («recuérdame mañana a las 9…», «cada lunes a las 8 resúmeme…»), puntuales o recurrentes, con tus herramientas agendar, listar_agenda y cancelar."`
and add, right after it:
```python
    Capability(
        summary="Memoria explícita: guardas, corriges y borras datos del usuario cuando te "
                "lo pide («recuerda que…», «olvida…», «¿qué sabes de mí?») con "
                "recordar_dato, olvidar_dato y ver_datos."),
    Capability(
        owner_only=True,
        summary="Administrar accesos conversando («aprueba a @juan», «revoca a @pedro», "
                "«¿quién pidió acceso?») con aprobar_acceso, revocar_acceso y ver_accesos."),
    Capability(
        owner_only=True,
        summary="Enviar mensajes a usuarios aprobados («avísale a @juan que…») con "
                "enviar_mensaje; llegan firmados con el nombre de tu creador."),
```
In `README.md`, add under "## Proactivity" a paragraph:
```markdown
Ari's own actions are tools of its internal MCP server (`python -m ari.mcp_server`,
started by the Claude CLI per turn): `agendar`, `listar_agenda`, `cancelar`,
`recordar_dato`, `olvidar_dato`, `ver_datos` for everyone, plus `aprobar_acceso`,
`revocar_acceso`, `ver_accesos`, `enviar_mensaje` for the owner. Scheduled tasks and
the heartbeat only get read-only tools. Every change is confirmed by a code-generated
receipt appended to Ari's reply; messages to other users are signed.
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest -m "not slow" -q` and `python -c "import ari.main"`
Expected: PASS; import OK.

- [ ] **Step 9: Commit**

```bash
git add src/ari/application/outbox.py src/ari/application/handle_message.py src/ari/application/schedule/heartbeat.py src/ari/main.py src/ari/domain/agent/capabilities.py README.md tests/application/test_outbox.py tests/application/test_handle_message.py tests/application/test_heartbeat.py tests/test_main_access.py
git commit -m "feat: run turns with Ari's MCP server; receipts, outbox, wiring"
```

---

### Task 9: Live verification

**Files:**
- Create: `tests/test_ari_tools_live.py`

**Interfaces:**
- Consumes: `ToolPolicy(…, ari=AriServerSpec, writer=TurnConfigWriter)`, `McpRegistry`, `ClaudeCodeCliAdapter`, `AgentService`, `ScheduleActions.context`, `SqliteTurnLog`, `connect`.

- [ ] **Step 1: Write the slow tests**

`tests/test_ari_tools_live.py`:
```python
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.schedule_actions import ScheduleActions
from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.tools.ari_permissions import CHAT
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.tools.mcp_registry import McpRegistry
from ari.infrastructure.tools.turn_config import TurnConfigWriter

TZ = ZoneInfo("America/Guayaquil")


async def _owner_turn(tmp_path, question):
    db = str(tmp_path / "ari.db")
    conn = await connect(db, embedding_dim=4)
    registry = McpRegistry(str(tmp_path / "none.json"), ".env", str(tmp_path / "out"))
    policy = ToolPolicy(registry, is_owner=lambda uid: True,
                        ari=AriServerSpec(sys.executable, ("-m", "ari.mcp_server"),
                                          {"ARI_DB_PATH": db, "ARI_TIMEZONE": "America/Guayaquil",
                                           "ARI_MAX_ITEMS": "20", "ARI_OWNER_IDS": "42"}),
                        writer=TurnConfigWriter(str(tmp_path / "turns")))
    s = Settings()
    llm = ClaudeCodeCliAdapter(cli_env=claude_cli_env(s.claude_oauth_token, s.claude_config_dir),
                               timeout=240)
    now = datetime.now(timezone.utc)
    context = await ScheduleActions(SqliteScheduleStore(conn), TZ, 20,
                                    clock=lambda: now).context("42")
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        system = AgentService().build_prompt([], None, [], is_owner=True, extra=context,
                                             tools=turn.view)
        reply = await llm.complete(system, [Message("42", "user", question, now)],
                                   toolset=turn.toolset)
    return conn, turn.turn_id, reply, now


@pytest.mark.slow
async def test_schedule_via_ari_tool(tmp_path):
    conn, turn_id, reply, now = await _owner_turn(tmp_path, "recuérdame en 5 minutos probar Ari")
    try:
        items = await SqliteScheduleStore(conn).list_for_user("42")
        receipts = await SqliteTurnLog(conn).receipts(turn_id)
    finally:
        await conn.close()
    assert len(items) == 1, reply
    assert timedelta(minutes=3) <= items[0].next_run_at - now <= timedelta(minutes=7)
    assert receipts and receipts[0].startswith("✅ Recordatorio #1")
    assert not os.listdir(tmp_path / "turns")  # per-turn config removed


@pytest.mark.slow
async def test_remember_fact_via_ari_tool(tmp_path):
    conn, turn_id, reply, _ = await _owner_turn(tmp_path,
                                                "recuerda que mi color favorito es azul")
    try:
        facts = await SqliteMemoryAdapter(conn, embedding_dim=4).get_facts("42")
    finally:
        await conn.close()
    assert any("azul" in f.value.lower() for f in facts), reply
```

- [ ] **Step 2: Run them**

Run: `python -m pytest tests/test_ari_tools_live.py -m slow -q`
Expected: PASS. If the model does not call the tool, read `reply` in the assertion message and adjust the hint wording in `ScheduleActions._TOOLS_HINT` or the tool docstrings in `ari/mcp_server/server.py` — never the assertions. Re-run up to 3 times to judge stability and report each run.

- [ ] **Step 3: Full suite**

Run: `python -m pytest -q`
Expected: PASS (MySQL/Google live tests may skip).

- [ ] **Step 4: Commit**

```bash
git add tests/test_ari_tools_live.py
git commit -m "test: live checks for Ari's own MCP tools"
```

- [ ] **Step 5: Manual acceptance (owner, Telegram) after `/restart`**

1. "recuérdame en 2 minutos probar Ari" → receipt; ⏰ arrives; "/recordatorios" lists; "cancela el N" → receipt.
2. "recuerda que mi color favorito es azul" → "🧠 Guardé…"; "olvida mi color favorito" → "🧹 Olvidé…"; "¿qué sabes de mí?".
3. "aprueba a @juan" (pending user) → "✅ Aprobé a …"; Juan receives the access notice.
4. "avísale a @juan que la reunión es a las 5" → Juan gets "📨 De <tu nombre> (vía Ari): …"; you see "📨 Enviado a …".
5. From an approved non-owner: "aprueba a @x" / "avísale a @y …" → Ari can't.
6. A scheduled task whose instruction asks to schedule or message → it can't.
