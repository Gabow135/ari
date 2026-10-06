# LAN file-serving (`abrir_archivo`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owner an owner-only `abrir_archivo(ruta)` tool that mints a one-time LAN HTTPS link to open any file on the Mac in the browser, except a hard secrets denylist.

**Architecture:** Reuse the `vault_web` LAN HTTPS server (cert, LAN-IP detection, on-demand start/auto-stop). Because the ari MCP server is a subprocess, the conversational tool enqueues a request to a new SQLite queue (mirroring `pedir_credenciales`), and a main-process `FileServeRequestRunner` mints the link via a new `VaultWebMaintainer.new_file_link` and sends it. A new single-use `FileLinkStore` maps token→path; a new `/f/<token>` route streams the file; a shared `is_denied` predicate guards at tool, mint, and serve time.

**Tech Stack:** Python ≥3.11, `uv`, stdlib (`http.server`, `ssl`, `socket`, `secrets`, `mimetypes`, `threading`, `os`), `aiosqlite`, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-05-lan-file-serving-design.md`

## Global Constraints

- Python floor `>=3.11`; **no new third-party dependencies** (stdlib + existing aiosqlite only).
- pytest-asyncio `asyncio_mode = "auto"` — bare `async def test_*`.
- Tool identifiers and user-facing docstrings/messages are **Spanish** (project convention); internal code/comments English.
- Commits: Conventional Commits, **no `Co-Authored-By` / AI attribution**.
- Work on a feature branch off `main`: `git checkout -b feat/lan-file-serving` before Task 1.
- `abrir_archivo` is **owner-only** and CHAT-only.
- Denylist enforced at **three** points (tool, runner mint, server serve), `realpath` before each check.
- File token is **single-use** (consumed on claim) + short TTL + TLS.
- `git add` only the files each task names — never `git add -A`/`.` (untracked docs under `docs/superpowers/` must not be committed).

## Review Focus

- **Symlink to a denied target** (e.g. `~/Desktop/x` → `~/.ssh/id_rsa`) must be denied — `realpath` before the denylist check. (Task 2)
- **Token reuse**: a second GET on the same `/f/<token>` must 403 — single-use consumption. (Task 1 + Task 4)
- **Path denied/removed between mint and serve**: the serve-time recheck (denylist + regular-file) refuses. (Task 4)
- **Non-owner invoking `abrir_archivo`**: returns `DENIED`, nothing enqueued. (Task 7)
- **Non-regular file or relative/`~` path**: directories/devices refused; `~` and `..` normalized before checks. (Task 2 + Task 7)

---

### Task 1: `FileLinkStore` (single-use token→path store)

**Files:**
- Create: `src/ari/infrastructure/vault_web/file_link_store.py`
- Test: `tests/infrastructure/vault_web/test_file_link_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class FileLinkStore(ttl_seconds: int, clock: Callable[[], float])` with `create(path: str) -> str`, `claim(token: str) -> str | None` (single-use), `active_count() -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/infrastructure/vault_web/test_file_link_store.py
from ari.infrastructure.vault_web.file_link_store import FileLinkStore


def test_claim_returns_path_once_then_none():
    clock = lambda: 100.0
    s = FileLinkStore(ttl_seconds=60, clock=clock)
    tok = s.create("/tmp/a.pdf")
    assert s.claim(tok) == "/tmp/a.pdf"
    assert s.claim(tok) is None  # single-use


def test_expired_token_is_none():
    now = {"t": 100.0}
    s = FileLinkStore(ttl_seconds=60, clock=lambda: now["t"])
    tok = s.create("/tmp/a.pdf")
    now["t"] = 161.0
    assert s.claim(tok) is None


def test_unknown_token_is_none():
    s = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    assert s.claim("nope") is None


def test_active_count_reflects_create_and_claim():
    s = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    t1 = s.create("/tmp/a"); s.create("/tmp/b")
    assert s.active_count() == 2
    s.claim(t1)
    assert s.active_count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/vault_web/test_file_link_store.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
# src/ari/infrastructure/vault_web/file_link_store.py
"""Single-use, short-lived token->filepath store for the LAN file server.
A token is consumed (removed) on claim, so a leaked link works at most once."""
import secrets
from collections.abc import Callable


class FileLinkStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float]):
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[str, float]] = {}  # token -> (path, expiry)

    def create(self, path: str) -> str:
        token = secrets.token_urlsafe(32)
        self._entries[token] = (path, self._clock() + self._ttl)
        return token

    def _sweep(self) -> None:
        now = self._clock()
        for t in [t for t, (_p, exp) in self._entries.items() if exp <= now]:
            del self._entries[t]

    def claim(self, token: str) -> str | None:
        self._sweep()
        entry = self._entries.pop(token, None)  # single-use: remove on claim
        return entry[0] if entry is not None else None

    def active_count(self) -> int:
        self._sweep()
        return len(self._entries)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/vault_web/test_file_link_store.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/file_link_store.py tests/infrastructure/vault_web/test_file_link_store.py
git commit -m "feat(vault_web): single-use file link store"
```

---

### Task 2: `is_denied` secrets denylist

**Files:**
- Create: `src/ari/infrastructure/vault_web/fs_denylist.py`
- Test: `tests/infrastructure/vault_web/test_fs_denylist.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `default_denied_roots(vault_path: str, claude_config_dir: str, cert_key_path: str | None = None) -> list[str]`
  - `is_denied(path: str, denied_roots: list[str]) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/infrastructure/vault_web/test_fs_denylist.py
import os

from ari.infrastructure.vault_web.fs_denylist import default_denied_roots, is_denied


def test_denies_configured_roots_and_env_and_allows_others(tmp_path):
    vault = tmp_path / "vault.enc"
    vault.write_text("x")
    ssh = tmp_path / ".ssh"; ssh.mkdir(); (ssh / "id_rsa").write_text("k")
    roots = [os.path.realpath(str(vault)), os.path.realpath(str(ssh))]
    assert is_denied(str(vault), roots) is True
    assert is_denied(str(ssh / "id_rsa"), roots) is True
    ok = tmp_path / "factura.pdf"; ok.write_text("p")
    assert is_denied(str(ok), roots) is False
    env = tmp_path / ".env"; env.write_text("S=1")
    assert is_denied(str(env), roots) is True  # any .env by basename


def test_symlink_to_denied_target_is_denied(tmp_path):
    secret = tmp_path / "secret"; secret.mkdir(); (secret / "k").write_text("k")
    link = tmp_path / "link"
    os.symlink(str(secret / "k"), str(link))
    roots = [os.path.realpath(str(secret))]
    assert is_denied(str(link), roots) is True  # realpath resolves the symlink


def test_default_denied_roots_includes_home_secret_dirs(tmp_path):
    roots = default_denied_roots(str(tmp_path / "vault.enc"), str(tmp_path / ".ari-claude"))
    home = os.path.expanduser("~")
    assert os.path.join(home, ".ssh") in roots
    assert os.path.join(home, "Library", "Keychains") in roots
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/vault_web/test_fs_denylist.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
# src/ari/infrastructure/vault_web/fs_denylist.py
"""Hard denylist of secret paths that the LAN file server must never serve."""
import os


def default_denied_roots(vault_path: str, claude_config_dir: str,
                         cert_key_path: str | None = None) -> list[str]:
    home = os.path.expanduser("~")
    roots = [
        os.path.realpath(os.path.expanduser(vault_path)),
        os.path.realpath(os.path.abspath(claude_config_dir)),
        os.path.join(home, ".ssh"),
        os.path.join(home, ".aws"),
        os.path.join(home, ".gnupg"),
        os.path.join(home, "Library", "Keychains"),
    ]
    if cert_key_path:
        roots.append(os.path.realpath(os.path.expanduser(cert_key_path)))
    return [os.path.realpath(r) for r in roots]


def is_denied(path: str, denied_roots: list[str]) -> bool:
    real = os.path.realpath(os.path.expanduser(path))
    if os.path.basename(real) == ".env":
        return True
    for root in denied_roots:
        if real == root or real.startswith(root + os.sep):
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/vault_web/test_fs_denylist.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/fs_denylist.py tests/infrastructure/vault_web/test_fs_denylist.py
git commit -m "feat(vault_web): secrets denylist for file serving"
```

---

### Task 3: `FileRequest` domain + `SqliteFileRequests` queue + schema

**Files:**
- Create: `src/ari/domain/files/__init__.py` (empty), `src/ari/domain/files/requests.py`
- Create: `src/ari/infrastructure/persistence/sqlite_file_requests.py`
- Modify: `src/ari/infrastructure/persistence/db.py` (add the `file_requests` table to `_SCHEMA`)
- Test: `tests/infrastructure/persistence/test_sqlite_file_requests.py`

**Interfaces:**
- Consumes: `connect` from `ari.infrastructure.persistence.db` (existing).
- Produces:
  - `FileRequest(id, user_id, chat_id, path, created_at)`; constants `PENDING, TAKEN, DONE, FAILED, SKIPPED`.
  - `SqliteFileRequests(conn)` with `add(user_id, chat_id, path) -> int`, `claim_pending() -> list[FileRequest]`, `reset_taken() -> list[FileRequest]`, `finish(id, status, detail=None)`, `status_of(id) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/persistence/test_sqlite_file_requests.py
from ari.domain.files.requests import DONE, PENDING, TAKEN
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests


async def test_add_claim_finish_roundtrip():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn)
    try:
        rid = await q.add("42", "42", "/tmp/factura.pdf")
        assert await q.status_of(rid) == PENDING
        [req] = await q.claim_pending()
        assert (req.user_id, req.chat_id, req.path) == ("42", "42", "/tmp/factura.pdf")
        assert await q.status_of(rid) == TAKEN
        assert await q.claim_pending() == []  # nothing left pending
        await q.finish(rid, DONE)
        assert await q.status_of(rid) == DONE
    finally:
        await conn.close()


async def test_reset_taken_requeues():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn)
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await q.claim_pending()
        [req] = await q.reset_taken()
        assert req.id == rid
        assert await q.status_of(rid) == PENDING
    finally:
        await conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/persistence/test_sqlite_file_requests.py -v`
Expected: FAIL — module/table missing.

- [ ] **Step 3: Write the implementation**

```python
# src/ari/domain/files/requests.py
from dataclasses import dataclass
from datetime import datetime

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"


@dataclass(frozen=True)
class FileRequest:
    id: int
    user_id: str
    chat_id: str
    path: str
    created_at: datetime
```

```python
# src/ari/infrastructure/persistence/sqlite_file_requests.py
import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.files.requests import PENDING, TAKEN, FileRequest

_COLS = "id, user_id, chat_id, path, created_at"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _request(r) -> FileRequest:
    return FileRequest(r["id"], r["user_id"], r["chat_id"], r["path"],
                       datetime.fromisoformat(r["created_at"]))


class SqliteFileRequests:
    """Queue between Ari's MCP server (writer) and the bot (claimer)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str, path: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO file_requests (user_id, chat_id, path, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, chat_id, path, PENDING, _iso(datetime.now(timezone.utc))))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[FileRequest]:
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE file_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (TAKEN, PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def reset_taken(self) -> list[FileRequest]:
        async with self._lock:
            cur = await self._conn.execute(
                f"UPDATE file_requests SET status = ? WHERE status = ? RETURNING {_COLS}",
                (PENDING, TAKEN))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((_request(r) for r in rows), key=lambda r: r.id)

    async def finish(self, request_id: int, status: str, detail: str | None = None) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE file_requests SET status = ?, detail = ? WHERE id = ?",
                (status, detail, request_id))
            await self._conn.commit()

    async def status_of(self, request_id: int) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT status FROM file_requests WHERE id = ?", (request_id,))
        return rows[0]["status"] if rows else None
```

In `src/ari/infrastructure/persistence/db.py`, add this table to `_SCHEMA`, next to the `credential_requests` table and matching its exact style (read that table first and mirror its `CREATE TABLE IF NOT EXISTS` form and column types):

```sql
CREATE TABLE IF NOT EXISTS file_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/persistence/test_sqlite_file_requests.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/files/ src/ari/infrastructure/persistence/sqlite_file_requests.py src/ari/infrastructure/persistence/db.py tests/infrastructure/persistence/test_sqlite_file_requests.py
git commit -m "feat(files): sqlite file-serve request queue"
```

---

### Task 4: `/f/<token>` route in `VaultWebServer`

**Files:**
- Modify: `src/ari/infrastructure/vault_web/server.py` (`_Handler` routing + `_serve_file`; `VaultWebServer.__init__`/`start` to carry `file_store` + `denied_roots`)
- Test: `tests/infrastructure/vault_web/test_server_file_route.py`

**Interfaces:**
- Consumes: `FileLinkStore` (Task 1), `is_denied` (Task 2).
- Produces: `VaultWebServer(bind, port, cert, key, vault, store, names, file_store=None, denied_roots=())`; a `GET /f/<token>` that streams the file once.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/vault_web/test_server_file_route.py
import http.client
import ssl

import pytest

from ari.infrastructure.vault_web.cert import ensure_cert
from ari.infrastructure.vault_web.file_link_store import FileLinkStore
from ari.infrastructure.vault_web.server import VaultWebServer


class _Vault:
    def names(self): return []


@pytest.fixture
def served(tmp_path):
    f = tmp_path / "factura.txt"; f.write_text("TOTAL 16,61")
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    store = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    srv = VaultWebServer("127.0.0.1", 0, cert, key, _Vault(), store, [],
                         file_store=store if False else FileLinkStore(60, lambda: 0.0),
                         denied_roots=[])
    # use one file store we can mint from:
    srv._file_store_for_test = None
    srv.start()
    yield srv, f
    srv.stop()


def _get(port, path):
    ctx = ssl._create_unverified_context()
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body
```

> NOTE to implementer: the fixture above is awkward because the server needs the SAME `file_store` it serves from. Write the fixture so the file store is created first, passed into `VaultWebServer(..., file_store=fs, denied_roots=[])`, a token minted via `fs.create(str(f))`, then assert:

```python
def test_file_route_streams_once_then_403(tmp_path):
    f = tmp_path / "factura.txt"; f.write_text("TOTAL 16,61")
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    fs = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    srv = VaultWebServer("127.0.0.1", 0, cert, key, _Vault(), fs, [],
                         file_store=fs, denied_roots=[])
    srv.start()
    try:
        tok = fs.create(str(f))
        status, body = _get(srv.port, f"/f/{tok}")
        assert status == 200 and body == b"TOTAL 16,61"
        status2, _ = _get(srv.port, f"/f/{tok}")
        assert status2 == 403  # single-use consumed
    finally:
        srv.stop()


def test_file_route_denied_path_refused(tmp_path):
    secret = tmp_path / ".env"; secret.write_text("S=1")
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    fs = FileLinkStore(ttl_seconds=60, clock=lambda: 0.0)
    srv = VaultWebServer("127.0.0.1", 0, cert, key, _Vault(), fs, [],
                         file_store=fs, denied_roots=[])
    srv.start()
    try:
        tok = fs.create(str(secret))  # a .env sneaked into the store
        status, _ = _get(srv.port, f"/f/{tok}")
        assert status == 404  # serve-time denylist recheck (.env by basename)
    finally:
        srv.stop()
```

(Delete the awkward `served` fixture scaffold above; keep only the two `test_file_route_*` functions and the `_Vault`/`_get` helpers.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/vault_web/test_server_file_route.py -v`
Expected: FAIL — `VaultWebServer.__init__` has no `file_store`/`denied_roots`; no `/f` route.

- [ ] **Step 3: Write the implementation**

In `src/ari/infrastructure/vault_web/server.py`: ensure `import os` and `import mimetypes` at the top. Replace the `_Handler._token` helper usage with a `_route` that recognizes both prefixes, route `/f` in `do_GET`, keep `do_POST` vault-only, and add `_serve_file`:

```python
    def _route(self):
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        if len(parts) == 2 and parts[0] in ("v", "f"):
            return parts[0], parts[1]
        return None, None

    def do_GET(self):
        kind, token = self._route()
        if kind == "f":
            return self._serve_file(token)
        if kind != "v":
            return self._reply(404, "no encontrado", "text/plain; charset=utf-8")
        if not self.server.store.valid(token):
            return self._reply(403, "Link vencido o inválido.", "text/plain; charset=utf-8")
        have = set(self.server.vault.names())
        self._reply(200, _render(token, self.server.names, have, []))

    def _serve_file(self, token):
        from ari.infrastructure.vault_web.fs_denylist import is_denied
        store = getattr(self.server, "file_store", None)
        path = store.claim(token) if store is not None else None
        if path is None:
            return self._reply(403, "Link vencido o inválido.", "text/plain; charset=utf-8")
        if is_denied(path, getattr(self.server, "denied_roots", ())) or not os.path.isfile(path):
            log.warning("file serve refused: %s", path)
            return self._reply(404, "no encontrado", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition",
                                 f'inline; filename="{os.path.basename(path)}"')
                self.end_headers()
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except OSError as exc:
            log.warning("file serve failed for %s: %s", path, exc)
            return
        log.info("served file %s (%d bytes)", path, size)
```

Update `do_POST` to use `_route()` and only accept `kind == "v"` (replace its `self._token()` call with `kind, token = self._route()` then `if kind != "v": return self._reply(404, ...)`).

Update `VaultWebServer.__init__` to accept and store `file_store=None` and `denied_roots=()`, and in `start()` set them on the httpd alongside the existing attrs:

```python
        httpd.vault, httpd.store, httpd.names = self._vault, self._store, self._names
        httpd.file_store, httpd.denied_roots = self._file_store, self._denied_roots
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/vault_web/test_server_file_route.py tests/infrastructure/vault_web/test_server.py -v`
Expected: PASS (the new file-route tests AND the existing vault `/v` server tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/server.py tests/infrastructure/vault_web/test_server_file_route.py
git commit -m "feat(vault_web): /f token route streams a file once with denylist recheck"
```

---

### Task 5: `VaultWebMaintainer.new_file_link` + shared lifecycle

**Files:**
- Modify: `src/ari/infrastructure/vault_web/maintainer.py`
- Test: `tests/infrastructure/vault_web/test_maintainer_file_link.py`

**Interfaces:**
- Consumes: `FileLinkStore` (Task 1), `is_denied` (Task 2), the updated `VaultWebServer` (Task 4).
- Produces: `VaultWebMaintainer(..., denied_roots: list[str] = ())` now owns a `FileLinkStore`; `new_file_link(path: str) -> str` returns a `https://<ip>:<port>/f/<token>` URL (raises `FileNotFoundError` / `PermissionError`); `sweep_and_maybe_stop` stops only when both stores are empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/vault_web/test_maintainer_file_link.py
import pytest

from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer


def _maintainer(tmp_path, denied):
    return VaultWebMaintainer(
        vault=type("V", (), {"names": lambda self: []})(),
        servers_json=str(tmp_path / "servers.json"),
        cert_dir=str(tmp_path), port=0, bind="127.0.0.1",
        ttl_minutes=10, clock=lambda: 0.0, denied_roots=denied)


def test_new_file_link_returns_f_url_and_keeps_server_up(tmp_path):
    f = tmp_path / "a.pdf"; f.write_text("x")
    m = _maintainer(tmp_path, denied=[])
    try:
        url = m.new_file_link(str(f))
        assert "/f/" in url and url.startswith("https://")
        m.sweep_and_maybe_stop()  # a file token is active -> server stays up
        # (no assertion on internals; just that it doesn't raise)
    finally:
        m.stop()


def test_new_file_link_rejects_denied_and_missing(tmp_path):
    env = tmp_path / ".env"; env.write_text("S=1")
    m = _maintainer(tmp_path, denied=[])
    try:
        with pytest.raises(PermissionError):
            m.new_file_link(str(env))  # .env denied by basename
        with pytest.raises(FileNotFoundError):
            m.new_file_link(str(tmp_path / "nope.pdf"))
    finally:
        m.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/vault_web/test_maintainer_file_link.py -v`
Expected: FAIL — no `denied_roots` kwarg / no `new_file_link`.

- [ ] **Step 3: Write the implementation**

In `src/ari/infrastructure/vault_web/maintainer.py`: `import os`; import `FileLinkStore` and `is_denied`. Add `denied_roots` to `__init__`, build a `FileLinkStore`, pass both to the server, add `new_file_link`, and widen `sweep_and_maybe_stop`:

```python
from ari.infrastructure.vault_web.file_link_store import FileLinkStore
from ari.infrastructure.vault_web.fs_denylist import is_denied
```

In `__init__` (after `self._store = VaultLinkStore(...)`):
```python
        self._file_store = FileLinkStore(ttl_minutes * 60, clock)
        self._denied_roots = list(denied_roots)
```
(add `denied_roots: list[str] = ()` to the signature.)

In `new_link`, where it constructs `VaultWebServer(...)`, add the two kwargs:
```python
                self._server = VaultWebServer(self._bind, self._port, cert, key,
                                              self._vault, self._store, names,
                                              file_store=self._file_store,
                                              denied_roots=self._denied_roots)
```

Add:
```python
    def new_file_link(self, path: str) -> str:
        real = os.path.realpath(os.path.expanduser(path))
        if not os.path.isfile(real):
            raise FileNotFoundError(path)
        if is_denied(real, self._denied_roots):
            raise PermissionError(f"denied path: {path}")
        with self._lock:
            if self._server is None:
                lan_ip = detect_lan_ip()
                self._lan_ip = lan_ip
                cert, key = ensure_cert(self._cert_dir, lan_ip)
                self._server = VaultWebServer(self._bind, self._port, cert, key,
                                              self._vault, self._store, self._writable_names(),
                                              file_store=self._file_store,
                                              denied_roots=self._denied_roots)
                self._server.start()
            token = self._file_store.create(real)
            return f"https://{self._lan_ip}:{self._server.port}/f/{token}"
```

Widen the reaper:
```python
    def sweep_and_maybe_stop(self) -> None:
        with self._lock:
            if (self._server is not None
                    and self._store.active_count() == 0
                    and self._file_store.active_count() == 0):
                self._server.stop()
                self._server = None
                self._lan_ip = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/vault_web/ -v`
Expected: PASS (new maintainer tests + existing maintainer/server tests green).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/maintainer.py tests/infrastructure/vault_web/test_maintainer_file_link.py
git commit -m "feat(vault_web): mint one-time LAN file links via the maintainer"
```

---

### Task 6: `FileServeRequestRunner`

**Files:**
- Create: `src/ari/application/files/__init__.py` (empty), `src/ari/application/files/request_runner.py`
- Test: `tests/application/test_file_serve_request_runner.py`

**Interfaces:**
- Consumes: `SqliteFileRequests` (Task 3), `VaultWebMaintainer.new_file_link` (Task 5).
- Produces: `FileServeRequestRunner(requests, vault_web, send, clock, max_age=timedelta(hours=1))` with `async __call__()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_file_serve_request_runner.py
from datetime import datetime, timedelta, timezone

from ari.application.files.request_runner import FileServeRequestRunner
from ari.domain.files.requests import DONE, FAILED, SKIPPED
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class _Web:
    def __init__(self, link=None, exc=None): self._link, self._exc = link, exc
    def new_file_link(self, path):
        if self._exc:
            raise self._exc
        return self._link


async def _run(q, web, sent, clock=lambda: NOW):
    async def send(chat_id, text): sent.append((chat_id, text))
    await FileServeRequestRunner(q, web, send, clock)()


async def test_pending_request_gets_link():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn); sent = []
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await _run(q, _Web(link="https://1.2.3.4:8765/f/tok"), sent)
        assert sent and "https://1.2.3.4:8765/f/tok" in sent[0][1]
        assert await q.status_of(rid) == DONE
    finally:
        await conn.close()


async def test_mint_failure_reported():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn); sent = []
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await _run(q, _Web(exc=PermissionError("denied path: /tmp/a.pdf")), sent)
        assert await q.status_of(rid) == FAILED
        assert sent and "No pude" in sent[0][1]
    finally:
        await conn.close()


async def test_stale_request_skipped_without_link():
    conn = await connect(":memory:", embedding_dim=4)
    q = SqliteFileRequests(conn); sent = []
    try:
        rid = await q.add("42", "42", "/tmp/a.pdf")
        await _run(q, _Web(link="x"), sent, clock=lambda: NOW + timedelta(hours=2))
        assert await q.status_of(rid) == SKIPPED and sent == []
    finally:
        await conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_file_serve_request_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
# src/ari/application/files/request_runner.py
import logging
import os
from datetime import timedelta

from ari.domain.files.requests import DONE, FAILED, SKIPPED

log = logging.getLogger("ari.files")


class FileServeRequestRunner:
    """Claims queued file requests and answers each with a one-time LAN file link."""

    def __init__(self, requests, vault_web, send, clock, max_age: timedelta = timedelta(hours=1)):
        self._requests, self._vault_web = requests, vault_web
        self._send, self._clock, self._max_age = send, clock, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                continue
            try:
                link = self._vault_web.new_file_link(req.path)
            except Exception as exc:  # noqa: BLE001 — denied/missing/mint failure
                log.warning("file link mint failed: %s", exc)
                await self._requests.finish(req.id, FAILED, str(exc))
                await self._send(req.chat_id, "No pude generar el link para abrir ese archivo.")
                continue
            name = os.path.basename(req.path)
            await self._send(
                req.chat_id,
                f"Abrí «{name}» en la misma red (vence pronto, un solo uso):\n{link}\n"
                "El navegador va a advertir por el certificado; aceptá una vez.")
            await self._requests.finish(req.id, DONE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/application/test_file_serve_request_runner.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/files/ tests/application/test_file_serve_request_runner.py
git commit -m "feat(files): runner mints and delivers one-time LAN file links"
```

---

### Task 7: `AriTools.abrir_archivo` + permissions + wrapper + capability

**Files:**
- Modify: `src/ari/application/ari_tools.py` (constructor kwargs `files`, `denied_roots`; `abrir_archivo` method)
- Modify: `src/ari/domain/tools/ari_permissions.py` (add `abrir_archivo` to `ARI_TOOLS`, owner-only — NOT in `_USER_CHAT`)
- Modify: `src/ari/mcp_server/server.py` (`@tool("abrir_archivo")` wrapper)
- Modify: `src/ari/domain/agent/capabilities.py` (owner-only Capability)
- Modify: `tests/domain/test_ari_permissions.py` (catalogue pin: add `abrir_archivo` to the `test_catalogue` tuple; it is owner-only so NOT in `USER_CHAT`)
- Test: `tests/application/test_ari_tools_abrir_archivo.py`, and extend `tests/infrastructure/test_mcp_server.py`

**Interfaces:**
- Consumes: `SqliteFileRequests` (Task 3), `is_denied` (Task 2), `allowed_ari_tools`.
- Produces: `AriTools(..., files=None, denied_roots=())`; `async abrir_archivo(ruta) -> str`; `abrir_archivo` registered on the ari MCP server owner-only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_ari_tools_abrir_archivo.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env(tmp_path):
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, SqliteFileRequests(conn), SqliteTurnLog(conn), tmp_path
    await conn.close()


def _tools(env, owner=True, denied=None):
    conn, files, log, _tmp = env
    actor = Actor("42", "42", "Gabriel", owner, CHAT, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, files=files, denied_roots=denied or [])


async def test_owner_enqueues_and_receipts(env):
    _conn, files, _log, tmp = env
    f = tmp / "factura.pdf"; f.write_text("x")
    out = await _tools(env).abrir_archivo(str(f))
    assert out.startswith("🔗")
    [req] = await files.claim_pending()
    assert req.path.endswith("factura.pdf")


async def test_non_owner_denied(env):
    _conn, files, _log, tmp = env
    f = tmp / "a.pdf"; f.write_text("x")
    assert await _tools(env, owner=False).abrir_archivo(str(f)) == DENIED
    assert await files.claim_pending() == []


async def test_denied_path_not_enqueued(env):
    _conn, files, _log, tmp = env
    secret = tmp / ".env"; secret.write_text("S=1")
    out = await _tools(env, denied=[]).abrir_archivo(str(secret))
    assert "protegida" in out.lower() or "no comparto" in out.lower()
    assert await files.claim_pending() == []


async def test_missing_file(env):
    out = await _tools(env).abrir_archivo("/no/such/file.pdf")
    assert out.startswith("No existe")
```

Add to `tests/infrastructure/test_mcp_server.py`:

```python
async def test_abrir_archivo_registered_only_for_owner_chat():
    async def get_tools():
        raise AssertionError("not called")
    from ari.domain.tools.ari_permissions import allowed_ari_tools
    owner = {t.name for t in await build_server(get_tools, allowed_ari_tools(True, "chat")).list_tools()}
    user = {t.name for t in await build_server(get_tools, allowed_ari_tools(False, "chat")).list_tools()}
    assert "abrir_archivo" in owner and "abrir_archivo" not in user
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/application/test_ari_tools_abrir_archivo.py -v`
Expected: FAIL — `AriTools` has no `files` kwarg / no `abrir_archivo`.

- [ ] **Step 3: Write the implementation**

In `src/ari/application/ari_tools.py`: ensure `import os` at top; import `is_denied`:
```python
from ari.infrastructure.vault_web.fs_denylist import is_denied
```
Append constructor kwargs and storage (end of `__init__`, after `self._runner = runner`):
```python
                 files=None, denied_roots=()):
        ...
        self._files = files            # SqliteFileRequests | None
        self._denied_roots = list(denied_roots)
```
Add the method (near `pedir_credenciales`):
```python
    async def abrir_archivo(self, ruta: str) -> str:
        if not self._allowed("abrir_archivo"):
            return DENIED
        if self._files is None:
            return "No pude prepararlo: la cola de archivos no está disponible."
        ruta = (ruta or "").strip()
        if not ruta:
            return "No me pasaste ninguna ruta."
        real = os.path.realpath(os.path.expanduser(ruta))
        if not os.path.isfile(real):
            return f"No existe el archivo: {ruta}"
        if is_denied(real, self._denied_roots):
            return "No comparto ese archivo: está en la lista de rutas protegidas (secretos)."
        await self._files.add(self._a.user_id, self._a.chat_id, real)
        return await self._receipt(f"🔗 Te preparo el link para abrir «{os.path.basename(real)}»")
```

In `src/ari/domain/tools/ari_permissions.py`: append `"abrir_archivo"` to `ARI_TOOLS` (do NOT add it to `_USER_CHAT` — owner-only).

In `src/ari/mcp_server/server.py` (before `return server`):
```python
    @tool("abrir_archivo")
    async def abrir_archivo(ruta: str) -> str:
        """(Solo dueño) Prepara un link seguro en la red local (un solo uso, vence
        pronto) para abrir un archivo de la Mac en el navegador. ruta: la ruta del
        archivo (p. ej. "/Users/.../Desktop/factura.pdf"). No comparte secretos
        (vault, .env, ~/.ssh, llaves)."""
        return await (await get_tools()).abrir_archivo(ruta)
```

In `src/ari/domain/agent/capabilities.py`, add an owner-only Capability (near the other owner command capabilities):
```python
    Capability(
        owner_only=True,
        summary="Abrir un archivo de la Mac en el navegador con abrir_archivo: prepara un "
                "link seguro en la red local (un solo uso, vence pronto, HTTPS). Sirve "
                "cualquier archivo MENOS secretos (vault, .env, ~/.ssh, llaves). Úsalo "
                "cuando tu creador quiera ver/abrir un archivo que está en su Mac."),
```

In `tests/domain/test_ari_permissions.py`, add `"abrir_archivo"` to the expected `test_catalogue` tuple (same position as in `ARI_TOOLS`). Do NOT add it to the `USER_CHAT` fixture (owner-only).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_ari_tools_abrir_archivo.py tests/domain/test_ari_permissions.py tests/infrastructure/test_mcp_server.py tests/domain/test_capabilities.py -v`
Expected: PASS — including the catalogue parity + stdio handshake in `test_mcp_server.py` (the wrapper is registered, so `ARI_TOOLS` parity holds).

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py src/ari/domain/tools/ari_permissions.py src/ari/mcp_server/server.py src/ari/domain/agent/capabilities.py tests/application/test_ari_tools_abrir_archivo.py tests/domain/test_ari_permissions.py tests/infrastructure/test_mcp_server.py
git commit -m "feat(files): owner-only abrir_archivo tool mints a LAN file link"
```

---

### Task 8: Wiring — settings env, main.py, MCP `__main__`

**Files:**
- Modify: `src/ari/main.py` (`build()` — construct `SqliteFileRequests`, `default_denied_roots`, pass `denied_roots` to the maintainer, add `file_requests` to `Components`; `ari_spec` env gains `ARI_VAULT_PATH` + `ARI_CLAUDE_CONFIG_DIR`; construct + poll `FileServeRequestRunner`; `reset_taken()` on startup)
- Modify: `src/ari/mcp_server/__main__.py` (`_get_tools` builds `SqliteFileRequests` + `default_denied_roots` from env and passes `files=`/`denied_roots=` to `AriTools`)
- Test: `tests/infrastructure/test_mcp_server_abrir_env.py` (env-driven wiring smoke) + full suite

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: end-to-end wired `abrir_archivo`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_mcp_server_abrir_env.py
# The MCP __main__ must build AriTools with a file queue + denylist from env,
# so abrir_archivo enqueues instead of returning the "cola no disponible" message.
import os

from ari.infrastructure.vault_web.fs_denylist import default_denied_roots, is_denied


def test_default_denied_roots_from_env_values(tmp_path):
    vault = str(tmp_path / "vault.enc")
    cfg = str(tmp_path / ".ari-claude")
    roots = default_denied_roots(vault, cfg)
    assert os.path.realpath(vault) in roots
    # a .env anywhere is denied regardless of roots
    env = tmp_path / ".env"; env.write_text("x")
    assert is_denied(str(env), roots) is True
```

(This pins the denylist builder used by the wiring. The heavier end-to-end wiring is validated by the full suite — notably the `test_mcp_server.py` stdio handshake, which spawns the real MCP subprocess and lists `abrir_archivo`.)

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `uv run pytest tests/infrastructure/test_mcp_server_abrir_env.py -v`
Expected: PASS already if Task 2 is in (this is a guard for the wiring). If it errors on import, Task 2 is missing.

- [ ] **Step 3: Write the wiring**

In `src/ari/main.py` `build()`:
- After `email_enroll = ...`, construct the queue:
  ```python
  from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests
  file_requests = SqliteFileRequests(conn)
  ```
- Build the denylist roots (near `workspaces_abs`):
  ```python
  from ari.infrastructure.vault_web.fs_denylist import default_denied_roots
  denied_roots = default_denied_roots(settings.vault_path, settings.claude_config_dir)
  ```
- Pass `denied_roots` to the maintainer:
  ```python
  vault_web = VaultWebMaintainer(
      vault, settings.mcp_config, cert_dir=cert_dir, port=settings.vault_web_port,
      bind=settings.vault_web_bind, ttl_minutes=settings.vault_web_ttl_minutes,
      extra_names=skills.required_secret_names, denied_roots=denied_roots)
  ```
- Add two env vars to the `ari_spec` base env dict (so the subprocess can build the same denylist + reach the queue — the queue uses `ARI_DB_PATH`, already present):
  ```python
      "ARI_VAULT_PATH": os.path.expanduser(settings.vault_path),
      "ARI_CLAUDE_CONFIG_DIR": os.path.abspath(settings.claude_config_dir),
  ```
- Add `file_requests` to the `Components` dataclass and its constructor call (add a `file_requests` field to `Components`).

In `src/ari/main.py` `main()`/post-init, mirror the credential runner wiring (find `CredentialRequestRunner` and replicate its three touch points for files): construct `FileServeRequestRunner(c.file_requests, c.vault_web, send, _utcnow)`, call it on the same periodic sweep/poll the credential runner uses, and call `await c.file_requests.reset_taken()` where credential `reset_taken()` is called on startup.

In `src/ari/mcp_server/__main__.py` `_get_tools`, add imports and build the queue + denylist from env, then pass to `AriTools(...)`:
```python
        from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests
        from ari.infrastructure.vault_web.fs_denylist import default_denied_roots
```
```python
        denied_roots = default_denied_roots(
            env.get("ARI_VAULT_PATH", "~/.ari/vault.enc"),
            env.get("ARI_CLAUDE_CONFIG_DIR", "./.ari-claude"))
```
and in the `AriTools(...)` call add:
```python
            files=SqliteFileRequests(conn),
            denied_roots=denied_roots)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS — entirely green (notably `tests/infrastructure/test_mcp_server.py::test_stdio_handshake_lists_tools_without_actor_env`, which spawns the real MCP subprocess and now lists `abrir_archivo`). Fix anything that broke.

- [ ] **Step 5: Commit**

```bash
git add src/ari/main.py src/ari/mcp_server/__main__.py tests/infrastructure/test_mcp_server_abrir_env.py
git commit -m "feat(files): wire abrir_archivo queue, denylist, and runner end to end"
```

---

## Self-Review

**1. Spec coverage**
- Owner-only tool + enqueue (request-queue pattern) → Task 7 + Task 3. ✅
- `FileLinkStore` single-use → Task 1. ✅
- Denylist (`is_denied`, 3 points) → Task 2 (predicate) used in Task 7 (tool), Task 5 (mint), Task 4 (serve). ✅
- `/f/<token>` streaming + serve-time recheck → Task 4. ✅
- `new_file_link` + shared lifecycle (stop when both stores empty) → Task 5. ✅
- `FileServeRequestRunner` + delivery → Task 6. ✅
- Capability + wrapper + permission gating → Task 7. ✅
- Wiring (settings env, main, __main__) → Task 8. ✅
- TLS / LAN reuse → inherited from `vault_web` (Tasks 4-5). ✅

**2. Placeholder scan** — no TBD/TODO; every code step has real code. The one awkward fixture scaffold in Task 4 Step 1 is explicitly flagged to be replaced by the two concrete `test_file_route_*` functions. ✅

**3. Type consistency** — `FileLinkStore.create/claim/active_count`; `is_denied(path, denied_roots)` / `default_denied_roots(vault_path, claude_config_dir, cert_key_path=None)`; `SqliteFileRequests.add/claim_pending/reset_taken/finish/status_of`; `FileRequest(id,user_id,chat_id,path,created_at)`; `new_file_link(path)->str`; `FileServeRequestRunner(requests, vault_web, send, clock, max_age)`; `AriTools(..., files, denied_roots)` + `abrir_archivo`. Consistent across tasks. ✅

**4. Review Focus coverage** — symlink→denied (Task 2 `test_symlink_to_denied_target_is_denied`); token reuse→403 (Task 1 + Task 4 `test_file_route_streams_once_then_403`); denied-at-serve (Task 4 `test_file_route_denied_path_refused`); non-owner (Task 7 `test_non_owner_denied`); missing/denied path from the tool (Task 7 `test_missing_file`/`test_denied_path_not_enqueued`). ✅

**Note:** `abrir_archivo` added to `ARI_TOOLS` + `_USER_CHAT`-exclusion AND the `@tool` wrapper AND the catalogue pins all land in **Task 7** together, so the suite has no cross-task red window (unlike a split would cause).
