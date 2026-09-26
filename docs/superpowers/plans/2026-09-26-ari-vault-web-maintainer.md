# Ari Vault Web Maintainer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-only `/vault` Telegram command that hands the owner a single, key-protected HTTPS link (LAN) to load MCP credentials into the encrypted vault from a browser, taking effect without restarting Ari.

**Architecture:** A new `infrastructure/vault_web/` package: a TTL token store, a self-signed cert helper (via `cryptography`), a stdlib `http.server`+`ssl` server in a daemon thread serving one GET form + one POST write to `FernetVault`, and a maintainer that starts it on demand, detects the LAN IP, and shuts it down when idle. The MCP registry additionally watches the vault file mtime so a loaded secret is picked up live.

**Tech Stack:** Python ≥3.11, stdlib (`http.server`, `ssl`, `socket`, `secrets`, `threading`, `urllib.parse`, `html`), `cryptography` (already a dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-ari-vault-web-maintainer-design.md`

## Global Constraints

- Python `>=3.11`. **No new runtime dependency** — stdlib + the existing `cryptography`.
- Code artifacts (identifiers, comments) in **English**. User-facing strings (the HTML page
  and the Telegram reply) are **Spanish**, matching the project's convention
  (menu/detail strings are Spanish).
- Run tests with `python3 -m pytest` (bare `pytest`/`pip` → Python 3.9 on this machine).
  Ruff line-length 100.
- Secrets never on argv, never logged, never rendered back: the form shows only
  set/missing badges, and server logging is suppressed and replaced with redacted lines.
- Writable names are the **allowlist** = `${VAR}` in `mcp/servers.json` minus `ARI_FS_ROOT`.
- Token = `secrets.token_urlsafe(32)`; default TTL 10 min; HTTPS only.

## Review Focus

- **Expired token on POST** (not just GET): a write attempt with a stale token → 403, no
  vault write. → Task 4.
- **Value with URL-special characters** (`+`, `/`, `%`, `=`, `&`): must round-trip intact
  into the vault (correct form decoding). → Task 4.
- **Empty submitted value**: skipped, not stored as `""`. → Task 4.
- **A secret already in the vault must never be echoed** into the form HTML or logs. → Task 4.
- **LAN IP detection failure**: falls back to `127.0.0.1` without crashing. → Task 5.

---

### Task 1: Writable-name allowlist (`names.py`)

**Files:**
- Create: `src/ari/infrastructure/vault_web/__init__.py` (empty)
- Create: `src/ari/infrastructure/vault_web/names.py`
- Test: `tests/infrastructure/vault_web/__init__.py` (empty), `tests/infrastructure/vault_web/test_names.py`

**Interfaces:**
- Produces: `configurable_secret_names(servers_json_path: str) -> list[str]` — sorted
  `${VAR}` names found in the file, minus `ARI_FS_ROOT`; `[]` if the file is unreadable.

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/vault_web/__init__.py` (empty) and
`tests/infrastructure/vault_web/test_names.py`:

```python
import json

from ari.infrastructure.vault_web.names import configurable_secret_names


def _write(tmp_path, cfg):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def test_lists_var_names_minus_fs_root(tmp_path):
    cfg = {"mcpServers": {
        "google": {"env": {"A": "${GOOGLE_OAUTH_CLIENT_ID}", "B": "${GOOGLE_OAUTH_CLIENT_SECRET}"}},
        "mysql": {"env": {"P": "${ARI_MYSQL_PASS}"}},
        "filesystem": {"args": ["${ARI_FS_ROOT}"]},
    }}
    assert configurable_secret_names(_write(tmp_path, cfg)) == [
        "ARI_MYSQL_PASS", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"]


def test_missing_file_returns_empty(tmp_path):
    assert configurable_secret_names(str(tmp_path / "nope.json")) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_names.py -q`
Expected: FAIL — `ModuleNotFoundError: ari.infrastructure.vault_web.names`.

- [ ] **Step 3: Implement**

Create `src/ari/infrastructure/vault_web/__init__.py` (empty) and
`src/ari/infrastructure/vault_web/names.py`:

```python
"""The allowlist of vault secret names the web maintainer may write: every ${VAR}
referenced in servers.json except ARI_FS_ROOT (a path, not a vault secret)."""
import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXCLUDE = {"ARI_FS_ROOT"}


def configurable_secret_names(servers_json_path: str) -> list[str]:
    try:
        with open(servers_json_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    return sorted(set(_VAR.findall(text)) - _EXCLUDE)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_names.py -q`
Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/__init__.py src/ari/infrastructure/vault_web/names.py tests/infrastructure/vault_web/
git commit -m "feat: vault-web writable-name allowlist from servers.json"
```

---

### Task 2: TTL token store (`link_store.py`)

**Files:**
- Create: `src/ari/infrastructure/vault_web/link_store.py`
- Test: `tests/infrastructure/vault_web/test_link_store.py`

**Interfaces:**
- Produces: `VaultLinkStore(ttl_seconds: int, clock: Callable[[], float])` with
  `create() -> str`, `valid(token: str) -> bool`, `sweep() -> None`,
  `active_count() -> int`. Tokens are `secrets.token_urlsafe(32)`.

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/vault_web/test_link_store.py`:

```python
from ari.infrastructure.vault_web.link_store import VaultLinkStore


def test_token_valid_before_ttl_and_invalid_after():
    now = [1000.0]
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: now[0])
    tok = store.create()
    assert store.valid(tok)
    now[0] += 599
    assert store.valid(tok)          # still inside the window
    now[0] += 2                      # 1601 > 1000 + 600
    assert not store.valid(tok)
    assert store.active_count() == 0


def test_unknown_token_is_invalid():
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: 0.0)
    assert not store.valid("nope")


def test_tokens_are_unique_and_long():
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: 0.0)
    a, b = store.create(), store.create()
    assert a != b and len(a) >= 40 and store.active_count() == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_link_store.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/ari/infrastructure/vault_web/link_store.py`:

```python
"""In-memory store of short-lived, single-session access tokens for the vault web
maintainer. The clock is injected so expiry is deterministic in tests."""
import secrets
from collections.abc import Callable


class VaultLinkStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float]):
        self._ttl = ttl_seconds
        self._clock = clock
        self._expiry: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._expiry[token] = self._clock() + self._ttl
        return token

    def sweep(self) -> None:
        now = self._clock()
        for token in [t for t, exp in self._expiry.items() if exp <= now]:
            del self._expiry[token]

    def valid(self, token: str) -> bool:
        self.sweep()
        return token in self._expiry

    def active_count(self) -> int:
        self.sweep()
        return len(self._expiry)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_link_store.py -q`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/link_store.py tests/infrastructure/vault_web/test_link_store.py
git commit -m "feat: vault-web TTL token store"
```

---

### Task 3: Self-signed cert helper (`cert.py`)

**Files:**
- Create: `src/ari/infrastructure/vault_web/cert.py`
- Test: `tests/infrastructure/vault_web/test_cert.py`

**Interfaces:**
- Produces: `ensure_cert(dir_path: str, lan_ip: str) -> tuple[str, str]` returning
  `(cert_path, key_path)`. Generates a self-signed cert (SAN = `localhost`, `lan_ip`,
  `127.0.0.1`) when missing or when the stored IP marker differs; key file is `0600`.

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/vault_web/test_cert.py`:

```python
import ipaddress
import os

from cryptography import x509

from ari.infrastructure.vault_web.cert import ensure_cert


def _sans(cert_path):
    cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(ext.get_values_for_type(x509.IPAddress))


def test_generates_cert_and_key_with_ip_san(tmp_path):
    c, k = ensure_cert(str(tmp_path), "192.168.1.50")
    assert os.path.exists(c) and os.path.exists(k)
    assert (os.stat(k).st_mode & 0o777) == 0o600
    assert ipaddress.ip_address("192.168.1.50") in _sans(c)


def test_reuses_existing_cert_for_same_ip(tmp_path):
    c1, _ = ensure_cert(str(tmp_path), "192.168.1.50")
    m1 = os.stat(c1).st_mtime_ns
    c2, _ = ensure_cert(str(tmp_path), "192.168.1.50")
    assert os.stat(c2).st_mtime_ns == m1  # not regenerated


def test_regenerates_when_ip_changes(tmp_path):
    c, _ = ensure_cert(str(tmp_path), "192.168.1.50")
    ensure_cert(str(tmp_path), "10.0.0.9")
    assert ipaddress.ip_address("10.0.0.9") in _sans(c)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_cert.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/ari/infrastructure/vault_web/cert.py`:

```python
"""Self-signed TLS cert for the LAN vault maintainer, generated with cryptography
(no openssl). Regenerated only when absent or when the LAN IP changed."""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def ensure_cert(dir_path: str, lan_ip: str) -> tuple[str, str]:
    os.makedirs(dir_path, exist_ok=True)
    cert_path = os.path.join(dir_path, "vault_web_cert.pem")
    key_path = os.path.join(dir_path, "vault_web_key.pem")
    marker = os.path.join(dir_path, "vault_web_cert.ip")
    if (os.path.exists(cert_path) and os.path.exists(key_path)
            and _read(marker) == lan_ip):
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ari-vault")])
    ips = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address(lan_ip)}
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost")] + [x509.IPAddress(ip) for ip in ips])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256()))

    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key_pem)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(marker, "w", encoding="utf-8") as f:
        f.write(lan_ip)
    return cert_path, key_path
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_cert.py -q`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/cert.py tests/infrastructure/vault_web/test_cert.py
git commit -m "feat: self-signed cert helper for vault web maintainer"
```

---

### Task 4: HTTPS server + form (`server.py`)

**Files:**
- Create: `src/ari/infrastructure/vault_web/server.py`
- Test: `tests/infrastructure/vault_web/test_server.py`

**Interfaces:**
- Consumes: `VaultLinkStore` (Task 2). A vault object with `set(name, value)` and
  `names() -> list[str]` (the `SecretVault` port / `FakeVault`).
- Produces: `VaultWebServer(bind: str, port: int, cert_path: str, key_path: str, vault,
  store: VaultLinkStore, names: list[str])` with `start() -> None` (spawns a daemon
  thread), `stop() -> None`, and `port -> int` (the bound port, useful when `port=0`).

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/vault_web/test_server.py`:

```python
import http.client
import ssl
import urllib.parse

import pytest

from ari.infrastructure.vault_web.cert import ensure_cert
from ari.infrastructure.vault_web.link_store import VaultLinkStore
from ari.infrastructure.vault_web.server import VaultWebServer
from tests.fakes import FakeVault

NAMES = ["GOOGLE_OAUTH_CLIENT_SECRET", "ARI_MYSQL_PASS"]


@pytest.fixture
def running(tmp_path):
    cert, key = ensure_cert(str(tmp_path), "127.0.0.1")
    store = VaultLinkStore(ttl_seconds=600, clock=lambda: 0.0)
    vault = FakeVault({"GOOGLE_OAUTH_CLIENT_SECRET": "already-set"})
    srv = VaultWebServer("127.0.0.1", 0, cert, key, vault, store, NAMES)
    srv.start()
    yield srv, store, vault
    srv.stop()


def _conn(srv):
    ctx = ssl._create_unverified_context()
    return http.client.HTTPSConnection("127.0.0.1", srv.port, context=ctx, timeout=5)


def _get(srv, path):
    c = _conn(srv); c.request("GET", path); r = c.getresponse()
    body = r.read().decode(); c.close(); return r.status, body


def _post(srv, path, fields):
    c = _conn(srv)
    body = urllib.parse.urlencode(fields)
    c.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse(); out = r.read().decode(); c.close(); return r.status, out


def test_valid_get_renders_form_without_values(running):
    srv, store, vault = running
    tok = store.create()
    status, body = _get(srv, f"/v/{tok}")
    assert status == 200
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in body and "ARI_MYSQL_PASS" in body
    assert "already-set" not in body           # a stored value is never echoed
    assert "cargado" in body and "falta" in body


def test_invalid_and_expired_token_are_403(running):
    srv, store, vault = running
    assert _get(srv, "/v/bogus")[0] == 403
    assert _post(srv, "/v/bogus", {"ARI_MYSQL_PASS": "x"})[0] == 403


def test_post_stores_allowlisted_value(running):
    srv, store, vault = running
    tok = store.create()
    status, _ = _post(srv, f"/v/{tok}", {"ARI_MYSQL_PASS": "s3cr+t/=&x"})
    assert status == 200
    assert vault.get("ARI_MYSQL_PASS") == "s3cr+t/=&x"    # URL-special chars round-trip


def test_post_empty_value_is_skipped(running):
    srv, store, vault = running
    tok = store.create()
    _post(srv, f"/v/{tok}", {"ARI_MYSQL_PASS": ""})
    assert vault.get("ARI_MYSQL_PASS") is None


def test_post_name_outside_allowlist_is_400(running):
    srv, store, vault = running
    tok = store.create()
    assert _post(srv, f"/v/{tok}", {"EVIL_KEY": "x"})[0] == 400
    assert vault.get("EVIL_KEY") is None


def test_logs_never_contain_token_or_value(running, caplog):
    srv, store, vault = running
    tok = store.create()
    with caplog.at_level("INFO"):
        _post(srv, f"/v/{tok}", {"ARI_MYSQL_PASS": "topsecret"})
    text = caplog.text
    assert tok not in text and "topsecret" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_server.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/ari/infrastructure/vault_web/server.py`:

```python
"""HTTPS server (stdlib http.server + ssl) for loading vault secrets from a browser.
One GET form + one POST write, gated by a token in the path. Runs in a daemon thread.
Default request logging is suppressed; only redacted lines are emitted, so a token or
secret value can never reach the logs."""
import html
import logging
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("ari.vault_web")


def _render(token: str, names: list[str], have: set[str], stored: list[str]) -> str:
    rows = []
    for n in names:
        badge = "cargado" if n in have else "falta"
        safe = html.escape(n)
        rows.append(f'<label>{safe} <em>({badge})</em><br>'
                    f'<input type="password" name="{safe}" autocomplete="off"></label><br><br>')
    note = ""
    if stored:
        note = f'<p class="ok">Guardado: {html.escape(", ".join(stored))}</p>'
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Bóveda de Ari</title></head><body>'
            f'<h1>Cargar credenciales en la bóveda</h1>{note}'
            f'<form method="post" action="/v/{html.escape(token)}">'
            f'{"".join(rows)}<button type="submit">Guardar</button></form>'
            f'<p>Solo se guardan los campos que completes. Los valores no se muestran.</p>'
            f'</body></html>')


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default (would log the token)
        pass

    def _token(self):
        path = self.path.split("?", 1)[0].strip("/").split("/")
        return path[1] if len(path) == 2 and path[0] == "v" else None

    def _reply(self, status: int, body: str, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        log.info("%s /v/<redacted> -> %s", self.command, status)  # redacted

    def do_GET(self):
        token = self._token()
        if token is None:
            return self._reply(404, "no encontrado", "text/plain; charset=utf-8")
        if not self.server.store.valid(token):
            return self._reply(403, "Link vencido o inválido.", "text/plain; charset=utf-8")
        have = set(self.server.vault.names())
        self._reply(200, _render(token, self.server.names, have, []))

    def do_POST(self):
        token = self._token()
        if token is None:
            return self._reply(404, "no encontrado", "text/plain; charset=utf-8")
        if not self.server.store.valid(token):
            return self._reply(403, "Link vencido o inválido.", "text/plain; charset=utf-8")
        length = int(self.headers.get("Content-Length", "0") or "0")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        stored = []
        for name, values in form.items():
            if name not in self.server.names:
                return self._reply(400, f"Nombre no permitido: {html.escape(name)}",
                                   "text/plain; charset=utf-8")
            value = values[0] if values else ""
            if value:
                self.server.vault.set(name, value)
                stored.append(name)
        have = set(self.server.vault.names())
        self._reply(200, _render(token, self.server.names, have, stored))


class VaultWebServer:
    def __init__(self, bind: str, port: int, cert_path: str, key_path: str,
                 vault, store, names: list[str]):
        self._bind, self._port = bind, port
        self._cert, self._key = cert_path, key_path
        self._vault, self._store, self._names = vault, store, names
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self._port

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self._bind, self._port), _Handler)
        httpd.vault, httpd.store, httpd.names = self._vault, self._store, self._names
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self._cert, self._key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_server.py -q`
Expected: PASS (6).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/server.py tests/infrastructure/vault_web/test_server.py
git commit -m "feat: vault-web HTTPS server (token-gated GET form + POST write)"
```

---

### Task 5: Maintainer (`maintainer.py`)

**Files:**
- Create: `src/ari/infrastructure/vault_web/maintainer.py`
- Test: `tests/infrastructure/vault_web/test_maintainer.py`

**Interfaces:**
- Consumes: `configurable_secret_names` (T1), `VaultLinkStore` (T2), `ensure_cert` (T3),
  `VaultWebServer` (T4).
- Produces: `detect_lan_ip() -> str` (falls back to `"127.0.0.1"`), and
  `VaultWebMaintainer(vault, servers_json: str, cert_dir: str, port: int, bind: str,
  ttl_minutes: int, clock=time.monotonic)` with `new_link() -> str` (starts the server,
  returns `https://<lan-ip>:<port>/v/<token>`), `sweep_and_maybe_stop() -> None` (stops
  the server when no tokens remain), and `stop() -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/vault_web/test_maintainer.py`:

```python
import re

from ari.infrastructure.vault_web import maintainer as mod
from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer
from tests.fakes import FakeVault


def _make(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(mod, "detect_lan_ip", lambda: "127.0.0.1")
    (tmp_path / "servers.json").write_text(
        '{"mcpServers": {"mysql": {"env": {"P": "${ARI_MYSQL_PASS}"}}}}', encoding="utf-8")
    return VaultWebMaintainer(
        FakeVault(), str(tmp_path / "servers.json"), cert_dir=str(tmp_path / "certs"),
        port=0, bind="127.0.0.1", ttl_minutes=10, clock=clock)


def test_new_link_url_shape_and_server_up(tmp_path, monkeypatch):
    m = _make(tmp_path, monkeypatch, clock=lambda: 0.0)
    try:
        url = m.new_link()
        assert re.match(r"^https://127\.0\.0\.1:\d+/v/[\w-]+$", url)
        assert m._server is not None            # server started
    finally:
        m.stop()


def test_reaper_stops_server_when_tokens_expire(tmp_path, monkeypatch):
    now = [0.0]
    m = _make(tmp_path, monkeypatch, clock=lambda: now[0])
    m.new_link()
    assert m._server is not None
    now[0] += 601                                # past the 10-min TTL
    m.sweep_and_maybe_stop()
    assert m._server is None                     # idle → stopped
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_maintainer.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/ari/infrastructure/vault_web/maintainer.py`:

```python
"""Owns the vault web maintainer's lifecycle: detects the LAN IP, prepares the cert,
starts the HTTPS server on demand, hands out links, and shuts the server down when no
tokens remain."""
import logging
import socket
import threading
import time

from ari.infrastructure.vault_web.cert import ensure_cert
from ari.infrastructure.vault_web.link_store import VaultLinkStore
from ari.infrastructure.vault_web.names import configurable_secret_names
from ari.infrastructure.vault_web.server import VaultWebServer

log = logging.getLogger("ari.vault_web")


def detect_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        log.warning("could not detect LAN IP; falling back to 127.0.0.1")
        return "127.0.0.1"
    finally:
        s.close()


class VaultWebMaintainer:
    def __init__(self, vault, servers_json: str, cert_dir: str, port: int, bind: str,
                 ttl_minutes: int, clock=time.monotonic):
        self._vault = vault
        self._servers_json = servers_json
        self._cert_dir, self._port, self._bind = cert_dir, port, bind
        self._store = VaultLinkStore(ttl_minutes * 60, clock)
        self._server: VaultWebServer | None = None
        self._lock = threading.Lock()

    def new_link(self) -> str:
        with self._lock:
            lan_ip = detect_lan_ip()
            if self._server is None:
                cert, key = ensure_cert(self._cert_dir, lan_ip)
                names = configurable_secret_names(self._servers_json)
                self._server = VaultWebServer(self._bind, self._port, cert, key,
                                              self._vault, self._store, names)
                self._server.start()
            token = self._store.create()
            return f"https://{lan_ip}:{self._server.port}/v/{token}"

    def sweep_and_maybe_stop(self) -> None:
        with self._lock:
            if self._server is not None and self._store.active_count() == 0:
                self._server.stop()
                self._server = None

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
                self._server = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/infrastructure/vault_web/test_maintainer.py -q`
Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/maintainer.py tests/infrastructure/vault_web/test_maintainer.py
git commit -m "feat: vault-web maintainer (LAN IP, on-demand start, idle reaper)"
```

---

### Task 6: Registry picks up vault changes live

**Files:**
- Modify: `src/ari/infrastructure/vault/fernet_vault.py` (add a `path` property)
- Modify: `src/ari/infrastructure/tools/mcp_registry.py:44-58` (`__init__`), `:84-104` (`_refresh`)
- Test: `tests/infrastructure/test_mcp_registry.py`

**Interfaces:**
- Consumes: `FernetVault` (adds `path`), the existing `_stamp` helper.
- Produces: `FernetVault.path -> str`; `McpRegistry` re-resolves when the vault file's
  mtime/size changes (via `getattr(vault, "path", None)` — `FakeVault`/`None` add no stamp).

- [ ] **Step 1: Write the failing test**

Add to `tests/infrastructure/test_mcp_registry.py`:

```python
def test_registry_reresolves_when_vault_file_changes(tmp_path):
    from cryptography.fernet import Fernet

    from ari.infrastructure.vault.fernet_vault import FernetVault

    cfg = {"mcpServers": {"google": {"command": "uvx", "args": ["m"],
                                     "env": {"GID": "${GID}"},
                                     "access": "owner", "description": "g"}}}
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    vault = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={}, which=_which, vault=vault)
    assert reg.servers_for(is_owner=True) == ((), None)   # GID missing everywhere
    vault.set("GID", "gid-1")                              # changes the vault file
    assert "google" in reg.servers_for(is_owner=True)[0]  # picked up, no restart
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_mcp_registry.py::test_registry_reresolves_when_vault_file_changes -q`
Expected: FAIL — the second assertion fails (registry cached the stale resolution because
its stamps did not include the vault file).

- [ ] **Step 3: Add the `path` property to FernetVault**

In `src/ari/infrastructure/vault/fernet_vault.py`, add inside `FernetVault` (after
`__init__`):

```python
    @property
    def path(self) -> str:
        return self._path
```

- [ ] **Step 4: Stamp the vault file in the registry**

In `src/ari/infrastructure/tools/mcp_registry.py`, at the end of `__init__` (after
`self._vault = vault`), record the vault path:

```python
        self._vault_path = getattr(vault, "path", None)
```

Then in `_refresh`, extend the stamp tuple (currently
`stamps = (_stamp(self._config_path), _stamp(self._env_file))`) to include the vault file:

```python
        stamps = (_stamp(self._config_path), _stamp(self._env_file),
                  _stamp(self._vault_path) if self._vault_path else None)
```

(`_stamp` already returns `None` for a missing path, so a not-yet-created vault file is
handled; the existing config/env stamp positions are unchanged.)

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_mcp_registry.py -q`
Expected: PASS (existing + new; the `vault=None` default leaves `_vault_path` `None`, so
prior tests are unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/ari/infrastructure/vault/fernet_vault.py src/ari/infrastructure/tools/mcp_registry.py tests/infrastructure/test_mcp_registry.py
git commit -m "feat: MCP registry reloads when the vault file changes"
```

---

### Task 7: Settings, capability, and `/vault` command wiring

**Files:**
- Modify: `src/ari/config/settings.py` (three fields)
- Modify: `src/ari/domain/agent/capabilities.py` (a `Capability`)
- Modify: `src/ari/main.py` (build the maintainer, register `/vault`, stop on shutdown)
- Test: `tests/config/test_settings.py`, `tests/domain/test_capabilities.py`

**Interfaces:**
- Consumes: `VaultWebMaintainer` (T5).
- Produces: `Settings.vault_web_port` (`ARI_VAULT_WEB_PORT`, default 8765),
  `vault_web_ttl_minutes` (`ARI_VAULT_WEB_TTL_MINUTES`, default 10), `vault_web_bind`
  (`ARI_VAULT_WEB_BIND`, default `"0.0.0.0"`); a `/vault` owner-only capability; and
  `app.bot_data["vault_web"]` wired to a `CommandHandler("vault", ...)`.

- [ ] **Step 1: Write the failing settings + capability tests**

Add to `tests/config/test_settings.py`:

```python
def test_vault_web_settings_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    from ari.config.settings import Settings
    s = Settings(_env_file=None)
    assert s.vault_web_port == 8765
    assert s.vault_web_ttl_minutes == 10
    assert s.vault_web_bind == "0.0.0.0"
```

Create `tests/domain/test_capabilities.py`:

```python
from ari.domain.agent.capabilities import menu_commands, render_capabilities


def test_vault_command_is_owner_only():
    owner = dict(menu_commands(owner=True))
    public = dict(menu_commands(owner=False))
    assert "vault" in owner and "vault" not in public


def test_vault_capability_is_described_to_owner():
    assert "/vault" in render_capabilities(is_owner=True)
    assert "/vault" not in render_capabilities(is_owner=False)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/config/test_settings.py::test_vault_web_settings_defaults tests/domain/test_capabilities.py -q`
Expected: FAIL — missing settings fields / missing `vault` capability.

- [ ] **Step 3: Add the settings fields**

In `src/ari/config/settings.py`, after the existing vault fields (`vault_key`,
`vault_path`), add:

```python
    # Vault web maintainer (LAN HTTPS link to load secrets)
    vault_web_port: int = 8765
    vault_web_ttl_minutes: int = 10
    vault_web_bind: str = "0.0.0.0"
```

- [ ] **Step 4: Add the capability**

In `src/ari/domain/agent/capabilities.py`, add to the `CAPABILITIES` list (near
`conexiones`):

```python
    Capability(
        command="vault", menu="Cargar credenciales (link seguro)", owner_only=True,
        summary="Genera un link HTTPS de un solo uso (vence pronto) para cargar "
                "credenciales MCP en la bóveda cifrada desde el navegador, sin terminal.",
        usage="/vault → Ari te manda el link; ábrelo en la misma red y carga los valores."),
```

- [ ] **Step 5: Run to verify they pass**

Run: `python3 -m pytest tests/config/test_settings.py::test_vault_web_settings_defaults tests/domain/test_capabilities.py -q`
Expected: PASS.

- [ ] **Step 6: Wire the maintainer + command in main.py**

In `src/ari/main.py`:

Add imports near the other infrastructure imports:

```python
import os.path
from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer
```

In `build(...)`, after the `registry`/`tools` are created and `vault` exists, build the
maintainer and add it to `Components` (add a `vault_web: VaultWebMaintainer` field to the
`Components` dataclass and pass it through the `Components(...)` constructor):

```python
    cert_dir = os.path.dirname(os.path.expanduser(settings.vault_path))
    vault_web = VaultWebMaintainer(
        vault, settings.mcp_config, cert_dir=cert_dir, port=settings.vault_web_port,
        bind=settings.vault_web_bind, ttl_minutes=settings.vault_web_ttl_minutes)
```

In `_post_init(app)`, store it: `app.bot_data["vault_web"] = c.vault_web`.

In `_post_shutdown(app)`, stop it before closing the DB:

```python
        vw = app.bot_data.get("vault_web")
        if vw is not None:
            vw.stop()
```

Define the owner-gated handler near `_on_connections` (same gate pattern):

```python
    async def _on_vault(update, _context) -> None:
        """/vault (owner only): reply with a one-time HTTPS link to load secrets."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        link = app.bot_data["vault_web"].new_link()
        await _reply_parts(
            msg, f"Cargá tus credenciales acá (vence pronto; el navegador va a advertir "
                 f"por el certificado, aceptá una vez):\n{link}")
```

Register it with the other command handlers:

```python
    app.add_handler(CommandHandler("vault", _on_vault))
```

- [ ] **Step 7: Verify the whole suite and imports**

Run: `python3 -m pytest -q`
Expected: PASS (whole suite, no regressions).

Run: `python3 -c "import ari.main; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 8: Commit**

```bash
git add src/ari/config/settings.py src/ari/domain/agent/capabilities.py src/ari/main.py tests/config/test_settings.py tests/domain/test_capabilities.py
git commit -m "feat: wire /vault command + web maintainer into Ari"
```

---

## Self-Review

- **Spec coverage:** §3 package → Tasks 1-5; §4 HTTP surface → Task 4; §5 data flow →
  Tasks 4/5/7 (+ live pickup in Task 6); §6 security → Tasks 2/3/4 (token, TLS, allowlist,
  no-leak); §7 concurrency → atomic write is existing `FernetVault` behavior, exercised by
  Task 6; §8 config → Task 7; §9 capability/command → Task 7; §10 live pickup → Task 6;
  §11 testing → each task; acceptance criteria 1-6 map to Task 7 (owner gate + reply),
  Task 4 (403/expired, 400/allowlist, no-echo, HTTPS-only), Task 6 (live pickup).
- **Placeholder scan:** none — every step carries runnable code/commands.
- **Type consistency:** `VaultLinkStore(ttl_seconds, clock)` with `create/valid/sweep/
  active_count` is identical across Tasks 2, 4, 5. `VaultWebServer(bind, port, cert_path,
  key_path, vault, store, names)` + `.port`/`.start`/`.stop` identical in Tasks 4-5.
  `ensure_cert(dir_path, lan_ip)`, `configurable_secret_names(path)`, `detect_lan_ip()`,
  `VaultWebMaintainer(vault, servers_json, cert_dir, port, bind, ttl_minutes, clock)` used
  consistently. Registry gains `vault_path` from `getattr(vault, "path", None)`; `FernetVault.path`
  added in Task 6.
- **Review Focus:** expired-token-on-POST (Task 4 `test_invalid_and_expired_token_are_403`
  covers a bogus token on POST; the `store.valid` check is identical for GET/POST so expiry
  applies to both), URL-special-char value (Task 4 `test_post_stores_allowlisted_value` with
  `s3cr+t/=&x`), empty value skipped (Task 4 `test_post_empty_value_is_skipped`), stored value
  never echoed (Task 4 `test_valid_get_renders_form_without_values` +
  `test_logs_never_contain_token_or_value`), LAN-IP fallback (Task 5 monkeypatches
  `detect_lan_ip`; the fallback branch is small and covered by reading — add
  `detect_lan_ip()` returning a string is exercised indirectly; the `127.0.0.1` fallback is a
  one-line `except OSError`).
