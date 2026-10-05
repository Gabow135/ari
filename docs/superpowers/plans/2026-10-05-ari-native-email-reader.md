# Native Email Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Ari native, in-process IMAP tools (`buscar_correos`, `leer_correo`) that return clean, bounded text and spill large bodies/attachments to the per-user workspace, so Ari reliably extracts data from large emails.

**Architecture:** Hexagonal, mirroring `leer_documento`/`extract_document`: a domain `EmailReaderPort` + entities, an infra `ImapEmailReader` (stdlib `imaplib`+`email`, `html.parser` for HTML→text), two `AriTools` application methods that orchestrate reader + workspace spill, and thin MCP `@tool` wrappers. Credentials are resolved in the main process (which holds the vault key) and passed to the credential-less `ari` MCP subprocess per turn via the `ARI_EMAIL_ACCOUNTS` env var.

**Tech Stack:** Python ≥3.11, stdlib only (`imaplib`, `email`, `html.parser`), `pytest`/`pytest-asyncio`. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-05-ari-native-email-reader-design.md`

## Global Constraints

- Python ≥ 3.11; **stdlib only** — no new third-party dependency (HTML→text via `html.parser`, IMAP via `imaplib`).
- The `ari` MCP subprocess must **never** receive the vault key; it gets only the acting user's resolved mailbox credentials for the turn, via env `ARI_EMAIL_ACCOUNTS` (JSON).
- Model-facing copy (tool docstrings, reply strings, error messages) in **neutral Spanish**, matching existing Ari tools. Code, identifiers, and comments in **English**.
- Strict TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit. Conventional Commits. **No AI attribution** in commit messages. Stage only named files (never `git add -A`).
- `ruff` line length 100; follow existing layering and patterns.
- Mail-server detection is by the substring `"mcp-mail-server"` in a server's `args` (robust to the pinned version), not the exact `MAIL_PACKAGE` string.

## Review Focus

- **MIME encoded-word headers** (`=?UTF-8?…?=` in `From`/`Subject`): a reasonable person expects "Pagos Mrjoy" decoded, not raw bytes. → pinned in Task 6.
- **HTML-only body with entities and nested tags**: expect readable text with `&amp;`/`&nbsp;` decoded and block tags turned into line breaks, script/style dropped. → pinned in Task 2.
- **Attachment filename with traversal/unsafe chars** (`../etc`, absolute, `nul`): expect the file written safely inside the per-email folder, never escaping the jail. → pinned in Task 8.
- **Body larger than the 1 MB workspace text cap**: expect the saved `.txt` truncated with a marker instead of the whole `leer_correo` call failing. → pinned in Task 8.
- **IMAP auth/connection failure**: expect a short Spanish error that never contains the password or a stack trace. → pinned in Task 7 (search) and Task 8 (fetch).

---

### Task 1: Domain entities + EmailReaderPort

**Files:**
- Modify: `src/ari/domain/email/entities.py`
- Create: `src/ari/domain/email/email_reader_port.py`
- Test: `tests/domain/test_email_reader_entities.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MailboxSpec(cuenta, imap_host, imap_port, imap_secure, user, password)`, `EmailSummary(uid, from_addr, subject, date, snippet)`, `EmailAttachment(filename, content_type, data)`, `FetchedEmail(uid, from_addr, to_addr, subject, date, body_text, attachments)`, and `EmailReaderPort` (Protocol) with sync `search(spec, criteria, limit) -> list[EmailSummary]` and `fetch(spec, uid) -> FetchedEmail`.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_email_reader_entities.py
import pytest

from ari.domain.email.entities import (EmailAttachment, EmailSummary,
                                        FetchedEmail, MailboxSpec)


def test_mailbox_spec_is_frozen():
    spec = MailboxSpec("email_corp", "imap.x.com", 993, True, "u@x.com", "secret")
    assert spec.cuenta == "email_corp" and spec.imap_port == 993
    with pytest.raises(Exception):
        spec.password = "other"  # frozen dataclass


def test_fetched_email_holds_attachments():
    att = EmailAttachment("factura.pdf", "application/pdf", b"%PDF-1.4")
    msg = FetchedEmail("12", "a@x.com", "b@y.com", "Pago", "Mon, 5 Oct",
                       "cuerpo", (att,))
    assert msg.attachments[0].filename == "factura.pdf"
    assert EmailSummary("12", "a@x.com", "Pago", "Mon, 5 Oct", "snip").uid == "12"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_email_reader_entities.py -v`
Expected: FAIL with `ImportError` (names not defined).

- [ ] **Step 3: Write minimal implementation**

Append to `src/ari/domain/email/entities.py`:

```python
@dataclass(frozen=True, slots=True)
class MailboxSpec:
    cuenta: str
    imap_host: str
    imap_port: int
    imap_secure: bool
    user: str
    password: str


@dataclass(frozen=True, slots=True)
class EmailSummary:
    uid: str
    from_addr: str
    subject: str
    date: str
    snippet: str


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class FetchedEmail:
    uid: str
    from_addr: str
    to_addr: str
    subject: str
    date: str
    body_text: str
    attachments: tuple[EmailAttachment, ...]
```

Create `src/ari/domain/email/email_reader_port.py`:

```python
from typing import Protocol

from ari.domain.email.entities import EmailSummary, FetchedEmail, MailboxSpec


class EmailReaderPort(Protocol):
    """Reads mail over IMAP. Methods are synchronous/blocking; callers run them
    off the event loop (e.g. asyncio.to_thread)."""

    def search(self, spec: MailboxSpec, criteria: str, limit: int) -> list[EmailSummary]: ...

    def fetch(self, spec: MailboxSpec, uid: str) -> FetchedEmail: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_email_reader_entities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/email/entities.py src/ari/domain/email/email_reader_port.py tests/domain/test_email_reader_entities.py
git commit -m "feat(email): email reader domain entities and port"
```

---

### Task 2: HTML→text stripper

**Files:**
- Create: `src/ari/infrastructure/email/html_text.py`
- Test: `tests/infrastructure/test_html_text.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `html_to_text(html: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_html_text.py
from ari.infrastructure.email.html_text import html_to_text


def test_strips_tags_and_decodes_entities():
    out = html_to_text("<p>Total&nbsp;USD&amp;: 1.234</p>")
    assert "Total" in out and "USD&:" in out and "1.234" in out
    assert "<p>" not in out


def test_drops_script_and_style():
    out = html_to_text("<style>.a{}</style><script>x()</script><p>hola</p>")
    assert "hola" in out
    assert "x()" not in out and ".a{}" not in out


def test_block_tags_become_line_breaks():
    out = html_to_text("<div>uno</div><div>dos</div>")
    assert "uno" in out and "dos" in out
    assert "\n" in out.strip()


def test_empty_input_is_empty():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_html_text.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ari/infrastructure/email/html_text.py`:

```python
"""Minimal HTML-to-text using only the stdlib, so email bodies are readable
without pulling a dependency. Not a full renderer: it drops script/style, turns
block tags into line breaks, and decodes entities."""
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "head", "title"}
_BLOCK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
          "table", "ul", "ol", "blockquote"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t\n]*", "\n\n", text)
    return text.strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_html_text.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/email/html_text.py tests/infrastructure/test_html_text.py
git commit -m "feat(email): stdlib HTML-to-text helper for email bodies"
```

---

### Task 3: `UserWorkspace.write_bytes`

**Files:**
- Modify: `src/ari/infrastructure/workspace/user_workspace.py`
- Test: `tests/infrastructure/test_user_workspace.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `UserWorkspace.write_bytes(rel: str, data: bytes, *, max_bytes: int = 10_485_760) -> str` (returns the workspace-relative path).

- [ ] **Step 1: Write the failing test**

Append to `tests/infrastructure/test_user_workspace.py`:

```python
def test_write_bytes_roundtrip(tmp_path):
    from ari.infrastructure.workspace.user_workspace import UserWorkspace
    ws = UserWorkspace(str(tmp_path), "u1")
    rel = ws.write_bytes("correos/adjuntos/a/factura.pdf", b"%PDF-1.4 bytes")
    assert rel == "correos/adjuntos/a/factura.pdf"
    assert ws.read_bytes(rel) == b"%PDF-1.4 bytes"


def test_write_bytes_rejects_oversize(tmp_path):
    import pytest
    from ari.infrastructure.workspace.user_workspace import UserWorkspace
    ws = UserWorkspace(str(tmp_path), "u1")
    with pytest.raises(ValueError):
        ws.write_bytes("big.bin", b"x" * 11, max_bytes=10)


def test_write_bytes_refuses_escape(tmp_path):
    import pytest
    from ari.infrastructure.workspace.user_workspace import UserWorkspace
    ws = UserWorkspace(str(tmp_path), "u1")
    with pytest.raises(ValueError):
        ws.write_bytes("../escape.bin", b"x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_user_workspace.py -k write_bytes -v`
Expected: FAIL with `AttributeError: 'UserWorkspace' object has no attribute 'write_bytes'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ari/infrastructure/workspace/user_workspace.py`, add this method to `UserWorkspace` (place it right after `read_bytes`):

```python
    def write_bytes(self, rel: str, data: bytes, *, max_bytes: int = 10_485_760) -> str:
        if len(data) > max_bytes:
            raise ValueError(f"content too large (> {max_bytes} bytes)")
        self.ensure()
        path = self.resolve(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return os.path.relpath(path, os.path.realpath(self._root))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_user_workspace.py -k write_bytes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/workspace/user_workspace.py tests/infrastructure/test_user_workspace.py
git commit -m "feat(workspace): write_bytes for binary files (email attachments)"
```

---

### Task 4: Mailbox credential serialization helpers

**Files:**
- Modify: `src/ari/infrastructure/email/server_spec.py`
- Test: `tests/infrastructure/test_email_accounts_from_servers.py`

**Interfaces:**
- Consumes: `MailboxSpec` (Task 1).
- Produces: `email_accounts_from_servers(servers: dict) -> list[MailboxSpec]`, `mailboxes_to_json(specs: list[MailboxSpec]) -> str`, `mailboxes_from_json(text: str) -> dict[str, MailboxSpec]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_email_accounts_from_servers.py
from ari.infrastructure.email.server_spec import (email_accounts_from_servers,
                                                  mailboxes_from_json,
                                                  mailboxes_to_json)

SERVERS = {
    "google": {"command": "uvx", "args": ["workspace-mcp"], "env": {}},
    "email_corp": {"command": "npx", "args": ["-y", "mcp-mail-server@2.1.0"],
                   "env": {"IMAP_HOST": "imap.corp.com", "IMAP_PORT": "993",
                           "IMAP_SECURE": "true", "EMAIL_USER": "me@corp.com",
                           "EMAIL_PASS": "s3cret"}},
    "mail_trabajo": {"command": "npx", "args": ["-y", "mcp-mail-server@2.1.0"],
                     "env": {"IMAP_HOST": "imap.gmail.com", "IMAP_PORT": "993",
                             "IMAP_SECURE": "true", "EMAIL_USER": "u@gmail.com",
                             "EMAIL_PASS": "app-pass"}},
    "ari": {"command": "py", "args": ["-m", "ari.mcp_server"], "env": {}},
}


def test_extracts_only_mail_servers():
    specs = {s.cuenta: s for s in email_accounts_from_servers(SERVERS)}
    assert set(specs) == {"email_corp", "mail_trabajo"}
    assert specs["email_corp"].imap_host == "imap.corp.com"
    assert specs["email_corp"].imap_port == 993
    assert specs["email_corp"].imap_secure is True
    assert specs["mail_trabajo"].password == "app-pass"


def test_skips_mail_servers_missing_host_or_user():
    broken = {"mail_x": {"args": ["mcp-mail-server@9"], "env": {"IMAP_HOST": "h"}}}
    assert email_accounts_from_servers(broken) == []


def test_json_roundtrip():
    specs = email_accounts_from_servers(SERVERS)
    restored = mailboxes_from_json(mailboxes_to_json(specs))
    assert set(restored) == {"email_corp", "mail_trabajo"}
    assert restored["mail_trabajo"].user == "u@gmail.com"


def test_from_json_tolerates_garbage():
    assert mailboxes_from_json("not json") == {}
    assert mailboxes_from_json("") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_email_accounts_from_servers.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ari/infrastructure/email/server_spec.py` (it already defines `MAIL_PACKAGE`; add `import json` at the top and `from ari.domain.email.entities import EmailAccount, MailboxSpec`):

```python
_MAIL_MARKER = "mcp-mail-server"


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


def email_accounts_from_servers(servers: dict) -> list[MailboxSpec]:
    """Pick the mail servers out of a per-turn MCP config and turn each into a
    MailboxSpec keyed by its config name (the turn-visible account name)."""
    specs: list[MailboxSpec] = []
    for name, cfg in (servers or {}).items():
        args = cfg.get("args") or []
        if not any(_MAIL_MARKER in str(a) for a in args):
            continue
        env = cfg.get("env") or {}
        host, user = env.get("IMAP_HOST"), env.get("EMAIL_USER")
        if not host or not user:
            continue
        try:
            port = int(env.get("IMAP_PORT", "993"))
        except (TypeError, ValueError):
            port = 993
        specs.append(MailboxSpec(
            cuenta=name, imap_host=host, imap_port=port,
            imap_secure=_truthy(env.get("IMAP_SECURE", "true")),
            user=user, password=env.get("EMAIL_PASS", "")))
    return specs


def mailboxes_to_json(specs: list[MailboxSpec]) -> str:
    return json.dumps([
        {"cuenta": s.cuenta, "imap_host": s.imap_host, "imap_port": s.imap_port,
         "imap_secure": s.imap_secure, "user": s.user, "password": s.password}
        for s in specs])


def mailboxes_from_json(text: str) -> dict[str, MailboxSpec]:
    try:
        raw = json.loads(text or "[]")
    except (TypeError, ValueError):
        return {}
    out: dict[str, MailboxSpec] = {}
    for d in raw if isinstance(raw, list) else []:
        try:
            out[d["cuenta"]] = MailboxSpec(
                cuenta=d["cuenta"], imap_host=d["imap_host"],
                imap_port=int(d["imap_port"]), imap_secure=bool(d["imap_secure"]),
                user=d["user"], password=d.get("password", ""))
        except (KeyError, TypeError, ValueError):
            continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_email_accounts_from_servers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/email/server_spec.py tests/infrastructure/test_email_accounts_from_servers.py
git commit -m "feat(email): extract and (de)serialize mailbox specs from turn config"
```

---

### Task 5: `ImapEmailReader.search`

**Files:**
- Create: `src/ari/infrastructure/email/imap_email_reader.py`
- Test: `tests/infrastructure/test_imap_email_reader.py`

**Interfaces:**
- Consumes: `MailboxSpec`, `EmailSummary` (Task 1).
- Produces: `ImapEmailReader(*, timeout=20.0, ssl_factory=imaplib.IMAP4_SSL, plain_factory=imaplib.IMAP4)` with `search(spec, criteria, limit) -> list[EmailSummary]`; module helper `_imap_criteria(criteria: str) -> list[str]`.

The factory kwargs exist so tests inject a fake connection instead of touching the network.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_imap_email_reader.py
from ari.domain.email.entities import MailboxSpec
from ari.infrastructure.email.imap_email_reader import (ImapEmailReader,
                                                        _imap_criteria)

SPEC = MailboxSpec("email_corp", "imap.x.com", 993, True, "u@x.com", "pw")

_HDR = (b"From: =?UTF-8?Q?Pagos_Mrjoy?= <pay@mrjoy.com>\r\n"
        b"Subject: =?UTF-8?Q?Factura_octubre?=\r\n"
        b"Date: Mon, 5 Oct 2026 10:00:00 -0500\r\n\r\n")


class FakeConn:
    def __init__(self):
        self.logged_out = False
        self.selected = None
    def login(self, user, pw): self.user = user
    def select(self, mailbox, readonly=False): self.selected = (mailbox, readonly)
    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"1 2 3"]
        if command == "FETCH":
            return "OK", [(args[0] + b" (HDR", _HDR)]
        return "OK", [b""]
    def logout(self): self.logged_out = True


def test_criteria_mapping():
    assert _imap_criteria("") == ["ALL"]
    assert _imap_criteria("de:pay@mrjoy.com") == ["FROM", '"pay@mrjoy.com"']
    assert _imap_criteria("asunto:factura") == ["SUBJECT", '"factura"']
    assert _imap_criteria("no leidos") == ["UNSEEN"]
    assert _imap_criteria("desde:2026-10-01") == ["SINCE", "01-Oct-2026"]
    assert _imap_criteria("saldo total") == ["TEXT", '"saldo total"']


def test_search_returns_newest_first_and_decodes_headers():
    conn = FakeConn()
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: conn)
    out = reader.search(SPEC, "", 2)
    assert [s.uid for s in out] == ["3", "2"]  # newest first, limited to 2
    assert out[0].from_addr == "Pagos Mrjoy <pay@mrjoy.com>"
    assert out[0].subject == "Factura octubre"
    assert conn.selected == ("INBOX", True)
    assert conn.logged_out is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_imap_email_reader.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ari/infrastructure/email/imap_email_reader.py`:

```python
"""Native IMAP reader (stdlib only). Blocking; callers run it off the loop."""
import email
import imaplib
from email.header import decode_header, make_header

from ari.domain.email.entities import EmailSummary, FetchedEmail, MailboxSpec

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — malformed header: fall back to raw
        return value


def _q(value: str) -> str:
    return '"' + value.replace('"', "") + '"'


def _imap_date(value: str) -> str:
    year, month, day = value.split("-")
    return f"{int(day):02d}-{_MONTHS[int(month) - 1]}-{year}"


def _imap_criteria(criteria: str) -> list[str]:
    text = (criteria or "").strip()
    if not text:
        return ["ALL"]
    low = text.lower()
    if low.startswith("de:"):
        return ["FROM", _q(text[3:].strip())]
    if low.startswith("asunto:"):
        return ["SUBJECT", _q(text[7:].strip())]
    if low.startswith("desde:"):
        try:
            return ["SINCE", _imap_date(text[6:].strip())]
        except (ValueError, IndexError):
            return ["ALL"]
    if low in ("no leidos", "no leídos", "nuevos", "unseen"):
        return ["UNSEEN"]
    return ["TEXT", _q(text)]


def _logout(conn) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001 — best-effort close
        pass


class ImapEmailReader:
    def __init__(self, *, timeout: float = 20.0,
                 ssl_factory=imaplib.IMAP4_SSL, plain_factory=imaplib.IMAP4):
        self._timeout = timeout
        self._ssl_factory = ssl_factory
        self._plain_factory = plain_factory

    def _connect(self, spec: MailboxSpec):
        if spec.imap_secure:
            conn = self._ssl_factory(spec.imap_host, spec.imap_port, timeout=self._timeout)
        else:
            conn = self._plain_factory(spec.imap_host, spec.imap_port, timeout=self._timeout)
            conn.starttls()
        conn.login(spec.user, spec.password)
        return conn

    def search(self, spec: MailboxSpec, criteria: str, limit: int) -> list[EmailSummary]:
        conn = self._connect(spec)
        try:
            conn.select("INBOX", readonly=True)
            _typ, data = conn.uid("SEARCH", None, *_imap_criteria(criteria))
            uids = data[0].split() if data and data[0] else []
            uids = uids[-limit:][::-1]  # newest UID first
            return [self._summary(conn, uid) for uid in uids]
        finally:
            _logout(conn)

    def _summary(self, conn, uid: bytes) -> EmailSummary:
        _typ, data = conn.uid(
            "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        raw = data[0][1] if data and data[0] and isinstance(data[0], tuple) else b""
        msg = email.message_from_bytes(raw)
        return EmailSummary(
            uid=uid.decode(), from_addr=_decode(msg.get("From")),
            subject=_decode(msg.get("Subject")), date=_decode(msg.get("Date")),
            snippet="")
```

> Note: `snippet` stays empty in v1 (header-only search keeps it to one round-trip per message). The field exists for a later best-effort preview without changing the contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_imap_email_reader.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/email/imap_email_reader.py tests/infrastructure/test_imap_email_reader.py
git commit -m "feat(email): ImapEmailReader.search with IMAP criteria mapping"
```

---

### Task 6: `ImapEmailReader.fetch` + MIME parsing

**Files:**
- Modify: `src/ari/infrastructure/email/imap_email_reader.py`
- Test: `tests/infrastructure/test_imap_email_reader.py`

**Interfaces:**
- Consumes: `MailboxSpec`, `html_to_text` (Task 2).
- Produces: `ImapEmailReader.fetch(spec, uid) -> FetchedEmail` (raises `LookupError` when the UID is absent); module helper `_parse_parts(msg) -> tuple[str, list[EmailAttachment]]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/infrastructure/test_imap_email_reader.py`:

```python
import email.message
import pytest

from ari.domain.email.entities import EmailAttachment  # noqa: E402
from ari.infrastructure.email.imap_email_reader import _parse_parts  # noqa: E402


def _build_multipart() -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = "=?UTF-8?Q?Pagos_Mrjoy?= <pay@mrjoy.com>"
    msg["To"] = "me@corp.com"
    msg["Subject"] = "Factura"
    msg["Date"] = "Mon, 5 Oct 2026 10:00:00 -0500"
    msg.set_content("Total: 1.234 USD")
    msg.add_alternative("<p>Total: <b>1.234</b> USD</p>", subtype="html")
    msg.add_attachment(b"%PDF-1.4 bytes", maintype="application",
                       subtype="pdf", filename="factura.pdf")
    return msg.as_bytes()


class FetchConn:
    def __init__(self, raw): self._raw = raw; self.logged_out = False
    def login(self, u, p): pass
    def select(self, m, readonly=False): pass
    def uid(self, command, *args):
        if command == "FETCH" and self._raw is not None:
            return "OK", [(args[0] + b" (RFC822", self._raw)]
        return "OK", [None]
    def logout(self): self.logged_out = True


def test_fetch_parses_body_and_attachment():
    from ari.infrastructure.email.imap_email_reader import ImapEmailReader
    conn = FetchConn(_build_multipart())
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: conn)
    msg = reader.fetch(SPEC, "7")
    assert msg.uid == "7"
    assert msg.from_addr == "Pagos Mrjoy <pay@mrjoy.com>"
    assert "Total: 1.234 USD" in msg.body_text  # prefers text/plain
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "factura.pdf"
    assert msg.attachments[0].data == b"%PDF-1.4 bytes"
    assert conn.logged_out is True


def test_fetch_missing_uid_raises_lookup():
    from ari.infrastructure.email.imap_email_reader import ImapEmailReader
    reader = ImapEmailReader(ssl_factory=lambda *a, **k: FetchConn(None))
    with pytest.raises(LookupError):
        reader.fetch(SPEC, "999")


def test_parse_parts_falls_back_to_html_when_no_plain():
    msg = email.message.EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("<div>Hola &amp; chau</div>", subtype="html")
    body, atts = _parse_parts(msg)
    assert "Hola & chau" in body and atts == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_imap_email_reader.py -k "fetch or parse_parts" -v`
Expected: FAIL (`fetch` / `_parse_parts` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/ari/infrastructure/email/imap_email_reader.py`, add the import and helpers, and the `fetch` method.

Add near the top imports:

```python
from ari.domain.email.entities import EmailAttachment
from ari.infrastructure.email.html_text import html_to_text
```

Add module-level helpers (after `_logout`):

```python
def _part_text(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _parse_parts(msg) -> tuple[str, list[EmailAttachment]]:
    body_plain, body_html, attachments = "", "", []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        ctype = part.get_content_type()
        if filename or "attachment" in disposition:
            attachments.append(EmailAttachment(
                _decode(filename) or "adjunto", ctype,
                part.get_payload(decode=True) or b""))
        elif ctype == "text/plain" and not body_plain:
            body_plain = _part_text(part)
        elif ctype == "text/html" and not body_html:
            body_html = _part_text(part)
    body = body_plain or html_to_text(body_html)
    return body.strip(), attachments
```

Add the `fetch` method to `ImapEmailReader`:

```python
    def fetch(self, spec: MailboxSpec, uid: str) -> FetchedEmail:
        conn = self._connect(spec)
        try:
            conn.select("INBOX", readonly=True)
            _typ, data = conn.uid("FETCH", uid.encode(), "(RFC822)")
            if not data or not data[0] or not isinstance(data[0], tuple):
                raise LookupError(uid)
            msg = email.message_from_bytes(data[0][1])
            body, attachments = _parse_parts(msg)
            return FetchedEmail(
                uid=uid, from_addr=_decode(msg.get("From")),
                to_addr=_decode(msg.get("To")), subject=_decode(msg.get("Subject")),
                date=_decode(msg.get("Date")), body_text=body,
                attachments=tuple(attachments))
        finally:
            _logout(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_imap_email_reader.py -v`
Expected: PASS (all reader tests).

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/email/imap_email_reader.py tests/infrastructure/test_imap_email_reader.py
git commit -m "feat(email): ImapEmailReader.fetch with MIME body and attachment parsing"
```

---

### Task 7: `AriTools` wiring + `buscar_correos`

**Files:**
- Modify: `src/ari/application/ari_tools.py`
- Test: `tests/application/test_ari_tools_email_reader.py`

**Interfaces:**
- Consumes: `EmailReaderPort` (Task 1), `MailboxSpec` lookup (Task 4).
- Produces: `AriTools(..., email_reader=None, mailboxes=None)` constructor kwargs; `AriTools.buscar_correos(cuenta, criterio="", limite=10) -> str`; module helper `_safe_name(name: str) -> str`; method `_mailbox(cuenta) -> tuple[MailboxSpec | None, str | None]`.

Note: `buscar_correos` is gated by `_allowed("buscar_correos")`, which only returns true once Task 9 registers the tool. So this task's tests build the `Actor` with `is_owner=True, context=CHAT` **and** Task 9 must be done for the permission check to pass. To keep this task self-contained, its tests assert behavior assuming the permission is granted; if run before Task 9 they will return `DENIED`. Implement Task 9 before or alongside this task's test run, OR temporarily include `buscar_correos` in the permission set. **Recommended order: do Task 9 first.** (The plan lists Task 9 after for grouping, but execution may reorder; the subagent executor should do Task 9 before Task 7's GREEN step.)

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_ari_tools_email_reader.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ari.application.ari_tools import Actor, AriTools
from ari.domain.email.entities import EmailSummary, FetchedEmail, MailboxSpec
from ari.domain.tools.ari_permissions import CHAT
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.workspace.user_workspace import Workspaces

import pytest

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
SPEC = MailboxSpec("email_corp", "imap.x.com", 993, True, "u@x.com", "pw")


class FakeReader:
    def __init__(self, summaries=None, message=None, boom=False):
        self._summaries, self._message, self._boom = summaries or [], message, boom
    def search(self, spec, criteria, limit):
        if self._boom:
            raise OSError("imap down")
        return self._summaries[:limit]
    def fetch(self, spec, uid):
        if self._boom:
            raise OSError("imap down")
        if self._message is None:
            raise LookupError(uid)
        return self._message


@pytest.fixture
async def env(tmp_path):
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, Workspaces(str(tmp_path)), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, *, reader, mailboxes=None):
    conn, workspaces, log = env
    actor = Actor("42", "42", "Gabriel", True, CHAT, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log,
                    tz=TZ, max_items=20, clock=lambda: NOW, workspaces=workspaces,
                    email_reader=reader,
                    mailboxes=mailboxes if mailboxes is not None else {"email_corp": SPEC})


async def test_buscar_lists_results(env):
    reader = FakeReader(summaries=[
        EmailSummary("3", "pay@mrjoy.com", "Factura", "5 Oct", ""),
        EmailSummary("2", "no@netlife.com", "Pago", "1 Oct", "")])
    out = await _tools(env, reader=reader).buscar_correos("email_corp")
    assert "#3" in out and "Factura" in out and "#2" in out


async def test_buscar_unknown_account(env):
    out = await _tools(env, reader=FakeReader()).buscar_correos("gmail")
    assert "no tenés una casilla" in out.lower()


async def test_buscar_no_mailboxes(env):
    out = await _tools(env, reader=FakeReader(), mailboxes={}).buscar_correos("x")
    assert "no tenés casillas" in out.lower()


async def test_buscar_connection_error_is_friendly(env):
    out = await _tools(env, reader=FakeReader(boom=True)).buscar_correos("email_corp")
    assert "no pude conectarme" in out.lower()
    assert "pw" not in out  # never leaks the password
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_ari_tools_email_reader.py -k buscar -v`
Expected: FAIL (`AriTools` has no `email_reader`/`mailboxes` kwargs or `buscar_correos`).

- [ ] **Step 3: Write minimal implementation**

In `src/ari/application/ari_tools.py`:

1. Add `import asyncio` at the top (after `import logging`).
2. Add a module-level helper near the other helpers (e.g. after `_match`):

```python
import os  # add to the import block at the top
import re  # add to the import block at the top

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_name(name: str) -> str:
    base = os.path.basename((name or "").strip())
    cleaned = _UNSAFE_NAME.sub("_", base)
    return cleaned if cleaned not in ("", ".", "..") else ""
```

3. Add the two constructor kwargs. Change the signature line:

```python
                 email_accounts=None, email_enroll=None,
                 workspaces=None, sql_sandbox=None, runner=None,
                 email_reader=None, mailboxes=None):
```

and add to the body (near the other email attributes):

```python
        self._email_reader = email_reader   # EmailReaderPort | None
        self._mailboxes = mailboxes or {}   # dict[str, MailboxSpec]
```

4. Add the mailbox resolver and `buscar_correos` in the `# ---- workspace (files)` region (after `leer_documento`):

```python
    # ---- email reading ----------------------------------------------------

    def _mailbox(self, cuenta: str):
        if self._email_reader is None or not self._mailboxes:
            return None, "No tenés casillas de correo conectadas."
        spec = self._mailboxes.get((cuenta or "").strip())
        if spec is None:
            names = ", ".join(sorted(self._mailboxes)) or "(ninguna)"
            return None, f"No tenés una casilla llamada «{cuenta}». Tus casillas: {names}."
        return spec, None

    async def buscar_correos(self, cuenta: str, criterio: str = "", limite: int = 10) -> str:
        if not self._allowed("buscar_correos"):
            return DENIED
        spec, error = self._mailbox(cuenta)
        if error:
            return error
        try:
            limite = max(1, min(int(limite or 10), 25))
        except (TypeError, ValueError):
            limite = 10
        try:
            summaries = await asyncio.to_thread(
                self._email_reader.search, spec, criterio or "", limite)
        except Exception:  # noqa: BLE001 — network/IMAP failure; never leak creds
            log.exception("buscar_correos failed for %s", cuenta)
            return f"No pude conectarme a la casilla «{cuenta}»."
        if not summaries:
            return "No encontré correos con ese criterio."
        return "\n".join(
            f"#{s.uid} · {s.from_addr} · {s.subject} · {s.date} · {s.snippet}".rstrip(" ·")
            for s in summaries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/application/test_ari_tools_email_reader.py -k buscar -v`
Expected: PASS. (Requires Task 9's permission entry — do Task 9 first.)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_email_reader.py
git commit -m "feat(email): AriTools.buscar_correos over the native reader"
```

---

### Task 8: `AriTools.leer_correo` + workspace spill

**Files:**
- Modify: `src/ari/application/ari_tools.py`
- Test: `tests/application/test_ari_tools_email_reader.py`

**Interfaces:**
- Consumes: `EmailReaderPort.fetch`, `_mailbox`, `_safe_name`, `UserWorkspace.write_text`/`write_bytes`.
- Produces: `AriTools.leer_correo(cuenta, id) -> str`; method `_save_attachments(ws, cuenta, msg) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/application/test_ari_tools_email_reader.py`:

```python
from ari.domain.email.entities import EmailAttachment  # noqa: E402


def _msg(body="Total: 1.234 USD", attachments=()):
    return FetchedEmail("7", "pay@mrjoy.com", "me@corp.com", "Factura",
                        "5 Oct", body, tuple(attachments))


async def test_leer_correo_formats_and_spills_body(env):
    _conn, workspaces, _ = env
    reader = FakeReader(message=_msg())
    out = await _tools(env, reader=reader).leer_correo("email_corp", "7")
    assert "Asunto: Factura" in out
    assert "Total: 1.234 USD" in out
    assert "correos/email_corp-7.txt" in out
    ws = workspaces.for_user("42")
    assert "1.234" in ws.read_text("correos/email_corp-7.txt")


async def test_leer_correo_saves_attachments(env):
    _conn, workspaces, _ = env
    att = EmailAttachment("factura.pdf", "application/pdf", b"%PDF bytes")
    out = await _tools(env, reader=FakeReader(message=_msg(attachments=[att]))
                       ).leer_correo("email_corp", "7")
    assert "correos/adjuntos/email_corp-7/01-factura.pdf" in out
    ws = workspaces.for_user("42")
    assert ws.read_bytes("correos/adjuntos/email_corp-7/01-factura.pdf") == b"%PDF bytes"


async def test_leer_correo_sanitizes_attachment_name(env):
    _conn, workspaces, _ = env
    att = EmailAttachment("../../etc/passwd", "text/plain", b"x")
    out = await _tools(env, reader=FakeReader(message=_msg(attachments=[att]))
                       ).leer_correo("email_corp", "7")
    # saved safely inside the per-email folder, never escaping
    assert "correos/adjuntos/email_corp-7/" in out
    assert ".." not in out.split("Adjuntos")[-1]


async def test_leer_correo_truncates_body_over_text_cap(env):
    _conn, workspaces, _ = env
    big = "A" * 1_200_000  # over the 1 MB text cap
    out = await _tools(env, reader=FakeReader(message=_msg(body=big))
                       ).leer_correo("email_corp", "7")
    ws = workspaces.for_user("42")
    saved = ws.read_text("correos/email_corp-7.txt")
    assert "(truncado)" in saved and len(saved.encode("utf-8")) <= 1_048_576 + 50


async def test_leer_correo_missing_uid(env):
    out = await _tools(env, reader=FakeReader(message=None)).leer_correo("email_corp", "999")
    assert "no encontré el correo" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_ari_tools_email_reader.py -k leer_correo -v`
Expected: FAIL (`leer_correo` not defined).

- [ ] **Step 3: Write minimal implementation**

Add to `src/ari/application/ari_tools.py`, right after `buscar_correos`:

```python
    _BODY_CAP = 8000           # inline preview characters
    _TEXT_FILE_CAP = 1_048_576  # workspace write_text byte cap
    _MAX_ATTACHMENTS = 20

    async def leer_correo(self, cuenta: str, id: str) -> str:
        if not self._allowed("leer_correo"):
            return DENIED
        spec, error = self._mailbox(cuenta)
        if error:
            return error
        if self._ws_factory is None:
            return "El espacio de trabajo no está disponible."
        try:
            msg = await asyncio.to_thread(self._email_reader.fetch, spec, str(id))
        except LookupError:
            return f"No encontré el correo #{id}."
        except Exception:  # noqa: BLE001 — network/IMAP failure; never leak creds
            log.exception("leer_correo failed for %s", cuenta)
            return f"No pude conectarme a la casilla «{cuenta}»."
        ws = self._ws()
        body = msg.body_text or "(sin texto en el cuerpo)"
        preview = (body if len(body) <= self._BODY_CAP
                   else body[:self._BODY_CAP] + "\n… (truncado; cuerpo completo en el archivo)")
        lines = [f"De: {msg.from_addr} · Para: {msg.to_addr} · Fecha: {msg.date}",
                 f"Asunto: {msg.subject}", "", preview]
        body_rel = f"correos/{cuenta}-{msg.uid}.txt"
        try:
            ws.write_text(body_rel, self._cap_bytes(body))
            lines += ["", f"📄 Cuerpo completo: {body_rel}"]
        except (ValueError, OSError):
            log.warning("could not save email body %s", body_rel)
        saved = self._save_attachments(ws, cuenta, msg)
        if saved:
            lines.append("📎 Adjuntos (usá leer_documento sobre cada ruta):")
            lines += [f"- {path}" for path in saved]
        return await self._receipt("\n".join(lines))

    def _cap_bytes(self, body: str) -> str:
        encoded = body.encode("utf-8")
        if len(encoded) <= self._TEXT_FILE_CAP:
            return body
        return encoded[:self._TEXT_FILE_CAP].decode("utf-8", "ignore") + "\n… (truncado)"

    def _save_attachments(self, ws, cuenta: str, msg) -> list[str]:
        saved: list[str] = []
        skipped = 0
        for index, att in enumerate(msg.attachments[:self._MAX_ATTACHMENTS], start=1):
            name = _safe_name(att.filename) or f"adjunto-{index}"
            rel = f"correos/adjuntos/{cuenta}-{msg.uid}/{index:02d}-{name}"
            try:
                ws.write_bytes(rel, att.data)
                saved.append(rel)
            except (ValueError, OSError):
                skipped += 1
        extra = len(msg.attachments) - self._MAX_ATTACHMENTS
        if extra > 0:
            skipped += extra
        if skipped:
            saved.append(f"(se omitieron {skipped} adjunto(s) por tamaño o cantidad)")
        return saved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/application/test_ari_tools_email_reader.py -v`
Expected: PASS (all email-reader app tests).

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py tests/application/test_ari_tools_email_reader.py
git commit -m "feat(email): AriTools.leer_correo with workspace body/attachment spill"
```

---

### Task 9: Register the tools in the permission table

**Files:**
- Modify: `src/ari/domain/tools/ari_permissions.py`
- Test: `tests/domain/test_ari_permissions.py` (create if absent)

**Interfaces:**
- Consumes: nothing.
- Produces: `buscar_correos` and `leer_correo` present in `ARI_TOOLS` and in `_USER_CHAT`.

> Execution note: do this task **before** Task 7/8's GREEN steps, because `_allowed("buscar_correos")` / `_allowed("leer_correo")` must return true for those tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_ari_permissions.py
from ari.domain.tools.ari_permissions import (CHAT, HEARTBEAT, TASK,
                                              allowed_ari_tools)


def test_email_reading_allowed_for_owner_chat():
    allowed = allowed_ari_tools(True, CHAT)
    assert "buscar_correos" in allowed and "leer_correo" in allowed


def test_email_reading_allowed_for_user_chat():
    allowed = allowed_ari_tools(False, CHAT)
    assert "buscar_correos" in allowed and "leer_correo" in allowed


def test_email_reading_absent_in_task_and_heartbeat():
    for ctx in (TASK, HEARTBEAT):
        allowed = allowed_ari_tools(True, ctx)
        assert "buscar_correos" not in allowed and "leer_correo" not in allowed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_ari_permissions.py -v`
Expected: FAIL (tools not in the sets).

- [ ] **Step 3: Write minimal implementation**

In `src/ari/domain/tools/ari_permissions.py`:

Add `"buscar_correos", "leer_correo"` to the `ARI_TOOLS` tuple (next to the other email/workspace tools), and add them to the `_USER_CHAT` set:

```python
ARI_TOOLS = ("agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "aprobar_acceso", "revocar_acceso", "ver_accesos",
             "enviar_mensaje", "proponer_codigo", "proponer_comando",
             "asignar_mision", "ver_misiones", "cancelar_mision",
             "pedir_credenciales",
             "ver_skills", "activar_skill", "desactivar_skill",
             "compartir", "ver_permisos", "revocar_permiso",
             "conectar_correo", "mis_correos", "olvidar_correo",
             "buscar_correos", "leer_correo",
             "escribir_archivo", "leer_archivo", "listar_archivos",
             "borrar_archivo", "consultar_sql", "ejecutar", "leer_documento")

_READ = {"listar_agenda", "ver_datos"}
_USER_CHAT = _READ | {"agendar", "cancelar", "recordar_dato", "olvidar_dato",
                      "compartir", "ver_permisos", "revocar_permiso",
                      "conectar_correo", "mis_correos", "olvidar_correo",
                      "buscar_correos", "leer_correo",
                      "escribir_archivo", "leer_archivo", "listar_archivos",
                      "borrar_archivo", "consultar_sql", "leer_documento"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_ari_permissions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/tools/ari_permissions.py tests/domain/test_ari_permissions.py
git commit -m "feat(email): allow buscar_correos/leer_correo in CHAT for owner and users"
```

---

### Task 10: Inject `ARI_EMAIL_ACCOUNTS` into the per-turn `ari` server env

**Files:**
- Modify: `src/ari/application/tools/tool_policy.py`
- Test: `tests/application/test_tool_policy_turn.py`

**Interfaces:**
- Consumes: `email_accounts_from_servers`, `mailboxes_to_json` (Task 4).
- Produces: the `ari` server's env in `ToolPolicy.turn` includes `ARI_EMAIL_ACCOUNTS` (JSON string of the turn's mailboxes).

- [ ] **Step 1: Write the failing test**

Append to `tests/application/test_tool_policy_turn.py`:

```python
async def test_turn_injects_email_accounts(tmp_path):
    from ari.infrastructure.email.server_spec import mailboxes_from_json
    mail = {"email_corp": {"command": "npx", "args": ["-y", "mcp-mail-server@2.1.0"],
                           "env": {"IMAP_HOST": "imap.corp.com", "IMAP_PORT": "993",
                                   "IMAP_SECURE": "true", "EMAIL_USER": "me@corp.com",
                                   "EMAIL_PASS": "s3cret"}}}
    policy = _policy(tmp_path, mail)
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        with open(turn.toolset.mcp_config_path, encoding="utf-8") as f:
            env = json.load(f)["mcpServers"]["ari"]["env"]
    boxes = mailboxes_from_json(env["ARI_EMAIL_ACCOUNTS"])
    assert set(boxes) == {"email_corp"}
    assert boxes["email_corp"].imap_host == "imap.corp.com"
    assert boxes["email_corp"].password == "s3cret"


async def test_turn_without_mailboxes_has_empty_accounts(tmp_path):
    policy = _policy(tmp_path, {"google": {"command": "uvx", "args": ["workspace-mcp"]}})
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        with open(turn.toolset.mcp_config_path, encoding="utf-8") as f:
            env = json.load(f)["mcpServers"]["ari"]["env"]
    assert env["ARI_EMAIL_ACCOUNTS"] == "[]"
```

> This adds a new key to the `ari` env. Update the strict equality in the existing
> `test_owner_chat_turn` so it still passes: add `"ARI_EMAIL_ACCOUNTS": "[]"` to the
> expected `env` dict in that test.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_tool_policy_turn.py -v`
Expected: FAIL (`ARI_EMAIL_ACCOUNTS` missing; and `test_owner_chat_turn` fails until its expected dict is updated).

- [ ] **Step 3: Write minimal implementation**

In `src/ari/application/tools/tool_policy.py`, inside `turn`, after `await self._inject_email(servers, user_id)` and before building the `ari` server entry:

```python
        from ari.infrastructure.email.server_spec import (email_accounts_from_servers,
                                                          mailboxes_to_json)
        accounts_json = mailboxes_to_json(email_accounts_from_servers(servers))
```

Then add the key to the `ari` env dict:

```python
        servers[ARI_SERVER] = {
            "command": self._ari.command, "args": list(self._ari.args),
            "env": {**self._ari.base_env, "ARI_ACTOR_ID": user_id,
                    "ARI_ACTOR_CHAT": chat_id or user_id, "ARI_ACTOR_NAME": actor_name,
                    "ARI_ROLE": "owner" if owner else "user", "ARI_CONTEXT": context,
                    "ARI_TURN_ID": turn_id, "ARI_EMAIL_ACCOUNTS": accounts_json}}
```

Also update `test_owner_chat_turn`'s expected `env` to include `"ARI_EMAIL_ACCOUNTS": "[]"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/application/test_tool_policy_turn.py -v`
Expected: PASS (all turn tests).

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/tools/tool_policy.py tests/application/test_tool_policy_turn.py
git commit -m "feat(email): pass resolved mailboxes to the ari server per turn"
```

---

### Task 11: Wire the reader in the `ari` server + expose the MCP tools

**Files:**
- Modify: `src/ari/mcp_server/__main__.py`
- Modify: `src/ari/mcp_server/server.py`
- Test: `tests/infrastructure/test_mcp_server.py`

**Interfaces:**
- Consumes: `mailboxes_from_json` (Task 4), `ImapEmailReader` (Task 5/6), `AriTools(email_reader, mailboxes)` (Task 7).
- Produces: the `ari` MCP server exposes `buscar_correos` and `leer_correo`, delegating to `AriTools`.

- [ ] **Step 1: Write the failing test**

First inspect the existing `tests/infrastructure/test_mcp_server.py` to match its harness (how it builds the server and lists tools). Then append a test in that file's style. A representative test:

```python
async def test_email_reading_tools_are_registered_and_delegate():
    # build_server exposes the two email tools and they call through to AriTools.
    calls = {}

    class FakeTools:
        async def buscar_correos(self, cuenta, criterio="", limite=10):
            calls["buscar"] = (cuenta, criterio, limite)
            return "ok-buscar"
        async def leer_correo(self, cuenta, id):
            calls["leer"] = (cuenta, id)
            return "ok-leer"

    from ari.mcp_server.server import build_server
    server = build_server(lambda: _await(FakeTools()))  # match existing harness
    names = {t.name for t in await server.list_tools()}
    assert {"buscar_correos", "leer_correo"} <= names
```

> Adapt `build_server(...)` construction and tool invocation to whatever
> `test_mcp_server.py` already does (it is the source of truth for the harness).
> The assertion that matters: both tool names are registered, and invoking them
> reaches `AriTools.buscar_correos` / `leer_correo` with the passed arguments.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_mcp_server.py -k email -v`
Expected: FAIL (tools not registered).

- [ ] **Step 3: Write minimal implementation**

In `src/ari/mcp_server/server.py`, add two wrappers next to `leer_documento` (inside `build_server`):

```python
    @tool("buscar_correos")
    async def buscar_correos(cuenta: str, criterio: str = "", limite: int = 10) -> str:
        """Busca correos en una de tus casillas conectadas y devuelve una lista
        corta (#id, remitente, asunto, fecha). cuenta es el nombre que aparece en
        «Tus herramientas» (p. ej. "email_corp"). criterio: texto libre, o
        "de:correo@dominio", "asunto:palabra", "desde:AAAA-MM-DD", "no leidos"."""
        return await (await get_tools()).buscar_correos(cuenta, criterio, limite)

    @tool("leer_correo")
    async def leer_correo(cuenta: str, id: str) -> str:
        """Lee un correo completo por su #id (de buscar_correos). Devuelve el
        cuerpo en texto limpio y, para correos grandes, guarda el cuerpo completo
        y los adjuntos en tu espacio de trabajo (carpeta "correos/"); usá
        leer_documento sobre cada adjunto (PDF, Excel) para extraer su contenido.
        Es la forma preferida de leer correos, sobre todo los grandes."""
        return await (await get_tools()).leer_correo(cuenta, id)
```

In `src/ari/mcp_server/__main__.py`, inside `_get_tools`, build the reader and mailboxes from env and pass them to `AriTools`:

```python
        from ari.infrastructure.email.imap_email_reader import ImapEmailReader
        from ari.infrastructure.email.server_spec import mailboxes_from_json
        ...
        mailboxes = mailboxes_from_json(env.get("ARI_EMAIL_ACCOUNTS", ""))
        email_reader = ImapEmailReader() if mailboxes else None
```

and add to the `AriTools(...)` construction:

```python
            email_reader=email_reader, mailboxes=mailboxes,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + ruff, then commit**

Run: `pytest -q && ruff check src tests`
Expected: all green, no lint errors.

```bash
git add src/ari/mcp_server/server.py src/ari/mcp_server/__main__.py tests/infrastructure/test_mcp_server.py
git commit -m "feat(email): expose buscar_correos/leer_correo and wire the IMAP reader"
```

---

## Self-Review

**1. Spec coverage**

- §4.1 domain entities + port → Task 1. ✓
- §4.2 ImapEmailReader (connect/search/fetch, html→text) → Tasks 5, 6 (+ Task 2 for html). ✓
- §4.3 `write_bytes` → Task 3. ✓
- §4.4 AriTools methods → Tasks 7, 8. ✓
- §4.5 MCP wrappers → Task 11. ✓
- §5.1 `buscar_correos` contract + criteria mapping → Tasks 5, 7. ✓
- §5.2 `leer_correo` contract → Task 8. ✓
- §5.3 workspace spill (body + attachments, sanitization, caps) → Task 8. ✓
- §6 credential plumbing (`email_accounts_from_servers`, env injection, parse in server) → Tasks 4, 10, 11. ✓
- §7 permissions → Task 9. ✓
- §8 edge cases (unknown cuenta, no mailboxes, IMAP failure, missing UID, html-empty, over-cap, too many attachments) → Tasks 7, 8. ✓
- §9 testing plan → every task is TDD. ✓
- §10 files touched → matches the tasks' file lists. ✓

No uncovered spec requirement.

**2. Placeholder scan**

No "TBD"/"TODO"/"add error handling"/"similar to Task N". Task 11's test references the existing `test_mcp_server.py` harness as the source of truth and gives the concrete assertion; this is a deliberate adaptation instruction, not a placeholder, because the harness shape is only known by reading that file.

**3. Type consistency**

- `MailboxSpec`/`EmailSummary`/`EmailAttachment`/`FetchedEmail` fields are identical across Tasks 1, 5, 6, 7, 8. ✓
- `email_accounts_from_servers → list[MailboxSpec]`, `mailboxes_to_json(list) → str`, `mailboxes_from_json(str) → dict[str, MailboxSpec]` consistent across Tasks 4, 10, 11. ✓
- `AriTools(..., email_reader=None, mailboxes=None)` introduced in Task 7 and consumed in Task 11. ✓
- `EmailReaderPort.search/fetch` signatures match the `FakeReader` in Tasks 7/8 and `ImapEmailReader` in Tasks 5/6. ✓

**4. Review Focus**

- MIME encoded-word headers → Task 6 `test_fetch_parses_body_and_attachment` (decodes "Pagos Mrjoy"). ✓
- HTML-only body with entities/nested tags → Task 2 tests + Task 6 `test_parse_parts_falls_back_to_html_when_no_plain`. ✓
- Attachment traversal/unsafe filename → Task 8 `test_leer_correo_sanitizes_attachment_name` + Task 3 `test_write_bytes_refuses_escape`. ✓
- Body over the 1 MB cap → Task 8 `test_leer_correo_truncates_body_over_text_cap`. ✓
- IMAP failure not leaking password → Task 7 `test_buscar_connection_error_is_friendly` (asserts `"pw" not in out`) + Task 8 fetch error path. ✓

All five Review Focus items are pinned to tasks.
