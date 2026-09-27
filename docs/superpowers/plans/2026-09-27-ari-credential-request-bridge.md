# Credential Request Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner hand Ari a credential conversationally ("quiero pasarte el api de groq" → Ari sends the vault_web link), and let Ari send that link the moment it actually needs a credential it lacks (e.g. a voice note arrives while `groq_audio` is `needs_secrets`).

**Architecture:** Two paths. The natural-language path mirrors the existing `coding_requests` cross-process queue: an owner-only agent tool (`pedir_credenciales`) runs in the DB-only MCP subprocess and enqueues a `credential_requests` row; a main-process `CredentialRequestRunner` (on the scheduler + `after_turn`) claims it, mints `VaultWebMaintainer.new_link()`, and sends the link over Telegram. The reactive path runs in the main process, so `_on_message` calls `new_link()` directly when a needs_secrets inbound skill is used.

**Tech Stack:** Python 3.11+, aiosqlite (shared SQLite `_SCHEMA`), python-telegram-bot, pytest + pytest-asyncio (`asyncio_mode=auto`). Mirrors `coding_requests` end-to-end; no new dependency.

**Spec:** `docs/superpowers/specs/2026-09-27-ari-credential-request-bridge-design.md`

## Global Constraints

- Python **>= 3.11**. Import style `from ari.<pkg> import <X>` (source root `src/`).
- Tests run with `python3 -m pytest` (the venv's bare `pytest`/`pip` are Python 3.9/3.14 — ALWAYS prefix `python3 -m`). `pythonpath = ["src"]`. No `conftest.py`. `asyncio_mode = "auto"` (async tests need no marker). Tests open a DB with `connect(":memory:", embedding_dim=4)`.
- The `pedir_credenciales` tool is **owner + CHAT context only** (add its name to `ARI_TOOLS`; it lands only in `set(ARI_TOOLS)`, exactly like `proponer_codigo`).
- Secrets are never entered here — this feature only sends the `vault_web` LAN link. `new_link()` shows ALL missing writable names; the message names the requested one.
- Status vocabulary reused: `PENDING, TAKEN, DONE, FAILED, SKIPPED`.
- Conventional Commit messages. **No AI attribution / no `Co-Authored-By`.**
- `reset_taken()` here **re-queues** stranded `taken` rows to `PENDING` (a credential link is cheap to re-mint) — this deliberately differs from the coding queue, which marks them `FAILED`.

## Review Focus

- **Non-owner (or task/heartbeat context) calls `pedir_credenciales`** — must return `DENIED` and enqueue nothing. (Task 2 test.)
- **A credential request left `taken` by a restart** — must be re-queued and re-answered with a fresh link, never a dead one. (Task 1 `reset_taken` test.)
- **`new_link()` raises when the runner tries to mint** — request finishes `FAILED` with a plain message; the bot does not crash. (Task 3 test.)
- **Voice note while `groq_audio` is `needs_secrets`** — the owner gets the vault link naming `GROQ_API_KEY`, not the generic "no pude procesar". (Task 4 helper test + Task 5 integration.)
- **A stale credential request (older than the TTL, e.g. bot was down for hours)** — skipped, not answered with an expired link. (Task 3 test.)

---

### Task 1: `credential_requests` store (domain + schema + queue)

**Files:**
- Create: `src/ari/domain/credentials/__init__.py` (empty)
- Create: `src/ari/domain/credentials/requests.py`
- Modify: `src/ari/infrastructure/persistence/db.py` (add a table to `_SCHEMA`)
- Create: `src/ari/infrastructure/persistence/sqlite_credential_requests.py`
- Test: `tests/infrastructure/test_sqlite_credential_requests.py`

**Interfaces:**
- Produces: `CredentialRequest(id:int, user_id:str, chat_id:str, requested:str, created_at:datetime)` and `PENDING/TAKEN/DONE/FAILED/SKIPPED`; `SqliteCredentialRequests(conn)` with `async add(user_id, chat_id, requested)->int`, `async claim_pending()->list[CredentialRequest]`, `async reset_taken()->list[CredentialRequest]` (taken→pending), `async finish(request_id, status, detail=None)`, `async status_of(request_id)->str|None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_sqlite_credential_requests.py
import asyncio

import pytest

from ari.domain.credentials.requests import DONE, FAILED, PENDING, TAKEN
from ari.infrastructure.persistence.db import connect, open_existing
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCredentialRequests(conn)
    await conn.close()


async def test_add_and_claim_once(store):
    a = await store.add("42", "42", "el api de groq")
    b = await store.add("42", "42", "OPENAI_API_KEY")
    claimed = await store.claim_pending()
    assert [(r.id, r.requested) for r in claimed] == [(a, "el api de groq"), (b, "OPENAI_API_KEY")]
    assert claimed[0].created_at.tzinfo is not None
    assert await store.claim_pending() == []
    assert await store.status_of(a) == TAKEN


async def test_finish(store):
    a = await store.add("42", "42", "x")
    await store.claim_pending()
    await store.finish(a, DONE)
    assert await store.status_of(a) == DONE
    await store.finish(a, FAILED, "boom")
    assert await store.status_of(a) == FAILED
    assert await store.status_of(999) is None


async def test_reset_taken_requeues_stranded_rows(store):
    a = await store.add("42", "42", "groq")
    await store.claim_pending()                 # a -> TAKEN
    reset = await store.reset_taken()
    assert [r.id for r in reset] == [a]
    assert await store.status_of(a) == PENDING  # re-queued, NOT failed
    assert [r.id for r in await store.claim_pending()] == [a]  # claimable again
    assert await store.reset_taken() == []


async def test_claim_is_atomic_across_connections(tmp_path):
    db = str(tmp_path / "ari.db")
    first = await connect(db, embedding_dim=4)
    await SqliteCredentialRequests(first).add("42", "42", "x")
    other = await open_existing(db)
    try:
        got = await asyncio.gather(SqliteCredentialRequests(first).claim_pending(),
                                   SqliteCredentialRequests(other).claim_pending())
    finally:
        await other.close()
        await first.close()
    assert sorted(len(g) for g in got) == [0, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_sqlite_credential_requests.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.domain.credentials'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/credentials/__init__.py
```

```python
# src/ari/domain/credentials/requests.py
from dataclasses import dataclass
from datetime import datetime

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"


@dataclass(frozen=True)
class CredentialRequest:
    """A credential the owner wants to load, waiting for the bot to mint a vault link."""
    id: int
    user_id: str
    chat_id: str
    requested: str        # free-text hint of what the owner wants to provide
    created_at: datetime  # tz-aware UTC
```

Add to `_SCHEMA` in `src/ari/infrastructure/persistence/db.py`, immediately after the
`coding_requests` table + index (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS credential_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  requested TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_credential_requests_status ON credential_requests(status, id);
```

```python
# src/ari/infrastructure/persistence/sqlite_credential_requests.py
import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.credentials.requests import PENDING, TAKEN, CredentialRequest

_COLS = "id, user_id, chat_id, requested, created_at"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _request(r) -> CredentialRequest:
    return CredentialRequest(r["id"], r["user_id"], r["chat_id"], r["requested"],
                             datetime.fromisoformat(r["created_at"]))


class SqliteCredentialRequests:
    """Queue between Ari's MCP server (writer) and the bot (claimer)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str, requested: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO credential_requests (user_id, chat_id, requested, status, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, chat_id, requested, PENDING, _iso(datetime.now(timezone.utc))))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[CredentialRequest]:
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE credential_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (TAKEN, PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def reset_taken(self) -> list[CredentialRequest]:
        """Re-queue stranded `taken` rows (bot restarted mid-delivery) back to `pending`
        so the runner mints a fresh link. A credential link is cheap to re-mint, so unlike
        the coding queue this re-queues instead of failing."""
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE credential_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (PENDING, TAKEN))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def finish(self, request_id: int, status: str, detail: str | None = None) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE credential_requests SET status = ?, detail = ? WHERE id = ?",
                (status, detail, request_id))
            await self._conn.commit()

    async def status_of(self, request_id: int) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT status FROM credential_requests WHERE id = ?", (request_id,))
        return rows[0]["status"] if rows else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_sqlite_credential_requests.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/credentials src/ari/infrastructure/persistence/sqlite_credential_requests.py src/ari/infrastructure/persistence/db.py tests/infrastructure/test_sqlite_credential_requests.py
git commit -m "feat(credentials): credential_requests queue (store + schema)"
```

---

### Task 2: `pedir_credenciales` agent tool

**Files:**
- Modify: `src/ari/application/ari_tools.py` (add `credentials` kwarg + method)
- Modify: `src/ari/domain/tools/ari_permissions.py` (add tool name to `ARI_TOOLS`)
- Modify: `src/ari/mcp_server/server.py` (register `@tool`)
- Modify: `src/ari/mcp_server/__main__.py` (pass `credentials=SqliteCredentialRequests(conn)`)
- Test: `tests/application/test_ari_tools_credentials.py`

**Interfaces:**
- Consumes: `SqliteCredentialRequests.add` (Task 1); `AriTools`/`Actor`, `DENIED`, `truncate`, `_allowed`, `_receipt` (existing).
- Produces: `AriTools.pedir_credenciales(nombres:str)->str`; `"pedir_credenciales"` in `ARI_TOOLS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_ari_tools_credentials.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, SqliteCredentialRequests(conn), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, owner=True, context=CHAT):
    conn, credentials, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, credentials=credentials)


async def test_owner_chat_queues_request_and_writes_receipt(env):
    _, credentials, log = env
    out = await _tools(env).pedir_credenciales("el api de groq")
    assert out == "🔑 Te preparo el link seguro para: el api de groq"
    [req] = await credentials.claim_pending()
    assert (req.user_id, req.chat_id, req.requested) == ("42", "42", "el api de groq")
    assert await log.receipts("t1") == [out]


@pytest.mark.parametrize("nombres", ["", "   ", "x" * 501])
async def test_invalid_input_inserts_nothing(env, nombres):
    _, credentials, log = env
    out = await _tools(env).pedir_credenciales(nombres)
    assert out.startswith("No pude prepararlo")
    assert await credentials.claim_pending() == [] and await log.receipts("t1") == []


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_denied_outside_owner_chat(env, owner, context):
    _, credentials, log = env
    assert await _tools(env, owner, context).pedir_credenciales("groq") == DENIED
    assert await credentials.claim_pending() == [] and await log.receipts("t1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_ari_tools_credentials.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'credentials'`

- [ ] **Step 3: Write minimal implementation**

In `src/ari/application/ari_tools.py`, add the kwarg to `__init__` (keep existing params) and store it:

```python
    def __init__(self, actor: Actor, *, schedule, memory, turn_log, tz, max_items: int,
                 clock, gate=None, access=None, coding=None, credentials=None):
        self._a, self._schedule, self._memory, self._log = actor, schedule, memory, turn_log
        self._tz, self._max, self._clock = tz, max_items, clock
        self._gate, self._access = gate, access
        self._coding = coding  # SqliteCodingRequests | None
        self._credentials = credentials  # SqliteCredentialRequests | None
```

Add the method (near `proponer_codigo`):

```python
    async def pedir_credenciales(self, nombres: str) -> str:
        if not self._allowed("pedir_credenciales"):
            return DENIED
        text = (nombres or "").strip()
        if not text or len(text) > 500:
            return "No pude prepararlo: decime qué credencial querés cargar (1–500 caracteres)."
        if self._credentials is None:
            return "No pude prepararlo: la cola de credenciales no está disponible."
        await self._credentials.add(self._a.user_id, self._a.chat_id, text)
        return await self._receipt(f"🔑 Te preparo el link seguro para: {truncate(text)}")
```

In `src/ari/domain/tools/ari_permissions.py`, append `"pedir_credenciales"` to the `ARI_TOOLS` tuple (leave `_READ`/`_USER_CHAT` unchanged so it stays owner+CHAT only):

```python
ARI_TOOLS = ("agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "aprobar_acceso", "revocar_acceso", "ver_accesos",
             "enviar_mensaje", "proponer_codigo", "pedir_credenciales")
```

In `src/ari/mcp_server/server.py`, register it (next to `proponer_codigo`):

```python
    @tool("pedir_credenciales")
    async def pedir_credenciales(nombres: str) -> str:
        """(Solo el creador) Cuando tu creador quiera darte una credencial, API o
        contraseña (p. ej. "quiero pasarte el api de groq"), prepara un link seguro para
        que la cargue sin escribirla en el chat. nombres: qué credencial quiere cargar."""
        return await (await get_tools()).pedir_credenciales(nombres)
```

In `src/ari/mcp_server/__main__.py`, import `SqliteCredentialRequests` and pass it to
`AriTools(...)` alongside `coding=`:

```python
        credentials=SqliteCredentialRequests(conn))
```

- [ ] **Step 4: Run tests to verify they pass, plus an import smoke for the subprocess wiring**

Run: `python3 -m pytest tests/application/test_ari_tools_credentials.py -v`
Expected: PASS (5 tests)
Run: `python3 -c "import ari.mcp_server.server, ari.mcp_server.__main__"`
Expected: no error (the `@tool` registration and `__main__` import resolve)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py src/ari/domain/tools/ari_permissions.py src/ari/mcp_server/server.py src/ari/mcp_server/__main__.py tests/application/test_ari_tools_credentials.py
git commit -m "feat(credentials): owner-only pedir_credenciales agent tool"
```

---

### Task 3: `CredentialRequestRunner`

**Files:**
- Create: `src/ari/application/credentials/__init__.py` (empty)
- Create: `src/ari/application/credentials/request_runner.py`
- Test: `tests/application/test_credential_request_runner.py`

**Interfaces:**
- Consumes: `SqliteCredentialRequests.claim_pending/finish` (Task 1); a `vault_web` object exposing `new_link()->str`; an async `send(chat_id:str, text:str)`; a `clock()->datetime`.
- Produces: `CredentialRequestRunner(requests, vault_web, send, clock, max_age=timedelta(hours=1))` with `async __call__()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_credential_request_runner.py
from datetime import datetime, timedelta, timezone

import pytest

from ari.application.credentials.request_runner import CredentialRequestRunner
from ari.domain.credentials.requests import DONE, FAILED, SKIPPED
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def requests():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCredentialRequests(conn)
    await conn.close()


class _Vault:
    def __init__(self, url="https://192.168.0.5:8765/v/tok", boom=False):
        self._url, self._boom = url, boom
        self.calls = 0

    def new_link(self):
        self.calls += 1
        if self._boom:
            raise RuntimeError("cert failed")
        return self._url


def _runner(requests, vault, clock=lambda: NOW):
    sent = []
    async def send(chat_id, text):
        sent.append((chat_id, text))
    return CredentialRequestRunner(requests, vault, send, clock), sent


async def test_pending_request_gets_link(requests):
    rid = await requests.add("42", "42", "el api de groq")
    vault = _Vault()
    runner, sent = _runner(requests, vault)
    await runner()
    assert vault.calls == 1
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "42"
    assert "https://192.168.0.5:8765/v/tok" in text and "el api de groq" in text
    assert await requests.status_of(rid) == DONE


async def test_stale_request_skipped_without_link(requests):
    rid = await requests.add("42", "42", "groq")
    vault = _Vault()
    runner, sent = _runner(requests, vault, clock=lambda: NOW + timedelta(hours=2))
    await runner()
    assert vault.calls == 0 and sent == []
    assert await requests.status_of(rid) == SKIPPED


async def test_new_link_failure_is_reported(requests):
    rid = await requests.add("42", "42", "groq")
    vault = _Vault(boom=True)
    runner, sent = _runner(requests, vault)
    await runner()
    assert await requests.status_of(rid) == FAILED
    assert len(sent) == 1 and "No pude generar el link" in sent[0][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_credential_request_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.application.credentials'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/credentials/__init__.py
```

```python
# src/ari/application/credentials/request_runner.py
import logging
from datetime import timedelta

from ari.application.text_format import truncate
from ari.domain.credentials.requests import DONE, FAILED, SKIPPED

log = logging.getLogger("ari.credentials")


class CredentialRequestRunner:
    """Claims queued credential requests and answers each with a fresh vault_web link."""

    def __init__(self, requests, vault_web, send, clock, max_age: timedelta = timedelta(hours=1)):
        self._requests, self._vault_web = requests, vault_web
        self._send, self._clock, self._max_age = send, clock, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                continue
            try:
                link = self._vault_web.new_link()
            except Exception as exc:
                log.warning("credential link mint failed: %s", exc)
                await self._requests.finish(req.id, FAILED, str(exc))
                await self._send(req.chat_id, "No pude generar el link para cargar la credencial.")
                continue
            await self._send(req.chat_id,
                f"Para cargar {truncate(req.requested)} abrí este link en la misma red "
                f"(vence pronto):\n{link}\nEl formulario muestra todas las credenciales que "
                f"faltan; completá la que corresponda.")
            await self._requests.finish(req.id, DONE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_credential_request_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/credentials/__init__.py src/ari/application/credentials/request_runner.py tests/application/test_credential_request_runner.py
git commit -m "feat(credentials): CredentialRequestRunner mints and sends the vault link"
```

---

### Task 4: Reactive missing-secret detector

**Files:**
- Create: `src/ari/application/credentials/needs.py`
- Test: `tests/application/test_credential_needs.py`

**Interfaces:**
- Consumes: `SkillManager.list()` → `SkillStatus(state, missing_secrets, hooks, …)` (existing).
- Produces: `missing_inbound_secrets(statuses) -> list[str]` — deduped, sorted secret names of inbound-capable skills stuck in `needs_secrets`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_credential_needs.py
from ari.application.credentials.needs import missing_inbound_secrets
from ari.application.skills.skill_manager import SkillStatus


def _s(name, state, missing, hooks):
    return SkillStatus(name, "0.1.0", state == "active", state, missing, hooks)


def test_collects_missing_secrets_of_needs_secrets_inbound_skills():
    statuses = [
        _s("groq_audio", "needs_secrets", ["GROQ_API_KEY"], ["inbound_transform", "outbound_transform"]),
        _s("other", "needs_secrets", ["OTHER_KEY"], ["outbound_transform"]),   # not inbound -> ignored
        _s("active_one", "active", [], ["inbound_transform"]),                  # active -> ignored
    ]
    assert missing_inbound_secrets(statuses) == ["GROQ_API_KEY"]


def test_empty_when_nothing_needs_inbound_secrets():
    assert missing_inbound_secrets([_s("a", "active", [], ["inbound_transform"])]) == []
    assert missing_inbound_secrets([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_credential_needs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.application.credentials.needs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/credentials/needs.py
def missing_inbound_secrets(statuses) -> list[str]:
    """Secret names missing for inbound-capable skills stuck in needs_secrets (deduped, sorted).

    A voice note that produced no text while such a skill exists means Ari needs that
    credential — the caller offers the vault link instead of a generic failure."""
    return sorted({
        name
        for s in statuses
        if s.state == "needs_secrets" and "inbound_transform" in s.hooks
        for name in s.missing_secrets
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_credential_needs.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/credentials/needs.py tests/application/test_credential_needs.py
git commit -m "feat(credentials): detect missing secrets of needs_secrets inbound skills"
```

---

### Task 5: Composition & reactive voice branch (integration)

**Files:**
- Modify: `src/ari/main.py`
- Test: none new (glue over already-tested units); proof is the full suite + an import smoke.

**Interfaces:**
- Consumes: `SqliteCredentialRequests` (Task 1), `CredentialRequestRunner` (Task 3), `missing_inbound_secrets` (Task 4), `VaultWebMaintainer.new_link()`, the existing `send`/`spawn`/`after_turn`/`Scheduler` wiring.

This task wires closures over `app.bot`/`app.bot_data`; every branch it uses is already unit-tested. Follow the edits exactly.

- [ ] **Step 1: Add imports**

At the top of `src/ari/main.py`:

```python
from ari.application.credentials.request_runner import CredentialRequestRunner
from ari.application.credentials.needs import missing_inbound_secrets
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests
```

- [ ] **Step 2: Construct the store + runner and register them (mirror `coding_runner`)**

Where `coding_requests` and `coding_runner` are created in `_post_init`, add alongside:

```python
    credential_requests = SqliteCredentialRequests(c.conn)
    credential_runner = CredentialRequestRunner(credential_requests, c.vault_web, send, _utcnow)
```

Add `credential_runner` to the `Scheduler([...])` list (next to `coding_runner`, before `vault_web_sweep`):

```python
        scheduler = Scheduler([due, notices.tick, heartbeat, flusher, coding_runner,
                               credential_runner, vault_web_sweep])
```

In `after_turn()`, run it after `coding_runner()` (same try/except shape):

```python
        try:
            await credential_runner()
        except Exception:
            log.exception("credential request runner failed")
```

At startup, next to the `coding_requests.reset_taken()` loop, re-queue stranded credential requests (they were re-queued to `pending` by `reset_taken`, so just notify):

```python
        for r in await credential_requests.reset_taken():
            await send(r.chat_id, "Retomo tu pedido de credencial…")
```

- [ ] **Step 3: Offer the link in the voice branch when a secret is missing**

In `_on_message`, replace the generic no-text failure reply with a missing-secret check first:

```python
    if not text:
        missing = missing_inbound_secrets(app.bot_data["skills"].list())
        if is_owner and missing:
            try:
                link = app.bot_data["vault_web"].new_link()
                await msg.reply_text(
                    f"Necesito {', '.join(missing)} para procesar audio. Cargala en la misma "
                    f"red (vence pronto):\n{link}")
            except Exception:
                await msg.reply_text("No pude procesar ese audio. ¿Lo escribís?")
        else:
            await msg.reply_text(
                "No pude procesar ese audio (¿muy largo o error de transcripción?). ¿Lo escribís?")
        return
```

(`is_owner` is already computed earlier in the voice branch; `app.bot_data["skills"]` and `["vault_web"]` are set in `_post_init`.)

- [ ] **Step 4: Verify the whole suite and an import smoke**

Run: `python3 -m pytest -q --continue-on-collection-errors`
Expected: no NEW failures vs. the base. Known environmental failures that already fail on the base are acceptable: the `*_live` tests (network/creds) and the `test_mcp_server.py` collection error (`mcp.server` import under the current Python). Report the exact pass/fail line.

Run: `python3 -c "import ari.main"`
Expected: no error.

- [ ] **Step 5: Manual smoke (documented, not automated)**

1. Run Ari with a valid `.env`.
2. Tell Ari "quiero pasarte el api de groq" → within a turn Ari replies with a `vault_web` `https://…/v/<token>` link.
3. Send a voice note while `GROQ_API_KEY` is missing → Ari replies with the link naming `GROQ_API_KEY` (not the generic failure).
4. Load the key via the link, then send a voice note → it transcribes normally.

- [ ] **Step 6: Commit**

```bash
git add src/ari/main.py
git commit -m "feat(credentials): wire runner + reactive voice link into the bot"
```

---

## Notes for the implementer

- `new_link()` is synchronous and thread-safe (`threading.Lock`); call it without `await`.
- Keep `python3 -m` in front of every pytest invocation.
- `reset_taken()` here re-queues (`taken`→`pending`); do not copy the coding queue's `taken`→`failed`.
- The MCP subprocess (`mcp_server/`) only enqueues; it never mints links. The runner and the reactive branch both run in the main process, which is the only place `vault_web.new_link()` works.
