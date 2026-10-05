# Per-user workspace (`.ari/`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Ari user a path-jailed `.ari/<user_id>/` workspace where they can create/read/list/delete files and run SQLite queries; the owner additionally gets arbitrary command execution scoped to their own workspace.

**Architecture:** A new infra layer (`UserWorkspace`/`Workspaces` for jailed file ops, `SqliteSandbox` for hardened SQL) is exposed through six new `AriTools` methods and six matching `@tool` wrappers on the existing `ari` MCP server. Role/context gating reuses `allowed_ari_tools`: file + SQL tools are available to everyone in `CHAT`; `ejecutar` is owner-only. The owner's command execution reuses the existing `ShellRunner` with its `cwd` jailed to the owner's workspace.

**Tech Stack:** Python ≥3.11, `uv`, pydantic-settings, stdlib `sqlite3` (for the sandbox) + the project's async sqlite connection (unchanged), pytest + pytest-asyncio (`asyncio_mode = "auto"`), MCP server (`ari.mcp_server`).

**Spec:** `docs/superpowers/specs/2026-10-04-ari-user-workspace-design.md`

## Global Constraints

- Python floor: `>=3.11` (`pyproject.toml`). No new third-party dependencies.
- Tests run under pytest-asyncio `asyncio_mode = "auto"` — bare `async def test_*`, no decorator needed.
- Tool identifiers and user-facing tool docstrings/strings are **Spanish**, matching the existing convention (`agendar`, `recordar_dato`). Internal code, comments, and this plan are English.
- Commit messages: Conventional Commits, **no `Co-Authored-By` / no AI attribution** (user rule overrides the harness default).
- Work on a feature branch (we are on `main`): before Task 1 run `git checkout -b feat/user-workspace`.
- Workspace file + SQL tools are **CHAT-only** (never `TASK`/`HEARTBEAT`). `ejecutar` is **owner + CHAT only**.
- Default limits: file ≤ 1 MiB (1_048_576 bytes); SQL statement timeout 5 s, result cap 1000 rows; command timeout 60 s, stdout/stderr cap 65536 chars each.

## Review Focus

- **Path escape** via `..`, absolute paths, or a symlink inside the workspace pointing outside → every file/DB op must raise and never touch files outside the user root. (pinned in Task 1)
- **Dangerous SQL** (`ATTACH`/`DETACH`, `load_extension`, multiple statements) used to reach other files or load code → denied. (pinned in Task 2)
- **`ejecutar` reached by a non-owner** or from `TASK`/`HEARTBEAT` → must be unexposed and return `DENIED`. (pinned in Task 3 and Task 6)
- **Oversized input/output** (file write larger than the cap, huge command output) → capped, never unbounded memory/disk. (pinned in Task 1 and Task 6)
- **Hostile `user_id`** containing path separators or `..` → sanitized to one safe segment so user A can never reach user B's workspace. (pinned in Task 1)

---

### Task 1: `UserWorkspace` + `Workspaces` (jailed file ops)

**Files:**
- Create: `src/ari/infrastructure/workspace/__init__.py` (empty)
- Create: `src/ari/infrastructure/workspace/user_workspace.py`
- Test: `tests/infrastructure/test_user_workspace.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces:
  - `class UserWorkspace(base_dir: str, user_id: str, *, max_file_bytes: int = 1_048_576)` with:
    - `root: str` (property) — the user's jail root
    - `ensure() -> str` — create root if missing, return it
    - `resolve(rel: str) -> str` — realpath inside root or raise `ValueError`
    - `write_text(rel: str, content: str) -> str` — returns the path relative to root
    - `read_text(rel: str) -> str`
    - `list(rel: str = ".") -> list[str]` — names, dirs suffixed with `/`
    - `delete(rel: str) -> None`
  - `class Workspaces(base_dir: str, *, max_file_bytes: int = 1_048_576)` with `for_user(user_id: str) -> UserWorkspace`

- [ ] **Step 1: Write the failing tests**

```python
# tests/infrastructure/test_user_workspace.py
import os

import pytest

from ari.infrastructure.workspace.user_workspace import UserWorkspace, Workspaces


def test_write_read_list_delete_roundtrip(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    rel = ws.write_text("notas/plan.txt", "hola")
    assert rel == os.path.join("notas", "plan.txt")
    assert ws.read_text("notas/plan.txt") == "hola"
    assert ws.list("notas") == ["plan.txt"]
    ws.delete("notas/plan.txt")
    with pytest.raises(FileNotFoundError):
        ws.read_text("notas/plan.txt")


def test_rejects_parent_traversal_and_absolute(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    with pytest.raises(ValueError):
        ws.resolve("../escape.txt")
    with pytest.raises(ValueError):
        ws.resolve("/etc/passwd")


def test_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    ws = UserWorkspace(str(tmp_path), "42")
    ws.ensure()
    os.symlink(str(outside), os.path.join(ws.root, "link"), target_is_directory=True)
    with pytest.raises(ValueError):
        ws.resolve("link/secret.txt")


def test_file_size_cap(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42", max_file_bytes=4)
    with pytest.raises(ValueError):
        ws.write_text("big.txt", "toolong")


def test_user_ids_are_sanitized_and_isolated(tmp_path):
    a = Workspaces(str(tmp_path)).for_user("../../etc")
    b = Workspaces(str(tmp_path)).for_user("99")
    a.ensure(); b.ensure()
    base = os.path.realpath(str(tmp_path))
    assert a.root.startswith(base + os.sep)
    assert os.path.dirname(a.root) == base  # one segment only, no traversal
    assert a.root != b.root
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/test_user_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: ari.infrastructure.workspace.user_workspace`

- [ ] **Step 3: Write the implementation**

```python
# src/ari/infrastructure/workspace/user_workspace.py
import os
import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_id(user_id: str) -> str:
    cleaned = _UNSAFE.sub("_", (user_id or "").strip())
    # Never let a sanitized id become empty, ".", or ".." — all would break the jail.
    return cleaned if cleaned not in ("", ".", "..") else "_"


class UserWorkspace:
    """A path-jailed per-user directory. Every op is resolved with realpath and
    rejected if it escapes the user's root (blocks '..' and symlink escapes)."""

    def __init__(self, base_dir: str, user_id: str, *, max_file_bytes: int = 1_048_576):
        base = os.path.realpath(os.path.expanduser(base_dir))
        self._root = os.path.join(base, _sanitize_id(user_id))
        self._max = max_file_bytes

    @property
    def root(self) -> str:
        return self._root

    def ensure(self) -> str:
        os.makedirs(self._root, exist_ok=True)
        return self._root

    def resolve(self, rel: str) -> str:
        raw = (rel or "").strip()
        if os.path.isabs(raw):
            raise ValueError(f"ruta absoluta no permitida: {raw}")
        path = os.path.realpath(os.path.join(self._root, raw))
        root = os.path.realpath(self._root)
        if path != root and not path.startswith(root + os.sep):
            raise ValueError(f"ruta fuera del workspace: {raw}")
        return path

    def write_text(self, rel: str, content: str) -> str:
        data = (content or "").encode("utf-8")
        if len(data) > self._max:
            raise ValueError(f"archivo demasiado grande (> {self._max} bytes)")
        self.ensure()
        path = self.resolve(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content or "")
        return os.path.relpath(path, os.path.realpath(self._root))

    def read_text(self, rel: str) -> str:
        path = self.resolve(rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(rel)
        if os.path.getsize(path) > self._max:
            raise ValueError("archivo demasiado grande para leer")
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def list(self, rel: str = ".") -> list[str]:
        path = self.resolve(rel)
        if not os.path.isdir(path):
            raise NotADirectoryError(rel)
        return [name + ("/" if os.path.isdir(os.path.join(path, name)) else "")
                for name in sorted(os.listdir(path))]

    def delete(self, rel: str) -> None:
        path = self.resolve(rel)
        if path == os.path.realpath(self._root):
            raise ValueError("no se puede borrar la raíz del workspace")
        if not os.path.isfile(path):
            raise FileNotFoundError(rel)
        os.remove(path)


class Workspaces:
    """Builds a jailed UserWorkspace per user under a shared base dir."""

    def __init__(self, base_dir: str, *, max_file_bytes: int = 1_048_576):
        self._base, self._max = base_dir, max_file_bytes

    def for_user(self, user_id: str) -> UserWorkspace:
        return UserWorkspace(self._base, user_id, max_file_bytes=self._max)
```

Also create the empty package file:

```python
# src/ari/infrastructure/workspace/__init__.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_user_workspace.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/workspace/ tests/infrastructure/test_user_workspace.py
git commit -m "feat(workspace): path-jailed per-user file workspace"
```

---

### Task 2: `SqliteSandbox` (hardened SQL runner)

**Files:**
- Create: `src/ari/infrastructure/workspace/sqlite_sandbox.py`
- Test: `tests/infrastructure/test_sqlite_sandbox.py`

**Interfaces:**
- Consumes: nothing (stdlib `sqlite3`, `asyncio`, `threading`).
- Produces:
  - `class SqlResult` with fields `columns: list[str]`, `rows: list[tuple]`, `rowcount: int`, `truncated: bool`
  - `class SqliteSandbox(*, timeout: float = 5.0, max_rows: int = 1000)` with
    `async run(db_path: str, sql: str) -> SqlResult` (raises `sqlite3.Error` on denied/invalid/timed-out SQL). `db_path` is assumed already jailed by the caller.

- [ ] **Step 1: Write the failing tests**

```python
# tests/infrastructure/test_sqlite_sandbox.py
import sqlite3

import pytest

from ari.infrastructure.workspace.sqlite_sandbox import SqliteSandbox


async def test_ddl_dml_then_select(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox()
    await sb.run(db, "CREATE TABLE t (a INTEGER, b TEXT)")
    r = await sb.run(db, "INSERT INTO t VALUES (1, 'uno'), (2, 'dos')")
    assert r.rowcount == 2 and r.columns == []
    r = await sb.run(db, "SELECT a, b FROM t ORDER BY a")
    assert r.columns == ["a", "b"]
    assert r.rows == [(1, "uno"), (2, "dos")]


async def test_attach_is_denied(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox()
    with pytest.raises(sqlite3.DatabaseError):
        await sb.run(db, f"ATTACH DATABASE '{tmp_path / 'other.sqlite'}' AS other")


async def test_row_cap_truncates(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox(max_rows=2)
    await sb.run(db, "CREATE TABLE t (a INTEGER)")
    await sb.run(db, "INSERT INTO t VALUES (1),(2),(3),(4)")
    r = await sb.run(db, "SELECT a FROM t ORDER BY a")
    assert r.rows == [(1,), (2,)] and r.truncated is True


async def test_statement_timeout(tmp_path):
    db = str(tmp_path / "data.sqlite")
    sb = SqliteSandbox(timeout=0.1)
    runaway = ("SELECT count(*) FROM ("
               "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) "
               "SELECT x FROM c)")
    with pytest.raises(sqlite3.Error):
        await sb.run(db, runaway)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/test_sqlite_sandbox.py -v`
Expected: FAIL with `ModuleNotFoundError: ari.infrastructure.workspace.sqlite_sandbox`

- [ ] **Step 3: Write the implementation**

```python
# src/ari/infrastructure/workspace/sqlite_sandbox.py
import asyncio
import sqlite3
import threading
from dataclasses import dataclass, field


@dataclass
class SqlResult:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    rowcount: int = 0
    truncated: bool = False


def _authorizer(action, arg1, arg2, db_name, source):
    # Block reaching other files (ATTACH/DETACH) and loading native code.
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg2 == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _run_sync(db_path: str, sql: str, timeout: float, max_rows: int) -> SqlResult:
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.set_authorizer(_authorizer)
    timer = threading.Timer(timeout, conn.interrupt)
    timer.start()
    try:
        cur = conn.execute(sql)  # raises on multiple statements — one op per call
        if cur.description is None:  # DDL/DML
            return SqlResult(rowcount=max(cur.rowcount, 0))
        cols = [d[0] for d in cur.description]
        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = fetched[:max_rows]
        return SqlResult(columns=cols, rows=rows, rowcount=len(rows), truncated=truncated)
    finally:
        timer.cancel()
        conn.close()


class SqliteSandbox:
    """Runs ONE SQL statement against a jailed sqlite file with an authorizer
    (no ATTACH/DETACH/load_extension), a wall-clock interrupt, and a row cap."""

    def __init__(self, *, timeout: float = 5.0, max_rows: int = 1000):
        self._timeout, self._max_rows = timeout, max_rows

    async def run(self, db_path: str, sql: str) -> SqlResult:
        return await asyncio.to_thread(
            _run_sync, db_path, (sql or "").strip(), self._timeout, self._max_rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_sqlite_sandbox.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/workspace/sqlite_sandbox.py tests/infrastructure/test_sqlite_sandbox.py
git commit -m "feat(workspace): hardened single-statement sqlite sandbox"
```

---

### Task 3: Permission matrix for the new tools

**Files:**
- Modify: `src/ari/domain/tools/ari_permissions.py:5-17`
- Test: `tests/domain/test_ari_permissions_workspace.py`

**Interfaces:**
- Consumes: `allowed_ari_tools(is_owner, context)` (unchanged signature).
- Produces: `ARI_TOOLS` now contains the six workspace tools; `_USER_CHAT` contains the five non-exec ones.

- [ ] **Step 1: Write the failing tests**

```python
# tests/domain/test_ari_permissions_workspace.py
from ari.domain.tools.ari_permissions import (
    CHAT, HEARTBEAT, TASK, allowed_ari_tools,
)

_FILES_SQL = {"escribir_archivo", "leer_archivo", "listar_archivos",
              "borrar_archivo", "consultar_sql"}


def test_owner_chat_has_everything_including_ejecutar():
    allowed = set(allowed_ari_tools(True, CHAT))
    assert _FILES_SQL <= allowed
    assert "ejecutar" in allowed


def test_user_chat_has_files_and_sql_but_not_ejecutar():
    allowed = set(allowed_ari_tools(False, CHAT))
    assert _FILES_SQL <= allowed
    assert "ejecutar" not in allowed


def test_workspace_tools_absent_outside_chat():
    for ctx in (TASK, HEARTBEAT):
        allowed = set(allowed_ari_tools(True, ctx))
        assert not (_FILES_SQL & allowed)
        assert "ejecutar" not in allowed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/domain/test_ari_permissions_workspace.py -v`
Expected: FAIL — `ejecutar`/workspace tools not yet in the permission sets.

- [ ] **Step 3: Write the implementation**

In `src/ari/domain/tools/ari_permissions.py`, extend the `ARI_TOOLS` tuple (append the six names) and `_USER_CHAT` (add the five non-exec names):

```python
ARI_TOOLS = ("agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "aprobar_acceso", "revocar_acceso", "ver_accesos",
             "enviar_mensaje", "proponer_codigo", "proponer_comando",
             "asignar_mision", "ver_misiones", "cancelar_mision",
             "pedir_credenciales",
             "ver_skills", "activar_skill", "desactivar_skill",
             "compartir", "ver_permisos", "revocar_permiso",
             "conectar_correo", "mis_correos", "olvidar_correo",
             "escribir_archivo", "leer_archivo", "listar_archivos",
             "borrar_archivo", "consultar_sql", "ejecutar")

_READ = {"listar_agenda", "ver_datos"}
_USER_CHAT = _READ | {"agendar", "cancelar", "recordar_dato", "olvidar_dato",
                      "compartir", "ver_permisos", "revocar_permiso",
                      "conectar_correo", "mis_correos", "olvidar_correo",
                      "escribir_archivo", "leer_archivo", "listar_archivos",
                      "borrar_archivo", "consultar_sql"}
```

`allowed_ari_tools` is unchanged: owner+CHAT already returns all of `ARI_TOOLS` (so `ejecutar` is included for the owner only), and `_USER_CHAT` excludes `ejecutar`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/domain/test_ari_permissions_workspace.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/tools/ari_permissions.py tests/domain/test_ari_permissions_workspace.py
git commit -m "feat(workspace): gate workspace tools (files/sql all users, ejecutar owner-only)"
```

---

### Task 4: `AriTools` file methods + constructor wiring

**Files:**
- Modify: `src/ari/application/ari_tools.py:53-67` (constructor) and add methods after `ver_datos` (~line 193)
- Test: `tests/application/test_ari_tools_workspace.py`

**Interfaces:**
- Consumes: `Workspaces.for_user` (Task 1), `allowed_ari_tools` (Task 3), the existing `self._a`, `self._allowed`, `self._receipt`.
- Produces on `AriTools`:
  - new kwargs `workspaces=None, sql_sandbox=None, runner=None`
  - `async escribir_archivo(ruta, contenido) -> str`
  - `async leer_archivo(ruta) -> str`
  - `async listar_archivos(ruta=".") -> str`
  - `async borrar_archivo(ruta) -> str`
  - private `_ws() -> UserWorkspace`

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_ari_tools_workspace.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.workspace.user_workspace import Workspaces

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env(tmp_path):
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, Workspaces(str(tmp_path)), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, *, owner=True, context=CHAT, sandbox=None, runner=None):
    conn, workspaces, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW,
                    workspaces=workspaces, sql_sandbox=sandbox, runner=runner)


async def test_write_then_read_roundtrip(env):
    t = _tools(env)
    assert await t.escribir_archivo("notas/plan.txt", "hola") == "📝 Guardé notas/plan.txt"
    assert await t.leer_archivo("notas/plan.txt") == "hola"


async def test_list_and_delete(env):
    t = _tools(env)
    await t.escribir_archivo("a.txt", "x")
    assert "a.txt" in await t.listar_archivos(".")
    assert await t.borrar_archivo("a.txt") == "🗑️ Borré a.txt"
    assert (await t.leer_archivo("a.txt")).startswith("No existe")


async def test_traversal_is_refused(env):
    t = _tools(env)
    out = await t.escribir_archivo("../escape.txt", "x")
    assert out.startswith("No pude escribir")


async def test_denied_outside_chat(env):
    out = await _tools(env, owner=True, context=TASK).escribir_archivo("a.txt", "x")
    assert out == DENIED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/application/test_ari_tools_workspace.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'workspaces'`

- [ ] **Step 3: Write the implementation**

In the `AriTools.__init__` signature append the three kwargs and store them (end of `__init__`):

```python
    def __init__(self, actor: Actor, *, schedule, memory, turn_log, tz, max_items: int,
                 clock, gate=None, access=None, coding=None, commands=None, missions=None,
                 credentials=None, skills=None, grants=None,
                 email_accounts=None, email_enroll=None,
                 workspaces=None, sql_sandbox=None, runner=None):
```

and at the end of the body:

```python
        self._ws_factory = workspaces  # Workspaces | None
        self._sql = sql_sandbox        # SqliteSandbox | None
        self._runner = runner          # ShellRunner | None
```

Add this helper and the four methods after `ver_datos` (before the cross-user grants section):

```python
    # ---- workspace (files) ------------------------------------------------

    def _ws(self):
        return self._ws_factory.for_user(self._a.user_id)

    async def escribir_archivo(self, ruta: str, contenido: str) -> str:
        if not self._allowed("escribir_archivo"):
            return DENIED
        if self._ws_factory is None:
            return "El espacio de trabajo no está disponible."
        try:
            rel = self._ws().write_text(ruta, contenido or "")
        except (ValueError, OSError) as exc:
            return f"No pude escribir el archivo: {exc}"
        return await self._receipt(f"📝 Guardé {rel}")

    async def leer_archivo(self, ruta: str) -> str:
        if not self._allowed("leer_archivo"):
            return DENIED
        if self._ws_factory is None:
            return "El espacio de trabajo no está disponible."
        try:
            return self._ws().read_text(ruta)
        except FileNotFoundError:
            return f"No existe el archivo: {ruta}"
        except (ValueError, OSError) as exc:
            return f"No pude leer el archivo: {exc}"

    async def listar_archivos(self, ruta: str = ".") -> str:
        if not self._allowed("listar_archivos"):
            return DENIED
        if self._ws_factory is None:
            return "El espacio de trabajo no está disponible."
        try:
            items = self._ws().list(ruta)
        except (FileNotFoundError, NotADirectoryError):
            return f"No existe la carpeta: {ruta}"
        except (ValueError, OSError) as exc:
            return f"No pude listar: {exc}"
        return "\n".join(items) if items else "(vacío)"

    async def borrar_archivo(self, ruta: str) -> str:
        if not self._allowed("borrar_archivo"):
            return DENIED
        if self._ws_factory is None:
            return "El espacio de trabajo no está disponible."
        try:
            self._ws().delete(ruta)
        except FileNotFoundError:
            return f"No existe el archivo: {ruta}"
        except (ValueError, OSError) as exc:
            return f"No pude borrar: {exc}"
        return await self._receipt(f"🗑️ Borré {ruta}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_ari_tools_workspace.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_workspace.py
git commit -m "feat(workspace): AriTools file methods (escribir/leer/listar/borrar)"
```

---

### Task 5: `AriTools.consultar_sql`

**Files:**
- Modify: `src/ari/application/ari_tools.py` (add `consultar_sql` after `borrar_archivo`; add module-level `_format_sql`)
- Test: `tests/application/test_ari_tools_workspace.py` (extend)

**Interfaces:**
- Consumes: `SqliteSandbox.run` (Task 2), `UserWorkspace.resolve`/`ensure` (Task 1), `self._sql` (Task 4 kwarg).
- Produces: `async consultar_sql(base, sql) -> str`.

- [ ] **Step 1: Write the failing tests** (append to `tests/application/test_ari_tools_workspace.py`)

```python
from ari.infrastructure.workspace.sqlite_sandbox import SqliteSandbox


async def test_sql_create_insert_select(env):
    t = _tools(env, sandbox=SqliteSandbox())
    assert (await t.consultar_sql("datos.sqlite", "CREATE TABLE t (a INT)")).startswith("✅")
    await t.consultar_sql("datos.sqlite", "INSERT INTO t VALUES (1),(2)")
    out = await t.consultar_sql("datos.sqlite", "SELECT a FROM t ORDER BY a")
    assert "a" in out and "1" in out and "2" in out


async def test_sql_attach_is_reported_as_error(env):
    t = _tools(env, sandbox=SqliteSandbox())
    out = await t.consultar_sql("datos.sqlite", "ATTACH DATABASE 'x.sqlite' AS x")
    assert out.startswith("Error de SQL")


async def test_sql_db_path_is_jailed(env):
    t = _tools(env, sandbox=SqliteSandbox())
    out = await t.consultar_sql("../escape.sqlite", "CREATE TABLE t (a INT)")
    assert out.startswith("Ruta de base inválida")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/application/test_ari_tools_workspace.py -k sql -v`
Expected: FAIL with `AttributeError: 'AriTools' object has no attribute 'consultar_sql'`

- [ ] **Step 3: Write the implementation**

Add the module-level helper near `DENIED` (top of `ari_tools.py`):

```python
def _format_sql(result) -> str:
    if not result.columns:
        return f"✅ Listo ({result.rowcount} fila(s) afectada(s))."
    header = " | ".join(result.columns)
    lines = [header, "-" * len(header)]
    lines += [" | ".join("" if v is None else str(v) for v in row) for row in result.rows]
    if result.truncated:
        lines.append(f"… (recortado a {len(result.rows)} filas)")
    return "\n".join(lines)
```

Add the method after `borrar_archivo`:

```python
    async def consultar_sql(self, base: str, sql: str) -> str:
        if not self._allowed("consultar_sql"):
            return DENIED
        if self._ws_factory is None or self._sql is None:
            return "El espacio de trabajo no está disponible."
        ws = self._ws()
        try:
            db_path = ws.resolve(base)
        except ValueError as exc:
            return f"Ruta de base inválida: {exc}"
        ws.ensure()  # the user root must exist before sqlite creates the file
        try:
            result = await self._sql.run(db_path, sql or "")
        except Exception as exc:  # noqa: BLE001 — sqlite errors, denied stmts, timeouts
            return f"Error de SQL: {exc}"
        return _format_sql(result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_ari_tools_workspace.py -k sql -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_workspace.py
git commit -m "feat(workspace): AriTools consultar_sql over jailed sqlite sandbox"
```

---

### Task 6: `AriTools.ejecutar` (owner-only command execution)

**Files:**
- Modify: `src/ari/application/ari_tools.py` (add `ejecutar` + module-level `_cap`)
- Test: `tests/application/test_ari_tools_workspace.py` (extend)

**Interfaces:**
- Consumes: `ShellRunner.__call__(command, cwd) -> CommandResult` (`returncode`, `timed_out`, `stdout`, `stderr`), `UserWorkspace.ensure`, `self._runner` (Task 4 kwarg), `allowed_ari_tools` owner-only gate (Task 3).
- Produces: `async ejecutar(comando) -> str`.

- [ ] **Step 1: Write the failing tests** (append to `tests/application/test_ari_tools_workspace.py`)

```python
from ari.infrastructure.command.shell_runner import ShellRunner


async def test_ejecutar_runs_in_workspace_cwd(env):
    conn, workspaces, _ = env
    t = _tools(env, runner=ShellRunner(timeout=30.0))
    out = await t.ejecutar("python -c \"import os;print(os.getcwd())\"")
    assert workspaces.for_user("42").root in out


async def test_ejecutar_denied_for_non_owner(env):
    t = _tools(env, owner=False, runner=ShellRunner(timeout=30.0))
    assert await t.ejecutar("echo hola") == DENIED


async def test_ejecutar_reports_timeout(env):
    t = _tools(env, runner=ShellRunner(timeout=0.2))
    out = await t.ejecutar("python -c \"import time;time.sleep(5)\"")
    assert "tiempo límite" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/application/test_ari_tools_workspace.py -k ejecutar -v`
Expected: FAIL with `AttributeError: 'AriTools' object has no attribute 'ejecutar'`

- [ ] **Step 3: Write the implementation**

Add the module-level helper near `_format_sql`:

```python
def _cap(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… (recortado, {len(text) - limit} caracteres más)"
```

Add the method after `consultar_sql`:

```python
    async def ejecutar(self, comando: str) -> str:
        if not self._allowed("ejecutar"):
            return DENIED
        if self._ws_factory is None or self._runner is None:
            return "El espacio de trabajo no está disponible."
        comando = (comando or "").strip()
        if not comando:
            return "No me pasaste ningún comando."
        cwd = self._ws().ensure()
        result = await self._runner(comando, cwd)
        if result.timed_out:
            return "⏱️ El comando excedió el tiempo límite y lo corté."
        parts = [f"(código {result.returncode})"]
        out, err = _cap(result.stdout, 65536), _cap(result.stderr, 65536)
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return await self._receipt("\n".join(parts))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_ari_tools_workspace.py -k ejecutar -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_workspace.py
git commit -m "feat(workspace): owner-only ejecutar scoped to the owner workspace"
```

---

### Task 7: MCP server `@tool` wrappers

**Files:**
- Modify: `src/ari/mcp_server/server.py` (add six wrappers inside `build_server`, before `return server`)
- Test: `tests/infrastructure/test_mcp_server.py` (extend)

**Interfaces:**
- Consumes: the six `AriTools` methods (Tasks 4-6), the `@tool(name)` decorator and `get_tools()` closure (existing).
- Produces: the six tools registered on the `ari` MCP server, so `server.list_tools()` returns exactly `set(ARI_TOOLS)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/infrastructure/test_mcp_server.py`)

```python
async def test_workspace_tools_callable_over_server(tmp_path):
    from ari.infrastructure.workspace.user_workspace import Workspaces
    conn = await connect(":memory:", embedding_dim=4)
    tools = AriTools(actor_from_env(ENV), schedule=SqliteScheduleStore(conn),
                     memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                     turn_log=SqliteTurnLog(conn), tz=TZ, max_items=20, clock=lambda: NOW,
                     workspaces=Workspaces(str(tmp_path)))

    async def get_tools():
        return tools

    server = build_server(get_tools)
    try:
        names = {t.name for t in await server.list_tools()}
        assert {"escribir_archivo", "leer_archivo", "listar_archivos",
                "borrar_archivo", "consultar_sql", "ejecutar"} <= names
        await server.call_tool("escribir_archivo", {"ruta": "a.txt", "contenido": "hola"})
        read = await server.call_tool("leer_archivo", {"ruta": "a.txt"})
        assert read.content[0].text == "hola"
    finally:
        await conn.close()


async def test_ejecutar_registered_only_for_owner_chat():
    async def get_tools():
        raise AssertionError("not called")

    from ari.domain.tools.ari_permissions import allowed_ari_tools
    owner = {t.name for t in await build_server(get_tools, allowed_ari_tools(True, "chat")).list_tools()}
    user = {t.name for t in await build_server(get_tools, allowed_ari_tools(False, "chat")).list_tools()}
    assert "ejecutar" in owner and "ejecutar" not in user
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/test_mcp_server.py -k "workspace_tools or ejecutar_registered" -v`
Expected: FAIL — wrappers not registered, so the names are missing. (The pre-existing `test_server_registers_exactly_the_catalogue_and_calls_tools` and the stdio handshake test will also fail now that `ARI_TOOLS` lists six extra names — expected until this task registers them.)

- [ ] **Step 3: Write the implementation**

In `src/ari/mcp_server/server.py`, inside `build_server`, just before `return server`, add:

```python
    @tool("escribir_archivo")
    async def escribir_archivo(ruta: str, contenido: str) -> str:
        """Crea o sobrescribe un archivo de texto en tu espacio de trabajo privado.
        ruta es relativa, p. ej. "notas/plan.txt"."""
        return await (await get_tools()).escribir_archivo(ruta, contenido)

    @tool("leer_archivo")
    async def leer_archivo(ruta: str) -> str:
        """Lee un archivo de texto de tu espacio de trabajo privado."""
        return await (await get_tools()).leer_archivo(ruta)

    @tool("listar_archivos")
    async def listar_archivos(ruta: str = ".") -> str:
        """Lista los archivos y carpetas de tu espacio de trabajo privado."""
        return await (await get_tools()).listar_archivos(ruta)

    @tool("borrar_archivo")
    async def borrar_archivo(ruta: str) -> str:
        """Borra un archivo de tu espacio de trabajo privado."""
        return await (await get_tools()).borrar_archivo(ruta)

    @tool("consultar_sql")
    async def consultar_sql(base: str, sql: str) -> str:
        """Ejecuta UNA sentencia SQL sobre un archivo .sqlite de tu espacio de
        trabajo (crear tablas, insertar, consultar). base es el nombre del
        archivo, p. ej. "datos.sqlite"."""
        return await (await get_tools()).consultar_sql(base, sql)

    @tool("ejecutar")
    async def ejecutar(comando: str) -> str:
        """(Solo dueño) Ejecuta un comando de shell con el directorio de trabajo
        en tu espacio privado. Devuelve código de salida, stdout y stderr."""
        return await (await get_tools()).ejecutar(comando)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_mcp_server.py -v`
Expected: PASS — the two new tests plus the restored catalogue/handshake parity tests.

- [ ] **Step 5: Commit**

```bash
git add src/ari/mcp_server/server.py tests/infrastructure/test_mcp_server.py
git commit -m "feat(workspace): expose workspace tools on the ari MCP server"
```

---

### Task 8: Wire settings, `main.py`, and `__main__.py`

**Files:**
- Modify: `src/ari/config/settings.py` (add `workspaces_dir`)
- Modify: `src/ari/main.py:158-170` (`sensitive` + `ari_spec` env)
- Modify: `src/ari/mcp_server/__main__.py:33-60` (construct + pass `workspaces`, `sql_sandbox`, `runner`)
- Test: `tests/config/test_settings_workspace.py`

**Interfaces:**
- Consumes: `Settings`, `Workspaces` (Task 1), `SqliteSandbox` (Task 2), `ShellRunner`, the `AriTools` kwargs (Task 4).
- Produces: `settings.workspaces_dir` (default `~/.ari/workspaces`, env `ARI_WORKSPACES_DIR`); the MCP server process receives `ARI_WORKSPACES_DIR` and builds `AriTools` with the three new collaborators; the workspaces dir is in the `sensitive` jail.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_settings_workspace.py
from ari.config.settings import Settings


def test_workspaces_dir_default(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    assert Settings().workspaces_dir == "~/.ari/workspaces"


def test_workspaces_dir_override(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ARI_WORKSPACES_DIR", "/data/ws")
    assert Settings().workspaces_dir == "/data/ws"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_settings_workspace.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'workspaces_dir'`

- [ ] **Step 3: Write the implementation**

In `src/ari/config/settings.py`, add near `skills_dir` (after line 36):

```python
    # Per-user execution/data workspaces (files + sqlite). Outside the repo.
    workspaces_dir: str = "~/.ari/workspaces"
```

In `src/ari/main.py`, add the resolved path to `sensitive` and to the `ari_spec` env. Replace the `sensitive = (...)` block (lines 158-159) with:

```python
    workspaces_abs = os.path.abspath(os.path.expanduser(settings.workspaces_dir))
    sensitive = (project_root, settings.vault_path,
                 os.path.join(project_root, ".env"),
                 os.path.abspath(settings.claude_config_dir), workspaces_abs)
```

and add one line to the `ari_spec` env dict (inside the `{...}` at lines 164-170):

```python
        "ARI_WORKSPACES_DIR": workspaces_abs,
```

In `src/ari/mcp_server/__main__.py`, add imports at the top of `_get_tools`'s local import block (after line 32) and construct the collaborators, then pass them to `AriTools`:

```python
        from ari.infrastructure.command.shell_runner import ShellRunner
        from ari.infrastructure.workspace.sqlite_sandbox import SqliteSandbox
        from ari.infrastructure.workspace.user_workspace import Workspaces
```

```python
        workspaces = Workspaces(env.get(
            "ARI_WORKSPACES_DIR", os.path.expanduser("~/.ari/workspaces")))
```

and in the `AriTools(...)` call add the three kwargs:

```python
            workspaces=workspaces,
            sql_sandbox=SqliteSandbox(),
            runner=ShellRunner(timeout=60.0))
```

- [ ] **Step 4: Run the test and the whole suite**

Run: `uv run pytest tests/config/test_settings_workspace.py -v`
Expected: PASS (2 passed)

Run: `uv run pytest`
Expected: PASS — the full suite is green (notably `tests/infrastructure/test_mcp_server.py::test_stdio_handshake_lists_tools_without_actor_env`, which spawns the real MCP subprocess and asserts it lists exactly `set(ARI_TOOLS)`, confirming all six wrappers are wired end to end).

- [ ] **Step 5: Commit**

```bash
git add src/ari/config/settings.py src/ari/main.py src/ari/mcp_server/__main__.py tests/config/test_settings_workspace.py
git commit -m "feat(workspace): wire workspaces_dir into settings, sensitive jail, and MCP server"
```

---

## Self-Review

**1. Spec coverage**
- Data location / `ARI_WORKSPACES_DIR` / sensitive jail → Task 8. ✅
- `UserWorkspace` jail → Task 1. ✅
- `SqliteSandbox` hardening → Task 2. ✅
- Tool surface (6 tools) → Tasks 4-7. ✅
- Gating (files/sql all users, `ejecutar` owner-only, CHAT-only) → Task 3 (+ Task 7 registration test). ✅
- Owner execution via `ShellRunner` jailed cwd → Task 6. ✅
- Limits (file size, SQL timeout/rows, command timeout/output) → Tasks 1, 2, 6. ✅
- Testing strategy → each task is TDD. ✅

**2. Placeholder scan** — no `TBD`/`TODO`/"handle edge cases"/"similar to Task N"; every code step has real code. ✅

**3. Type consistency** — `Workspaces.for_user → UserWorkspace`; `UserWorkspace.resolve/ensure/write_text/read_text/list/delete`; `SqliteSandbox.run → SqlResult(columns, rows, rowcount, truncated)`; `AriTools` kwargs `workspaces/sql_sandbox/runner` used consistently as `self._ws_factory/self._sql/self._runner`; `CommandResult.timed_out/returncode/stdout/stderr` matches `shell_runner.py`. ✅

**4. Review Focus coverage** — path escape (Task 1 `test_rejects_*`), dangerous SQL (Task 2 `test_attach_is_denied`), `ejecutar` non-owner (Task 3 + Task 6 `test_ejecutar_denied_for_non_owner`), oversized output/file (Task 1 `test_file_size_cap`, Task 6 timeout/cap), hostile `user_id` (Task 1 `test_user_ids_are_sanitized_and_isolated`). ✅

**Note on owner `ejecutar`:** scoped to `cwd` + timeout + output cap only; it runs with the Ari process's privileges and is **not** a real sandbox (accepted owner-trust decision, documented in the spec §4.4). Non-owner users never receive it.
