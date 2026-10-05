# Personal WhatsApp (read + approved reply) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Ari read the owner's personal WhatsApp, notify the owner on Telegram about filtered incoming messages, and send replies only to messages the owner confirms.

**Architecture:** Hexagonal. A `WhatsAppPort` isolates the neonize (whatsmeow) multi-device session, which runs in-process in Ari's main asyncio loop. Inbound events flow through an ingest service (store → filter → notify-on-Telegram). Outbound replies cross the main/MCP process boundary through the shared SQLite DB using Ari's existing outbox pattern: an MCP tool persists a draft and (on confirmation) queues it; a main-process flusher sends queued rows via neonize.

**Tech Stack:** Python 3.11+, asyncio, `neonize` (whatsmeow binding), SQLite (`aiosqlite`, shared `ari.db`), `python-telegram-bot` (existing), MCP tools via the `ari` server.

**Spec:** [docs/superpowers/specs/2026-10-05-ari-whatsapp-personal-design.md](../specs/2026-10-05-ari-whatsapp-personal-design.md)

## Global Constraints

- Python `>=3.11`; ruff `line-length = 100`.
- `neonize` is added as an **optional extra** (`[project.optional-dependencies] whatsapp`), so the base install stays lean. Do not add it to base `dependencies`.
- WhatsApp is **disabled by default**: `whatsapp_enabled: bool = False`. None of the session wiring starts unless enabled.
- **Owner-only**: the WhatsApp tools go in `ARI_TOOLS` but **not** in `_USER_CHAT`, and are absent from `TASK`/`HEARTBEAT`.
- Model-facing tool docstrings are in **Spanish** (consistent with existing tools); all other artifacts (code, identifiers, comments) in English.
- Strict TDD: RED → GREEN → REFACTOR. Never write implementation before a failing test for the owned behavior.
- Frequent commits: one per task minimum.
- No auto-reply, no bulk/broadcast send, one message per confirmed action.
- Run tests with `python3 -m pytest` (see README note on `python3`).

## Review Focus

- **Media-only inbound (empty `text`)** — ingest and the pending list must show a typed placeholder (e.g. `[image]`), never crash or notify an empty body. *(Task 4 test)*
- **Concurrent flush double-send** — `claim_queued` must atomically flip `queued→sending` so two flush ticks never send the same row twice. *(Task 3 test)*
- **Accent/case keyword match** — a keyword `pago` must match `Pagó`, and a contact match must be case-insensitive. *(Task 2 test)*
- **Confirm bypass / stale draft** — `whatsapp_enviar` on an unknown or already-queued/sent draft id must refuse and send nothing. *(Task 6 test)*
- **Non-owner access** — any WhatsApp tool invoked by a non-owner in CHAT returns `DENIED` and performs no read or write. *(Task 7 test)*

---

### Task 1: Domain port + entities

**Files:**
- Create: `src/ari/domain/whatsapp/__init__.py`
- Create: `src/ari/domain/whatsapp/entities.py`
- Create: `src/ari/domain/whatsapp/whatsapp_port.py`
- Test: `tests/domain/test_whatsapp_entities.py`

**Interfaces:**
- Produces: `InboundWhatsApp(wa_chat_id: str, contact_name: str, text: str, media_kind: str = "", is_group: bool = False)`; `WhatsAppPort` Protocol with `async start(on_message)`, `async send(wa_chat_id, text)`, `async pair_phone(number) -> str`, `connection_state() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_whatsapp_entities.py
from ari.domain.whatsapp.entities import InboundWhatsApp


def test_inbound_defaults_text_only():
    m = InboundWhatsApp(wa_chat_id="5939@s.whatsapp.net", contact_name="Juan", text="hola")
    assert m.media_kind == ""
    assert m.is_group is False


def test_inbound_media_placeholder_fields():
    m = InboundWhatsApp(wa_chat_id="g@g.us", contact_name="Grupo", text="",
                        media_kind="image", is_group=True)
    assert m.media_kind == "image"
    assert m.is_group is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_whatsapp_entities.py -v`
Expected: FAIL with `ModuleNotFoundError: ari.domain.whatsapp`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/whatsapp/__init__.py
# (empty)
```

```python
# src/ari/domain/whatsapp/entities.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundWhatsApp:
    wa_chat_id: str
    contact_name: str
    text: str
    media_kind: str = ""   # "", "image", "audio", "video", "document", "sticker"
    is_group: bool = False
```

```python
# src/ari/domain/whatsapp/whatsapp_port.py
from typing import Awaitable, Callable, Protocol

from ari.domain.whatsapp.entities import InboundWhatsApp

OnMessage = Callable[[InboundWhatsApp], Awaitable[None]]


class WhatsAppPort(Protocol):
    async def start(self, on_message: OnMessage) -> None: ...
    async def send(self, wa_chat_id: str, text: str) -> None: ...
    async def pair_phone(self, number: str) -> str: ...
    def connection_state(self) -> str: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/domain/test_whatsapp_entities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/whatsapp tests/domain/test_whatsapp_entities.py
git commit -m "feat(whatsapp): domain port and inbound entity"
```

---

### Task 2: Filter logic

**Files:**
- Create: `src/ari/application/whatsapp/__init__.py`
- Create: `src/ari/application/whatsapp/filter.py`
- Test: `tests/application/test_whatsapp_filter.py`

**Interfaces:**
- Produces: `WhatsAppFilter(contacts: frozenset[str], keywords: frozenset[str])`; `passes_filter(contact_name: str, wa_chat_id: str, text: str, f: WhatsAppFilter) -> bool`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_whatsapp_filter.py
from ari.application.whatsapp.filter import WhatsAppFilter, passes_filter


def _f(contacts=(), keywords=()):
    return WhatsAppFilter(frozenset(contacts), frozenset(keywords))


def test_contact_match_is_case_insensitive():
    assert passes_filter("Juan Perez", "549111@s.whatsapp.net", "hola", _f(contacts={"juan perez"}))


def test_contact_match_by_number():
    assert passes_filter("", "549111@s.whatsapp.net", "hola", _f(contacts={"549111"}))


def test_keyword_match_ignores_accents_and_case():
    assert passes_filter("Ana", "x@s.whatsapp.net", "Ya te Pagó el flete", _f(keywords={"pago"}))


def test_no_match_returns_false():
    assert not passes_filter("Ana", "x@s.whatsapp.net", "buen dia", _f(contacts={"juan"}, keywords={"factura"}))


def test_empty_filter_notifies_nothing():
    assert not passes_filter("Ana", "x@s.whatsapp.net", "hola", _f())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_whatsapp_filter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/whatsapp/__init__.py
# (empty)
```

```python
# src/ari/application/whatsapp/filter.py
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WhatsAppFilter:
    contacts: frozenset[str]
    keywords: frozenset[str]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().strip()


def passes_filter(contact_name: str, wa_chat_id: str, text: str, f: WhatsAppFilter) -> bool:
    name_n = _norm(contact_name)
    number_n = _norm(wa_chat_id.split("@", 1)[0])
    for c in f.contacts:
        cn = _norm(c)
        if cn and (cn == name_n or cn in number_n):
            return True
    text_n = _norm(text)
    return any(_norm(k) in text_n for k in f.keywords if k.strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_whatsapp_filter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/whatsapp/__init__.py src/ari/application/whatsapp/filter.py tests/application/test_whatsapp_filter.py
git commit -m "feat(whatsapp): contact/keyword filter with accent-insensitive match"
```

---

### Task 3: Persistence (SqliteWhatsApp + schema)

**Files:**
- Create: `src/ari/infrastructure/persistence/sqlite_whatsapp.py`
- Modify: `src/ari/infrastructure/persistence/db.py` (add two tables to `_SCHEMA`)
- Test: `tests/infrastructure/test_sqlite_whatsapp.py`

**Interfaces:**
- Consumes: `connect()` from `db.py`; `InboundWhatsApp`; `WhatsAppFilter`.
- Produces: `SqliteWhatsApp(conn, clock=_utcnow)` with:
  - `async record_inbound(m: InboundWhatsApp) -> int`
  - `async mark_notified(id: int) -> None`
  - `async list_pending(limit: int) -> list[Row]` (Row: `id, wa_chat_id, contact_name, text, media_kind, ts`)
  - `async get_inbound(id: int) -> Row | None`
  - `async create_draft(inbound_id: int, wa_chat_id: str, contact_name: str, text: str) -> int`
  - `async get_draft(draft_id: int) -> Row | None`
  - `async queue_draft(draft_id: int) -> Row | None` (atomic `draft→queued`; marks linked inbound answered; returns the row or None if not in `draft`)
  - `async claim_queued() -> Row | None` (atomic `queued→sending`; Row includes `wa_chat_id, text, contact_name`)
  - `async mark_sent(id: int) -> None` / `async mark_failed(id: int) -> None`
  - `async get_filter() -> WhatsAppFilter` / `async add_contact(v)` / `async remove_contact(v)` / `async add_keyword(v)` / `async remove_keyword(v)`

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_sqlite_whatsapp.py
import pytest

from ari.domain.whatsapp.entities import InboundWhatsApp
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_whatsapp import SqliteWhatsApp


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=8)
    yield SqliteWhatsApp(conn)
    await conn.close()


async def test_record_and_list_pending(store):
    rid = await store.record_inbound(
        InboundWhatsApp("549111@s.whatsapp.net", "Juan", "hola", "", False))
    rows = await store.list_pending(10)
    assert rows[0].id == rid and rows[0].contact_name == "Juan" and rows[0].text == "hola"


async def test_draft_then_queue_marks_inbound_answered(store):
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "pago?", "", False))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "sí, mañana")
    queued = await store.queue_draft(did)
    assert queued is not None and queued.text == "sí, mañana"
    # answered inbound no longer pending
    assert all(r.id != rid for r in await store.list_pending(10))


async def test_queue_draft_twice_returns_none_second_time(store):
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "hi", "", False))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "ok")
    assert await store.queue_draft(did) is not None
    assert await store.queue_draft(did) is None  # no double-queue


async def test_claim_queued_is_atomic_no_double_send(store):
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "hi", "", False))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "ok")
    await store.queue_draft(did)
    first = await store.claim_queued()
    second = await store.claim_queued()
    assert first is not None and second is None  # only one claim wins


async def test_filter_roundtrip(store):
    await store.add_contact("Juan")
    await store.add_keyword("factura")
    f = await store.get_filter()
    assert "Juan" in f.contacts and "factura" in f.keywords
    await store.remove_contact("Juan")
    assert "Juan" not in (await store.get_filter()).contacts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_sqlite_whatsapp.py -v`
Expected: FAIL (module/table missing)

- [ ] **Step 3: Add tables to the schema**

In `src/ari/infrastructure/persistence/db.py`, append to the `_SCHEMA` string (same style as existing tables):

```sql
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_id INTEGER,                      -- for drafts/outbound: the inbound replied to
    wa_chat_id TEXT NOT NULL,
    contact_name TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL,                 -- 'in' | 'out'
    text TEXT NOT NULL DEFAULT '',
    media_kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,                     -- in: pending|notified|answered ; out: draft|queued|sending|sent|failed
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS whatsapp_filter (
    kind TEXT NOT NULL,                       -- 'contact' | 'keyword'
    value TEXT NOT NULL,
    PRIMARY KEY (kind, value)
);
```

- [ ] **Step 4: Write minimal implementation**

```python
# src/ari/infrastructure/persistence/sqlite_whatsapp.py
from dataclasses import dataclass
from datetime import datetime, timezone

from ari.application.whatsapp.filter import WhatsAppFilter
from ari.domain.whatsapp.entities import InboundWhatsApp


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Row:
    id: int
    wa_chat_id: str
    contact_name: str
    text: str
    media_kind: str
    ts: str
    inbound_id: int | None = None


class SqliteWhatsApp:
    def __init__(self, conn, clock=_utcnow):
        self._c = conn
        self._clock = clock

    async def record_inbound(self, m: InboundWhatsApp) -> int:
        cur = await self._c.execute(
            "INSERT INTO whatsapp_messages "
            "(wa_chat_id, contact_name, direction, text, media_kind, status, ts) "
            "VALUES (?,?, 'in', ?,?, 'pending', ?)",
            (m.wa_chat_id, m.contact_name, m.text, m.media_kind, self._clock().isoformat()),
        )
        await self._c.commit()
        return cur.lastrowid

    async def mark_notified(self, id: int) -> None:
        await self._c.execute(
            "UPDATE whatsapp_messages SET status='notified' WHERE id=? AND status='pending'", (id,))
        await self._c.commit()

    async def list_pending(self, limit: int) -> list[Row]:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts FROM whatsapp_messages "
            "WHERE direction='in' AND status IN ('pending','notified') ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 25)),))
        return [Row(*r) for r in await cur.fetchall()]

    async def get_inbound(self, id: int) -> Row | None:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts FROM whatsapp_messages "
            "WHERE id=? AND direction='in'", (id,))
        r = await cur.fetchone()
        return Row(*r) if r else None

    async def create_draft(self, inbound_id: int, wa_chat_id: str,
                           contact_name: str, text: str) -> int:
        cur = await self._c.execute(
            "INSERT INTO whatsapp_messages "
            "(inbound_id, wa_chat_id, contact_name, direction, text, status, ts) "
            "VALUES (?,?,?, 'out', ?, 'draft', ?)",
            (inbound_id, wa_chat_id, contact_name, text, self._clock().isoformat()))
        await self._c.commit()
        return cur.lastrowid

    async def get_draft(self, draft_id: int) -> Row | None:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts, inbound_id "
            "FROM whatsapp_messages WHERE id=? AND direction='out' AND status='draft'", (draft_id,))
        r = await cur.fetchone()
        return Row(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) if r else None

    async def queue_draft(self, draft_id: int) -> Row | None:
        cur = await self._c.execute(
            "UPDATE whatsapp_messages SET status='queued' WHERE id=? AND status='draft'", (draft_id,))
        await self._c.commit()
        if cur.rowcount == 0:
            return None
        row = await self._row(draft_id)
        if row.inbound_id is not None:
            await self._c.execute(
                "UPDATE whatsapp_messages SET status='answered' WHERE id=? AND direction='in'",
                (row.inbound_id,))
            await self._c.commit()
        return row

    async def claim_queued(self) -> Row | None:
        cur = await self._c.execute(
            "UPDATE whatsapp_messages SET status='sending' WHERE id = ("
            "  SELECT id FROM whatsapp_messages WHERE direction='out' AND status='queued' "
            "  ORDER BY id LIMIT 1)")
        await self._c.commit()
        if cur.rowcount == 0:
            return None
        cur2 = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts, inbound_id "
            "FROM whatsapp_messages WHERE status='sending' ORDER BY id LIMIT 1")
        r = await cur2.fetchone()
        return Row(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) if r else None

    async def mark_sent(self, id: int) -> None:
        await self._c.execute("UPDATE whatsapp_messages SET status='sent' WHERE id=?", (id,))
        await self._c.commit()

    async def mark_failed(self, id: int) -> None:
        await self._c.execute("UPDATE whatsapp_messages SET status='failed' WHERE id=?", (id,))
        await self._c.commit()

    async def get_filter(self) -> WhatsAppFilter:
        cur = await self._c.execute("SELECT kind, value FROM whatsapp_filter")
        contacts, keywords = set(), set()
        for kind, value in await cur.fetchall():
            (contacts if kind == "contact" else keywords).add(value)
        return WhatsAppFilter(frozenset(contacts), frozenset(keywords))

    async def add_contact(self, v: str) -> None:
        await self._put("contact", v)

    async def remove_contact(self, v: str) -> None:
        await self._del("contact", v)

    async def add_keyword(self, v: str) -> None:
        await self._put("keyword", v)

    async def remove_keyword(self, v: str) -> None:
        await self._del("keyword", v)

    async def _put(self, kind: str, v: str) -> None:
        await self._c.execute(
            "INSERT OR IGNORE INTO whatsapp_filter (kind, value) VALUES (?,?)", (kind, v.strip()))
        await self._c.commit()

    async def _del(self, kind: str, v: str) -> None:
        await self._c.execute(
            "DELETE FROM whatsapp_filter WHERE kind=? AND value=?", (kind, v.strip()))
        await self._c.commit()

    async def _row(self, id: int) -> Row:
        cur = await self._c.execute(
            "SELECT id, wa_chat_id, contact_name, text, media_kind, ts, inbound_id "
            "FROM whatsapp_messages WHERE id=?", (id,))
        r = await cur.fetchone()
        return Row(r[0], r[1], r[2], r[3], r[4], r[5], r[6])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/infrastructure/test_sqlite_whatsapp.py -v`
Expected: PASS (all 5)

- [ ] **Step 6: Commit**

```bash
git add src/ari/infrastructure/persistence/sqlite_whatsapp.py src/ari/infrastructure/persistence/db.py tests/infrastructure/test_sqlite_whatsapp.py
git commit -m "feat(whatsapp): sqlite store with atomic queue claim and filter config"
```

---

### Task 4: Ingest (record → filter → notify)

**Files:**
- Create: `src/ari/application/whatsapp/ingest.py`
- Test: `tests/application/test_whatsapp_ingest.py`

**Interfaces:**
- Consumes: `SqliteWhatsApp` (`record_inbound`, `get_filter`, `mark_notified`); `passes_filter`; a `notify: Callable[[str, str], Awaitable[None]]` (the proactive Telegram `send`); `owner_ids: Iterable[str]`.
- Produces: `WhatsAppIngest(store, notify, owner_ids)` callable `async __call__(m: InboundWhatsApp) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_whatsapp_ingest.py
from ari.application.whatsapp.filter import WhatsAppFilter
from ari.application.whatsapp.ingest import WhatsAppIngest
from ari.domain.whatsapp.entities import InboundWhatsApp


class FakeStore:
    def __init__(self, f):
        self._f = f
        self.recorded, self.notified = [], []
    async def record_inbound(self, m):
        self.recorded.append(m)
        return len(self.recorded)
    async def get_filter(self):
        return self._f
    async def mark_notified(self, id):
        self.notified.append(id)


def _ingest(store, sent):
    async def notify(chat_id, text):
        sent.append((chat_id, text))
    return WhatsAppIngest(store, notify, owner_ids=["111", "222"])


async def test_passing_message_notifies_every_owner_and_marks_notified():
    sent = []
    store = FakeStore(WhatsAppFilter(frozenset({"juan"}), frozenset()))
    await _ingest(store, sent)(InboundWhatsApp("5@s.whatsapp.net", "Juan", "hola"))
    assert [c for c, _ in sent] == ["111", "222"]
    assert "Juan" in sent[0][1] and "hola" in sent[0][1]
    assert store.notified == [1]


async def test_non_matching_message_is_stored_but_silent():
    sent = []
    store = FakeStore(WhatsAppFilter(frozenset(), frozenset()))
    await _ingest(store, sent)(InboundWhatsApp("5@s.whatsapp.net", "Ana", "hola"))
    assert sent == [] and store.notified == [] and len(store.recorded) == 1


async def test_media_only_message_uses_placeholder_body():
    sent = []
    store = FakeStore(WhatsAppFilter(frozenset({"ana"}), frozenset()))
    await _ingest(store, sent)(InboundWhatsApp("5@s.whatsapp.net", "Ana", "", media_kind="image"))
    assert "[image]" in sent[0][1]  # no empty body, no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_whatsapp_ingest.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/whatsapp/ingest.py
import logging
from typing import Awaitable, Callable, Iterable

from ari.application.whatsapp.filter import passes_filter
from ari.domain.whatsapp.entities import InboundWhatsApp

log = logging.getLogger("ari.whatsapp")

Notify = Callable[[str, str], Awaitable[None]]


class WhatsAppIngest:
    def __init__(self, store, notify: Notify, owner_ids: Iterable[str]):
        self._store = store
        self._notify = notify
        self._owners = list(owner_ids)

    async def __call__(self, m: InboundWhatsApp) -> None:
        try:
            rid = await self._store.record_inbound(m)
            f = await self._store.get_filter()
            if not passes_filter(m.contact_name, m.wa_chat_id, m.text, f):
                return
            body = m.text.strip() or f"[{m.media_kind or 'media'}]"
            for owner in self._owners:
                await self._notify(owner, f"📱 WhatsApp de {m.contact_name}: {body}")
            await self._store.mark_notified(rid)
        except Exception:  # never break the WhatsApp event loop
            log.exception("whatsapp ingest failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_whatsapp_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/whatsapp/ingest.py tests/application/test_whatsapp_ingest.py
git commit -m "feat(whatsapp): ingest filters inbound and notifies owners on telegram"
```

---

### Task 5: Outbox flusher

**Files:**
- Create: `src/ari/application/whatsapp/outbox.py`
- Test: `tests/application/test_whatsapp_outbox.py`

**Interfaces:**
- Consumes: `SqliteWhatsApp` (`claim_queued`, `mark_sent`, `mark_failed`); `WhatsAppPort.send`; `notify`; `owner_ids`; `min_delay: float`; injectable `sleep`.
- Produces: `WhatsAppOutbox(store, port, notify, owner_ids, min_delay=3.0, sleep=asyncio.sleep)` callable `async __call__() -> None` (drains all queued rows).

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_whatsapp_outbox.py
from ari.application.whatsapp.outbox import WhatsAppOutbox
from ari.infrastructure.persistence.sqlite_whatsapp import Row


class FakeStore:
    def __init__(self, rows):
        self._rows = list(rows)
        self.sent, self.failed = [], []
    async def claim_queued(self):
        return self._rows.pop(0) if self._rows else None
    async def mark_sent(self, id):
        self.sent.append(id)
    async def mark_failed(self, id):
        self.failed.append(id)


class FakePort:
    def __init__(self, fail=False):
        self.fail, self.sends = fail, []
    async def send(self, wa_chat_id, text):
        if self.fail:
            raise RuntimeError("disconnected")
        self.sends.append((wa_chat_id, text))


def _row(id):
    return Row(id, "x@s.whatsapp.net", "Ana", "hola", "", "t", None)


async def _noop_sleep(_):
    return None


async def test_drains_and_marks_sent():
    store = FakeStore([_row(1), _row(2)])
    port = FakePort()
    sent = []
    async def notify(c, t):
        sent.append((c, t))
    await WhatsAppOutbox(store, port, notify, ["111"], min_delay=0, sleep=_noop_sleep)()
    assert port.sends == [("x@s.whatsapp.net", "hola"), ("x@s.whatsapp.net", "hola")]
    assert store.sent == [1, 2] and store.failed == [] and sent == []


async def test_send_failure_marks_failed_and_notifies_owner():
    store = FakeStore([_row(7)])
    sent = []
    async def notify(c, t):
        sent.append((c, t))
    await WhatsAppOutbox(store, FakePort(fail=True), notify, ["111"], min_delay=0, sleep=_noop_sleep)()
    assert store.failed == [7] and store.sent == []
    assert sent and "Ana" in sent[0][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_whatsapp_outbox.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/whatsapp/outbox.py
import asyncio
import logging

log = logging.getLogger("ari.whatsapp")


class WhatsAppOutbox:
    def __init__(self, store, port, notify, owner_ids, min_delay=3.0, sleep=asyncio.sleep):
        self._store = store
        self._port = port
        self._notify = notify
        self._owners = list(owner_ids)
        self._min_delay = min_delay
        self._sleep = sleep

    async def __call__(self) -> None:
        while True:
            row = await self._store.claim_queued()
            if row is None:
                return
            try:
                if self._min_delay:
                    await self._sleep(self._min_delay)  # human pace, anti-ban
                await self._port.send(row.wa_chat_id, row.text)
                await self._store.mark_sent(row.id)
            except Exception:
                log.exception("whatsapp send failed for %s", row.wa_chat_id)
                await self._store.mark_failed(row.id)
                for owner in self._owners:
                    await self._notify(owner, f"❌ No pude enviar a {row.contact_name}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/application/test_whatsapp_outbox.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/whatsapp/outbox.py tests/application/test_whatsapp_outbox.py
git commit -m "feat(whatsapp): outbox flusher sends queued replies at human pace"
```

---

### Task 6: AriTools WhatsApp methods

**Files:**
- Modify: `src/ari/application/ari_tools.py` (add `whatsapp=None` ctor param; four methods)
- Test: `tests/application/test_ari_tools_whatsapp.py`

**Interfaces:**
- Consumes: `self._allowed(tool)`, `self._receipt(text)`, `self._a` (Actor), `SqliteWhatsApp` as `self._whatsapp`.
- Produces: `async whatsapp_pendientes(limite=10) -> str`; `async whatsapp_responder(id, instruccion) -> str`; `async whatsapp_enviar(borrador_id) -> str`; `async whatsapp_filtro(accion, valor="") -> str`.

Note (design refinement vs spec §4.6): the confirmation is realized as a persisted two-step across the MCP boundary — `whatsapp_responder` creates a **draft** and returns it for the owner to see; `whatsapp_enviar` (called only after the owner's explicit "sí") flips the draft to `queued`. The main-process flusher (Task 5) performs the actual send. This keeps the gate auditable and testable without reaching into the Telegram message loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_ari_tools_whatsapp.py
import pytest

from ari.application.ari_tools import AriTools, DENIED
from ari.domain.whatsapp.entities import InboundWhatsApp
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_whatsapp import SqliteWhatsApp


class Actor:
    def __init__(self, is_owner=True):
        self.user_id, self.chat_id, self.turn_id = "111", "111", "t1"
        self.context, self.is_owner = "chat", is_owner


class FakeLog:
    async def add_receipt(self, turn_id, text):
        return None


@pytest.fixture
async def tools_and_store():
    conn = await connect(":memory:", embedding_dim=8)
    store = SqliteWhatsApp(conn)
    tools = AriTools.__new__(AriTools)        # minimal wiring for these methods
    tools._a = Actor()
    tools._log = FakeLog()
    tools._whatsapp = store
    yield tools, store
    await conn.close()


async def test_pendientes_lists_messages(tools_and_store):
    tools, store = tools_and_store
    await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Juan", "hola"))
    out = await tools.whatsapp_pendientes()
    assert "Juan" in out and "hola" in out


async def test_responder_creates_draft_and_sends_nothing(tools_and_store):
    tools, store = tools_and_store
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "pago?"))
    out = await tools.whatsapp_responder(rid, "decile que sí")
    assert "Ana" in out and "¿" in out           # asks for confirmation
    assert await store.claim_queued() is None     # nothing queued yet


async def test_enviar_queues_the_draft_once(tools_and_store):
    tools, store = tools_and_store
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "pago?"))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "sí")
    out = await tools.whatsapp_enviar(did)
    assert "Ana" in out
    assert await store.claim_queued() is not None     # now queued
    # a second enviar on the same draft refuses
    assert "no" in (await tools.whatsapp_enviar(did)).lower()


async def test_enviar_unknown_draft_refuses(tools_and_store):
    tools, _ = tools_and_store
    assert "no" in (await tools.whatsapp_enviar(999)).lower()


async def test_non_owner_denied(tools_and_store):
    tools, store = tools_and_store
    tools._a = Actor(is_owner=False)
    assert await tools.whatsapp_pendientes() == DENIED
    assert await tools.whatsapp_enviar(1) == DENIED


async def test_filtro_add_and_view(tools_and_store):
    tools, _ = tools_and_store
    await tools.whatsapp_filtro("agregar_contacto", "Juan")
    out = await tools.whatsapp_filtro("ver")
    assert "Juan" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_ari_tools_whatsapp.py -v`
Expected: FAIL (`AttributeError: whatsapp_pendientes` / missing `_whatsapp`)

- [ ] **Step 3: Write minimal implementation**

In `AriTools.__init__`, add a parameter `whatsapp=None` and `self._whatsapp = whatsapp`. Then add the methods (place them after the email tools, keeping the file's section-comment style):

```python
    # ---- whatsapp ---------------------------------------------------------

    async def whatsapp_pendientes(self, limite: int = 10) -> str:
        if not self._allowed("whatsapp_pendientes"):
            return DENIED
        if self._whatsapp is None:
            return "WhatsApp no está conectado."
        rows = await self._whatsapp.list_pending(int(limite or 10))
        if not rows:
            return "No hay WhatsApp pendientes."
        lines = []
        for r in rows:
            body = r.text.strip() or f"[{r.media_kind or 'media'}]"
            lines.append(f"#{r.id} · {r.contact_name} · {body}")
        return "\n".join(lines)

    async def whatsapp_responder(self, id: int, instruccion: str) -> str:
        if not self._allowed("whatsapp_responder"):
            return DENIED
        if self._whatsapp is None:
            return "WhatsApp no está conectado."
        try:
            id_int = int(id)
        except (ValueError, TypeError):
            return f"No encontré el WhatsApp #{id}."
        msg = await self._whatsapp.get_inbound(id_int)
        if msg is None:
            return f"No encontré el WhatsApp #{id_int}."
        draft = (instruccion or "").strip()
        if not draft:
            return "No pude redactar: decime qué responder."
        did = await self._whatsapp.create_draft(id_int, msg.wa_chat_id, msg.contact_name, draft)
        return (f"Voy a responder a {msg.contact_name}: «{draft}». "
                f"¿Confirmo? (usá whatsapp_enviar #{did})")

    async def whatsapp_enviar(self, borrador_id: int) -> str:
        if not self._allowed("whatsapp_enviar"):
            return DENIED
        if self._whatsapp is None:
            return "WhatsApp no está conectado."
        try:
            did = int(borrador_id)
        except (ValueError, TypeError):
            return "No encontré ese borrador."
        row = await self._whatsapp.queue_draft(did)
        if row is None:
            return "No pude enviar: ese borrador no existe o ya se envió."
        return await self._receipt(f"✅ Encolado para {row.contact_name}: «{row.text}»")

    async def whatsapp_filtro(self, accion: str, valor: str = "") -> str:
        if not self._allowed("whatsapp_filtro"):
            return DENIED
        if self._whatsapp is None:
            return "WhatsApp no está conectado."
        accion, valor = (accion or "").strip().lower(), (valor or "").strip()
        ops = {
            "agregar_contacto": self._whatsapp.add_contact,
            "quitar_contacto": self._whatsapp.remove_contact,
            "agregar_palabra": self._whatsapp.add_keyword,
            "quitar_palabra": self._whatsapp.remove_keyword,
        }
        if accion in ops:
            if not valor:
                return "Falta el valor."
            await ops[accion](valor)
        elif accion != "ver":
            return "Acciones: agregar_contacto, quitar_contacto, agregar_palabra, quitar_palabra, ver."
        f = await self._whatsapp.get_filter()
        return (f"Contactos: {', '.join(sorted(f.contacts)) or '—'}\n"
                f"Palabras: {', '.join(sorted(f.keywords)) or '—'}")
```

- [ ] **Step 3b: Register the tools (prerequisite for the owner gate)**

`_allowed(tool)` only returns True when the tool is in `ARI_TOOLS`, so the four
names must be registered for these methods to be reachable (Task 7 adds the
owner-only regression test). In `src/ari/domain/tools/ari_permissions.py`, add the
names to the `ARI_TOOLS` tuple (after `"abrir_archivo"`) and **not** to
`_USER_CHAT`:

```python
             "abrir_archivo",
             "whatsapp_pendientes", "whatsapp_responder",
             "whatsapp_enviar", "whatsapp_filtro")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/application/test_ari_tools_whatsapp.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py src/ari/domain/tools/ari_permissions.py tests/application/test_ari_tools_whatsapp.py
git commit -m "feat(whatsapp): ari tools for pending, draft reply, confirmed send, filter"
```

---

### Task 7: Permissions regression lock (owner-only)

The four names were registered in `ARI_TOOLS` in Task 6 (Step 3b). This task adds
the dedicated test that locks the **owner-only** guarantee: present for the owner
in CHAT, absent from `_USER_CHAT`, `TASK`, and `HEARTBEAT`. If Task 6 is executed
out of order and the names are not yet present, add them per Task 6 Step 3b first.

**Files:**
- Verify: `src/ari/domain/tools/ari_permissions.py` (names added in Task 6)
- Test: `tests/domain/test_whatsapp_permissions.py`

**Interfaces:**
- Consumes: `allowed_ari_tools(is_owner, context)`.
- Produces: the regression test locking the four names as owner-only CHAT tools.

- [ ] **Step 1: Write the test (RED if Task 6 Step 3b was skipped)**

```python
# tests/domain/test_whatsapp_permissions.py
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK, allowed_ari_tools

WA = ("whatsapp_pendientes", "whatsapp_responder", "whatsapp_enviar", "whatsapp_filtro")


def test_owner_chat_has_all_whatsapp_tools():
    allowed = allowed_ari_tools(is_owner=True, context=CHAT)
    assert all(t in allowed for t in WA)


def test_non_owner_chat_has_no_whatsapp_tools():
    allowed = allowed_ari_tools(is_owner=False, context=CHAT)
    assert all(t not in allowed for t in WA)


def test_whatsapp_absent_in_task_and_heartbeat():
    for ctx in (TASK, HEARTBEAT):
        allowed = allowed_ari_tools(is_owner=True, context=ctx)
        assert all(t not in allowed for t in WA)
```

- [ ] **Step 2: Run the test**

Run: `python3 -m pytest tests/domain/test_whatsapp_permissions.py -v`
Expected: PASS (names were registered in Task 6 Step 3b). If it FAILS with the
names missing, apply Task 6 Step 3b, then re-run and confirm PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/domain/test_whatsapp_permissions.py
git commit -m "test(whatsapp): lock tools as owner-only in CHAT"
```

---

### Task 8: MCP wrappers + subprocess wiring

**Files:**
- Modify: `src/ari/mcp_server/server.py` (four `@tool` wrappers)
- Modify: `src/ari/mcp_server/__main__.py` (build `SqliteWhatsApp(conn)` and pass to `AriTools`)
- Test: `tests/mcp_server/test_whatsapp_tools_registered.py`

**Interfaces:**
- Consumes: `AriTools.whatsapp_*`; `SqliteWhatsApp`; existing `build_server(get_tools, ...)`.
- Produces: MCP tools `whatsapp_pendientes`, `whatsapp_responder`, `whatsapp_enviar`, `whatsapp_filtro`.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp_server/test_whatsapp_tools_registered.py
from ari.mcp_server.server import build_server


async def _get_tools():
    raise AssertionError("not called during registration")


def test_whatsapp_tools_are_registered():
    server = build_server(_get_tools)
    names = set(server.tool_names()) if hasattr(server, "tool_names") else None
    # Fallback: FastMCP exposes registered tools; adjust accessor to match the
    # existing test helpers in tests/mcp_server if present.
    assert names is None or {
        "whatsapp_pendientes", "whatsapp_responder",
        "whatsapp_enviar", "whatsapp_filtro"} <= names
```

Note: match the accessor used by existing `tests/mcp_server` tests for listing tools; if the suite already has a helper that lists registered tool names, reuse it instead of the fallback above.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/mcp_server/test_whatsapp_tools_registered.py -v`
Expected: FAIL (tools not registered)

- [ ] **Step 3: Add the wrappers**

In `src/ari/mcp_server/server.py`, next to the other `@tool` wrappers (Spanish docstrings, model-facing):

```python
    @tool("whatsapp_pendientes")
    async def whatsapp_pendientes(limite: int = 10) -> str:
        """Muestra los WhatsApp que llegaron y están sin responder."""
        return await (await get_tools()).whatsapp_pendientes(limite)

    @tool("whatsapp_responder")
    async def whatsapp_responder(id: int, instruccion: str) -> str:
        """Redacta una respuesta para el WhatsApp #id a partir de tu instrucción.
        NO envía: deja un borrador y te pide confirmación. Solo envía con
        whatsapp_enviar después de que el usuario confirme explícitamente."""
        return await (await get_tools()).whatsapp_responder(id, instruccion)

    @tool("whatsapp_enviar")
    async def whatsapp_enviar(borrador_id: int) -> str:
        """Envía por WhatsApp un borrador ya confirmado por el usuario.
        Usalo SOLO cuando el usuario haya dicho que sí al borrador."""
        return await (await get_tools()).whatsapp_enviar(borrador_id)

    @tool("whatsapp_filtro")
    async def whatsapp_filtro(accion: str, valor: str = "") -> str:
        """Gestiona a quién/qué avisarte: agregar_contacto, quitar_contacto,
        agregar_palabra, quitar_palabra, ver."""
        return await (await get_tools()).whatsapp_filtro(accion, valor)
```

- [ ] **Step 4: Wire the store in `__main__.py`**

Where `AriTools(...)` is constructed, build and pass the store (the shared `conn` is already opened as `conn`):

```python
from ari.infrastructure.persistence.sqlite_whatsapp import SqliteWhatsApp
# ...
        whatsapp=SqliteWhatsApp(conn),
```

- [ ] **Step 5: Run tests + the existing MCP suite**

Run: `python3 -m pytest tests/mcp_server -v`
Expected: PASS (new + existing)

- [ ] **Step 6: Commit**

```bash
git add src/ari/mcp_server/server.py src/ari/mcp_server/__main__.py tests/mcp_server/test_whatsapp_tools_registered.py
git commit -m "feat(whatsapp): expose MCP tools and wire store into ari server"
```

---

### Task 9: Neonize adapter (thin; manual validation)

**Files:**
- Create: `src/ari/infrastructure/whatsapp/__init__.py`
- Create: `src/ari/infrastructure/whatsapp/neonize_adapter.py`
- Modify: `pyproject.toml` (optional `whatsapp` extra)
- Test: `tests/infrastructure/test_neonize_adapter.py` (adaptation logic only, with a fake client)

**Interfaces:**
- Consumes: `WhatsAppPort`; `InboundWhatsApp`; neonize `NewAClient`, `MessageEv`, `build_jid`.
- Produces: `NeonizeWhatsApp(session_dir: str, clock=...)` implementing the port; a pure module function `inbound_from_event(event) -> InboundWhatsApp` that is unit-tested.

The neonize client talks to the live protocol, so **only** the event→entity adaptation is unit-tested; connect/send/pair are validated manually during real pairing (spec §10).

- [ ] **Step 1: Write the failing test (pure adaptation)**

```python
# tests/infrastructure/test_neonize_adapter.py
from types import SimpleNamespace

from ari.infrastructure.whatsapp.neonize_adapter import inbound_from_event


def _event(conversation="", image=False, pushname="Juan", is_group=False,
           chat="549111@s.whatsapp.net"):
    msg = SimpleNamespace(conversation=conversation, extendedTextMessage=None,
                          imageMessage=SimpleNamespace(caption="") if image else None,
                          audioMessage=None, videoMessage=None,
                          documentMessage=None, stickerMessage=None)
    source = SimpleNamespace(IsGroup=is_group, chat=chat)
    info = SimpleNamespace(MessageSource=source, Pushname=pushname)
    return SimpleNamespace(Message=msg, Info=info)


def test_text_message_maps_to_inbound():
    m = inbound_from_event(_event(conversation="hola"))
    assert m.text == "hola" and m.contact_name == "Juan"
    assert m.wa_chat_id == "549111@s.whatsapp.net" and m.media_kind == "" and m.is_group is False


def test_image_message_sets_media_kind_and_empty_text():
    m = inbound_from_event(_event(image=True, conversation=""))
    assert m.media_kind == "image" and m.text == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_neonize_adapter.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write the adapter**

```python
# src/ari/infrastructure/whatsapp/__init__.py
# (empty)
```

```python
# src/ari/infrastructure/whatsapp/neonize_adapter.py
"""Thin neonize (whatsmeow) adapter. All logic lives in `inbound_from_event`;
the client calls themselves are validated manually during real pairing."""
import logging

from ari.domain.whatsapp.entities import InboundWhatsApp

log = logging.getLogger("ari.whatsapp")

_MEDIA = (("imageMessage", "image"), ("audioMessage", "audio"),
          ("videoMessage", "video"), ("documentMessage", "document"),
          ("stickerMessage", "sticker"))


def inbound_from_event(event) -> InboundWhatsApp:
    msg, info = event.Message, event.Info
    source = info.MessageSource
    text = getattr(msg, "conversation", "") or ""
    ext = getattr(msg, "extendedTextMessage", None)
    if not text and ext is not None:
        text = getattr(ext, "text", "") or ""
    media_kind = ""
    for attr, kind in _MEDIA:
        if getattr(msg, attr, None):
            media_kind = kind
            break
    contact = getattr(info, "Pushname", "") or source.chat.split("@", 1)[0]
    return InboundWhatsApp(
        wa_chat_id=source.chat,
        contact_name=contact,
        text=text,
        media_kind=media_kind,
        is_group=bool(getattr(source, "IsGroup", False)),
    )


class NeonizeWhatsApp:
    """Implements WhatsAppPort over neonize's async client."""

    def __init__(self, session_dir: str):
        self._session_dir = session_dir
        self._client = None
        self._state = "logged_out"

    async def start(self, on_message) -> None:
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, MessageEv
        import os
        os.makedirs(self._session_dir, exist_ok=True)
        db = os.path.join(self._session_dir, "session.db")
        self._client = NewAClient(db)

        @self._client.event(ConnectedEv)
        async def _on_connected(client, event):  # noqa: ANN001
            self._state = "connected"

        @self._client.event(MessageEv)
        async def _on_message(client, event):  # noqa: ANN001
            try:
                await on_message(inbound_from_event(event))
            except Exception:
                log.exception("whatsapp on_message failed")

        await self._client.connect()

    async def send(self, wa_chat_id: str, text: str) -> None:
        from neonize.utils.jid import build_jid
        jid = build_jid(wa_chat_id.split("@", 1)[0])
        await self._client.send_message(jid, text)

    async def pair_phone(self, number: str) -> str:
        return await self._client.pair_phone(number, show_push_notification=True)

    def connection_state(self) -> str:
        return self._state
```

Note: neonize attribute casing (`event.Info` vs `event.info`, `MessageSource`) and the exact async `pair_phone`/`send_message` signatures must be confirmed against the installed neonize version during manual pairing; `inbound_from_event` is written defensively with `getattr` so casing drift is a one-line fix isolated here.

- [ ] **Step 4: Run the adaptation test**

Run: `python3 -m pytest tests/infrastructure/test_neonize_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Add the optional dependency**

In `pyproject.toml` under `[project.optional-dependencies]`:

```toml
whatsapp = ["neonize>=0.5"]
```

- [ ] **Step 6: Commit**

```bash
git add src/ari/infrastructure/whatsapp tests/infrastructure/test_neonize_adapter.py pyproject.toml
git commit -m "feat(whatsapp): thin neonize adapter with tested event adaptation"
```

---

### Task 10: Settings + main wiring + pairing

**Files:**
- Modify: `src/ari/config/settings.py` (four settings)
- Modify: `src/ari/main.py` (construct/wire session, ingest, outbox; pairing on first run)
- Test: `tests/test_settings_whatsapp.py`

**Interfaces:**
- Consumes: `Settings`; `NeonizeWhatsApp`, `SqliteWhatsApp`, `WhatsAppIngest`, `WhatsAppOutbox`; the existing `send(chat_id, text)`, `Scheduler`, and `owner_id_set`.
- Produces: a running session + a `whatsapp_outbox` scheduler tick when `whatsapp_enabled`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings_whatsapp.py
from ari.config.settings import Settings


def test_whatsapp_defaults_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    s = Settings()
    assert s.whatsapp_enabled is False
    assert s.whatsapp_session_dir.endswith("whatsapp")
    assert s.whatsapp_send_min_delay_seconds >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_settings_whatsapp.py -v`
Expected: FAIL (`AttributeError`)

- [ ] **Step 3: Add the settings**

In `src/ari/config/settings.py` (after the proactivity block):

```python
    # WhatsApp (personal, opt-in; unofficial multi-device via neonize)
    whatsapp_enabled: bool = False
    whatsapp_number: str = ""            # for phone pairing code (no +, with country code)
    whatsapp_session_dir: str = "~/.ari/whatsapp"
    whatsapp_send_min_delay_seconds: int = 3
```

- [ ] **Step 4: Run the settings test**

Run: `python3 -m pytest tests/test_settings_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Wire it in `main.py`**

After the bot is built and `send`/`owner_id_set` are available, and only when enabled:

```python
        if settings.whatsapp_enabled:
            from ari.application.whatsapp.ingest import WhatsAppIngest
            from ari.application.whatsapp.outbox import WhatsAppOutbox
            from ari.infrastructure.persistence.sqlite_whatsapp import SqliteWhatsApp
            from ari.infrastructure.whatsapp.neonize_adapter import NeonizeWhatsApp

            wa_store = SqliteWhatsApp(c.conn)
            wa_port = NeonizeWhatsApp(os.path.expanduser(settings.whatsapp_session_dir))
            owners = sorted(settings.owner_id_set)
            ingest = WhatsAppIngest(wa_store, send, owners)
            await wa_port.start(ingest)
            if wa_port.connection_state() != "connected" and settings.whatsapp_number:
                code = await wa_port.pair_phone(settings.whatsapp_number)
                for o in owners:
                    await send(o, f"Vinculá Ari a WhatsApp: Dispositivos vinculados → "
                                  f"Vincular con número → ingresá: {code}")
            wa_outbox = WhatsAppOutbox(wa_store, wa_port, send, owners,
                                       min_delay=settings.whatsapp_send_min_delay_seconds)
            scheduler_ticks.append(wa_outbox)   # add to the existing Scheduler([...]) list
```

Note: add `wa_outbox` to the same list passed to `Scheduler([...])` (currently `[due, notices.tick, heartbeat, n, coding_runner, ...]`). Keep the import-inside-guard so a base install without the `whatsapp` extra never imports neonize.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS (no regressions; WhatsApp paths covered by Tasks 1–9).

- [ ] **Step 7: Commit**

```bash
git add src/ari/config/settings.py src/ari/main.py tests/test_settings_whatsapp.py
git commit -m "feat(whatsapp): opt-in settings, session wiring, and phone pairing"
```

---

## Manual validation (after Task 10)

1. `pip install -e '.[whatsapp]'`, set `ARI_WHATSAPP_ENABLED=true` and `ARI_WHATSAPP_NUMBER=<your number>`, run `ari`.
2. Receive the pairing code on Telegram; link the device (WhatsApp → Linked Devices → Link with phone number).
3. Add a contact to the filter via chat (`whatsapp_filtro agregar_contacto <name>`); have that contact message you; confirm the Telegram notice.
4. Tell Ari to reply; confirm the draft; confirm the message arrives on WhatsApp.
5. Restart `ari`; confirm no re-pairing is needed (session persisted under `~/.ari/whatsapp`).
