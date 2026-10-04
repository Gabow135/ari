# Per-user email (BYO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every Telegram user connect their own IMAP/SMTP mailboxes (multiple each), so Ari reads and sends from *that user's* account, with credentials loaded via a zero-ingress sealed-box blob.

**Architecture:** A new `user_email_accounts` SQLite table stores each user's mailboxes with the password encrypted at rest (Fernet, same `ARI_VAULT_KEY`). At each turn, `ToolPolicy.turn(user_id)` — which already writes a fresh per-turn MCP config and deletes it — injects `mail_<label>` email MCP servers resolved from the acting user's rows. Credentials are loaded by sending the user a self-contained HTML file (Telegram document) that seals the account to Ari's X25519 public key; the user pastes back an `ari-mail:v1:<b64>` blob that is intercepted before memory, decrypted with Ari's private key, and stored.

**Tech Stack:** Python 3, aiosqlite, `cryptography` (Fernet, already present), PyNaCl (new — sealed box), vendored libsodium.js (browser side), python-telegram-bot (already used), MCP (`mcp-mail-server@2.1.0`).

**Spec:** `docs/superpowers/specs/2026-10-04-per-user-email-design.md`

## Global Constraints

- Plaintext passwords must NEVER be persisted to memory/recalls/turn log, nor pass through any publicly reachable URL. Blobs are ciphertext; still intercept + delete them.
- Password at rest = Fernet ciphertext under `ARI_VAULT_KEY`. The key never lives in the DB.
- The Ari MCP server process has NO `ARI_VAULT_KEY` (its env is only `ARI_DB_PATH`, `ARI_TIMEZONE`, `ARI_MAX_ITEMS`, `ARI_OWNER_IDS`, `ARI_SKILLS_DIR`). Only the bot process decrypts. MCP-side reads must be cipher-free (masked summaries) and removes are pure SQL.
- Injected per-user MCP server names are prefixed `mail_` to never collide with the owner's `servers.json` `email_*`.
- Sealed-box base64 uses the ORIGINAL variant (standard, padded) on BOTH the JS and Python sides.
- Email MCP server package is pinned `mcp-mail-server@2.1.0` (matches `mcp/servers.json`).
- New table is added to `_SCHEMA` with `CREATE TABLE IF NOT EXISTS` so it migrates on the next `connect`/`open_existing`.
- Strict TDD: observe RED before writing implementation. Existing test runner: `uv run pytest`.
- Conventional Commits; no AI attribution in messages.

## Review Focus

- **Base64 variant mismatch (JS ↔ Python):** libsodium.js `to_base64` defaults to URL-safe-no-padding; Python `base64.b64decode` expects standard. If they disagree, every blob fails to decrypt. Pinned to ORIGINAL both sides — Task 7 tests Python `open()` against a PyNaCl-sealed, standard-base64 blob.
- **`ARI_VAULT_KEY` unset:** no key pair can be minted and nothing can be stored/injected. `conectar_correo` must explain, injection must skip silently, nothing must crash — Task 5 and Task 10 tests cover the None-cipher / no-keypair path.
- **Unsafe label:** a label like `corp`, `../x`, or `gmail` must be slug-validated so the injected `mail_<label>` is filesystem/name safe and cannot shadow another server — Task 7 tests rejection of non-slug labels.
- **Malformed blob must not leak into the LLM/memory:** an `ari-mail:v1:` message that fails to decrypt must be intercepted, answered with an error, and NEVER forwarded to the handler or appended to memory — Task 11 tests `is_email_blob` + the rejection path returns without calling the handler.
- **MCP process cannot decrypt:** `mis_correos`/`olvidar_correo` run where there is no vault key; they must not call any decrypt path — Task 3 gives `summaries_for`/`remove` that never touch the cipher, tested with `cipher=None`.

---

### Task 1: SecretCipher + FernetCipher + PyNaCl dependency

**Files:**
- Modify: `pyproject.toml` (add `pynacl` to `dependencies`)
- Create: `src/ari/domain/crypto/secret_cipher.py`
- Create: `src/ari/domain/crypto/__init__.py` (empty)
- Create: `src/ari/infrastructure/crypto/fernet_cipher.py`
- Create: `src/ari/infrastructure/crypto/__init__.py` (empty)
- Test: `tests/infrastructure/test_fernet_cipher.py`

**Interfaces:**
- Produces: `SecretCipher` protocol with `encrypt(self, plaintext: str) -> str` and `decrypt(self, token: str) -> str`. `FernetCipher(key: str)` implementing it; `FernetCipher.__init__` raises `RuntimeError` if `key` is empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_fernet_cipher.py
import pytest
from cryptography.fernet import Fernet
from ari.infrastructure.crypto.fernet_cipher import FernetCipher


def test_encrypt_decrypt_round_trip():
    cipher = FernetCipher(Fernet.generate_key().decode())
    token = cipher.encrypt("hunter2")
    assert token != "hunter2"
    assert cipher.decrypt(token) == "hunter2"


def test_empty_key_raises():
    with pytest.raises(RuntimeError):
        FernetCipher("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_fernet_cipher.py -v`
Expected: FAIL (ModuleNotFoundError: ari.infrastructure.crypto.fernet_cipher)

- [ ] **Step 3: Write the protocol and implementation**

```python
# src/ari/domain/crypto/secret_cipher.py
from typing import Protocol


class SecretCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, token: str) -> str: ...
```

```python
# src/ari/infrastructure/crypto/fernet_cipher.py
from cryptography.fernet import Fernet

from ari.domain.crypto.secret_cipher import SecretCipher


class FernetCipher(SecretCipher):
    """Encrypts/decrypts a single string with the same ARI_VAULT_KEY the vault uses."""

    def __init__(self, key: str):
        if not key:
            raise RuntimeError("ARI_VAULT_KEY no está configurada")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
```

- [ ] **Step 4: Add PyNaCl dependency**

Add `"pynacl>=1.5"` to the `dependencies` array in `pyproject.toml`, then run `uv sync --all-extras`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_fernet_cipher.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/ari/domain/crypto src/ari/infrastructure/crypto tests/infrastructure/test_fernet_cipher.py
git commit -m "feat(email): add SecretCipher/FernetCipher and PyNaCl dependency"
```

---

### Task 2: EmailAccount entity + EmailAccountsPort + SqliteEmailAccounts (storage core)

**Files:**
- Modify: `src/ari/infrastructure/persistence/db.py` (`_SCHEMA`: add `user_email_accounts`)
- Create: `src/ari/domain/email/entities.py`
- Create: `src/ari/domain/email/__init__.py` (empty)
- Create: `src/ari/domain/email/email_accounts_port.py`
- Create: `src/ari/infrastructure/email/__init__.py` (empty)
- Create: `src/ari/infrastructure/email/sqlite_email_accounts.py`
- Test: `tests/infrastructure/test_sqlite_email_accounts.py`

**Interfaces:**
- Consumes: `SecretCipher` (Task 1).
- Produces:
  - `EmailAccount` dataclass: `user_id: str, label: str, imap_host: str, imap_port: int, imap_secure: bool, smtp_host: str, smtp_port: int, smtp_secure: bool, email_user: str, password: str`.
  - `EmailAccountSummary` dataclass: `label: str, address: str` (address pre-masked).
  - `mask_address(addr: str) -> str` (e.g. `juan@gmail.com` → `ju***@gmail.com`).
  - `EmailAccountsPort` protocol: `add(account)`, `list_for_user(user_id) -> list[EmailAccount]` (decrypts — needs cipher), `summaries_for(user_id) -> list[EmailAccountSummary]` (never decrypts), `remove(user_id, label) -> bool`, `delete_for_user(user_id) -> int`.
  - `SqliteEmailAccounts(conn, cipher: SecretCipher | None)`. `list_for_user`/`add` raise `RuntimeError` if `cipher is None`.

- [ ] **Step 1: Add the table to the schema**

Insert into `_SCHEMA` in `src/ari/infrastructure/persistence/db.py` (after the `facts_history` block):

```sql
CREATE TABLE IF NOT EXISTS user_email_accounts (
  user_id     TEXT    NOT NULL,
  label       TEXT    NOT NULL,
  imap_host   TEXT    NOT NULL,
  imap_port   INTEGER NOT NULL DEFAULT 993,
  imap_secure INTEGER NOT NULL DEFAULT 1,
  smtp_host   TEXT    NOT NULL,
  smtp_port   INTEGER NOT NULL DEFAULT 465,
  smtp_secure INTEGER NOT NULL DEFAULT 1,
  email_user  TEXT    NOT NULL,
  pass_enc    TEXT    NOT NULL,
  created_at  TEXT    NOT NULL,
  PRIMARY KEY (user_id, label));
```

- [ ] **Step 2: Write the failing test**

```python
# tests/infrastructure/test_sqlite_email_accounts.py
import pytest
from cryptography.fernet import Fernet

from ari.domain.email.entities import EmailAccount
from ari.infrastructure.crypto.fernet_cipher import FernetCipher
from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
from ari.infrastructure.persistence.db import connect


def _acct(user_id="7", label="trabajo", password="s3cret"):
    return EmailAccount(user_id, label, "imap.x.com", 993, True,
                        "smtp.x.com", 465, True, "juan@x.com", password)


@pytest.fixture
async def cipher():
    return FernetCipher(Fernet.generate_key().decode())


@pytest.fixture
async def store(cipher):
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteEmailAccounts(conn, cipher)
    await conn.close()


async def test_add_and_list_round_trip(store):
    await store.add(_acct())
    got = await store.list_for_user("7")
    assert [(a.label, a.email_user, a.password) for a in got] == [("trabajo", "juan@x.com", "s3cret")]


async def test_password_is_ciphertext_at_rest(store, cipher):
    await store.add(_acct(password="plain-pw"))
    rows = await store._conn.execute_fetchall(
        "SELECT pass_enc FROM user_email_accounts")
    assert rows[0]["pass_enc"] != "plain-pw"
    assert cipher.decrypt(rows[0]["pass_enc"]) == "plain-pw"


async def test_isolation_between_users(store):
    await store.add(_acct(user_id="7", label="a"))
    await store.add(_acct(user_id="9", label="b"))
    assert [a.label for a in await store.list_for_user("7")] == ["a"]


async def test_summaries_mask_and_need_no_cipher(store):
    await store.add(_acct())
    conn = store._conn
    cipherless = SqliteEmailAccounts(conn, None)
    sums = await cipherless.summaries_for("7")
    assert [(s.label, s.address) for s in sums] == [("trabajo", "ju***@x.com")]


async def test_remove_and_delete_for_user(store):
    await store.add(_acct(label="a"))
    await store.add(_acct(label="b"))
    assert await store.remove("7", "a") is True
    assert await store.remove("7", "nope") is False
    assert await store.delete_for_user("7") == 1


async def test_list_without_cipher_raises(store):
    await store.add(_acct())
    with pytest.raises(RuntimeError):
        await SqliteEmailAccounts(store._conn, None).list_for_user("7")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_sqlite_email_accounts.py -v`
Expected: FAIL (ModuleNotFoundError: ari.domain.email.entities)

- [ ] **Step 4: Write the entities, port, and adapter**

```python
# src/ari/domain/email/entities.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailAccount:
    user_id: str
    label: str
    imap_host: str
    imap_port: int
    imap_secure: bool
    smtp_host: str
    smtp_port: int
    smtp_secure: bool
    email_user: str
    password: str


@dataclass(frozen=True, slots=True)
class EmailAccountSummary:
    label: str
    address: str


def mask_address(addr: str) -> str:
    local, _, domain = addr.partition("@")
    shown = local[:2] if len(local) > 2 else local[:1]
    masked = f"{shown}***"
    return f"{masked}@{domain}" if domain else masked
```

```python
# src/ari/domain/email/email_accounts_port.py
from typing import Protocol

from ari.domain.email.entities import EmailAccount, EmailAccountSummary


class EmailAccountsPort(Protocol):
    async def add(self, account: EmailAccount) -> None: ...
    async def list_for_user(self, user_id: str) -> list[EmailAccount]: ...
    async def summaries_for(self, user_id: str) -> list[EmailAccountSummary]: ...
    async def remove(self, user_id: str, label: str) -> bool: ...
    async def delete_for_user(self, user_id: str) -> int: ...
```

```python
# src/ari/infrastructure/email/sqlite_email_accounts.py
import asyncio
from datetime import datetime, timezone

import aiosqlite

from ari.domain.crypto.secret_cipher import SecretCipher
from ari.domain.email.entities import EmailAccount, EmailAccountSummary, mask_address

_COLS = ("imap_host, imap_port, imap_secure, smtp_host, smtp_port, smtp_secure, "
         "email_user, pass_enc")


class SqliteEmailAccounts:
    """EmailAccountsPort over user_email_accounts. The cipher is required only to
    read/write the password; masked summaries and removal work without it (the
    MCP server process has no vault key)."""

    def __init__(self, conn: aiosqlite.Connection, cipher: SecretCipher | None):
        self._conn = conn
        self._cipher = cipher
        self._lock = asyncio.Lock()

    def _require_cipher(self) -> SecretCipher:
        if self._cipher is None:
            raise RuntimeError("email cipher unavailable (ARI_VAULT_KEY missing)")
        return self._cipher

    async def add(self, account: EmailAccount) -> None:
        pass_enc = self._require_cipher().encrypt(account.password)
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO user_email_accounts (user_id, label, imap_host, imap_port, "
                "imap_secure, smtp_host, smtp_port, smtp_secure, email_user, pass_enc, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, label) DO UPDATE SET "
                "imap_host=excluded.imap_host, imap_port=excluded.imap_port, "
                "imap_secure=excluded.imap_secure, smtp_host=excluded.smtp_host, "
                "smtp_port=excluded.smtp_port, smtp_secure=excluded.smtp_secure, "
                "email_user=excluded.email_user, pass_enc=excluded.pass_enc",
                (account.user_id, account.label, account.imap_host, account.imap_port,
                 int(account.imap_secure), account.smtp_host, account.smtp_port,
                 int(account.smtp_secure), account.email_user, pass_enc,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            await self._conn.commit()

    async def list_for_user(self, user_id: str) -> list[EmailAccount]:
        cipher = self._require_cipher()
        rows = await self._conn.execute_fetchall(
            f"SELECT label, {_COLS} FROM user_email_accounts WHERE user_id = ? "
            "ORDER BY label", (user_id,))
        return [EmailAccount(
            user_id, r["label"], r["imap_host"], r["imap_port"], bool(r["imap_secure"]),
            r["smtp_host"], r["smtp_port"], bool(r["smtp_secure"]), r["email_user"],
            cipher.decrypt(r["pass_enc"])) for r in rows]

    async def summaries_for(self, user_id: str) -> list[EmailAccountSummary]:
        rows = await self._conn.execute_fetchall(
            "SELECT label, email_user FROM user_email_accounts WHERE user_id = ? "
            "ORDER BY label", (user_id,))
        return [EmailAccountSummary(r["label"], mask_address(r["email_user"])) for r in rows]

    async def remove(self, user_id: str, label: str) -> bool:
        async with self._lock:
            cur = await self._conn.execute(
                "DELETE FROM user_email_accounts WHERE user_id = ? AND label = ?",
                (user_id, label))
            await self._conn.commit()
            return cur.rowcount > 0

    async def delete_for_user(self, user_id: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "DELETE FROM user_email_accounts WHERE user_id = ?", (user_id,))
            await self._conn.commit()
            return cur.rowcount
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_sqlite_email_accounts.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ari/domain/email src/ari/infrastructure/email/__init__.py src/ari/infrastructure/email/sqlite_email_accounts.py src/ari/infrastructure/persistence/db.py tests/infrastructure/test_sqlite_email_accounts.py
git commit -m "feat(email): user_email_accounts table, entity, port, and adapter"
```

---

### Task 3: email_server_spec (runtime MCP builder) + label validation

**Files:**
- Create: `src/ari/infrastructure/email/server_spec.py`
- Test: `tests/infrastructure/test_email_server_spec.py`

**Interfaces:**
- Consumes: `EmailAccount` (Task 2).
- Produces:
  - `LABEL_RE` and `valid_label(label: str) -> bool` (slug: `^[a-z0-9][a-z0-9_-]{0,30}$`).
  - `server_name(label: str) -> str` returning `f"mail_{label}"`.
  - `email_server_spec(account: EmailAccount) -> dict` returning the `mcp-mail-server@2.1.0` CLI dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_email_server_spec.py
from ari.domain.email.entities import EmailAccount
from ari.infrastructure.email.server_spec import (
    email_server_spec, server_name, valid_label,
)


def _acct():
    return EmailAccount("7", "trabajo", "imap.x.com", 993, True,
                        "smtp.x.com", 587, False, "juan@x.com", "pw")


def test_server_name_is_prefixed():
    assert server_name("trabajo") == "mail_trabajo"


def test_valid_label_rejects_unsafe():
    assert valid_label("trabajo")
    assert not valid_label("../x")
    assert not valid_label("Corp")   # uppercase
    assert not valid_label("")


def test_email_server_spec_builds_cli_env():
    spec = email_server_spec(_acct())
    assert spec["command"] == "npx"
    assert spec["args"] == ["-y", "mcp-mail-server@2.1.0"]
    assert spec["env"] == {
        "IMAP_HOST": "imap.x.com", "IMAP_PORT": "993", "IMAP_SECURE": "true",
        "SMTP_HOST": "smtp.x.com", "SMTP_PORT": "587", "SMTP_SECURE": "false",
        "EMAIL_USER": "juan@x.com", "EMAIL_PASS": "pw",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_email_server_spec.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write the implementation**

```python
# src/ari/infrastructure/email/server_spec.py
import re

from ari.domain.email.entities import EmailAccount

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
MAIL_PACKAGE = "mcp-mail-server@2.1.0"


def valid_label(label: str) -> bool:
    return bool(LABEL_RE.match(label or ""))


def server_name(label: str) -> str:
    return f"mail_{label}"


def _b(flag: bool) -> str:
    return "true" if flag else "false"


def email_server_spec(account: EmailAccount) -> dict:
    return {
        "command": "npx",
        "args": ["-y", MAIL_PACKAGE],
        "env": {
            "IMAP_HOST": account.imap_host, "IMAP_PORT": str(account.imap_port),
            "IMAP_SECURE": _b(account.imap_secure),
            "SMTP_HOST": account.smtp_host, "SMTP_PORT": str(account.smtp_port),
            "SMTP_SECURE": _b(account.smtp_secure),
            "EMAIL_USER": account.email_user, "EMAIL_PASS": account.password,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_email_server_spec.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/email/server_spec.py tests/infrastructure/test_email_server_spec.py
git commit -m "feat(email): email_server_spec builder and label validation"
```

---

### Task 4: AriSealedBox (X25519 key pair in the vault + open)

**Files:**
- Create: `src/ari/infrastructure/email/sealed_box.py`
- Test: `tests/infrastructure/test_sealed_box.py`

**Interfaces:**
- Consumes: the vault (`SecretVault`: `get`/`set`), PyNaCl.
- Produces: `AriSealedBox(vault, key_name="ARI_SEALEDBOX_SK")` with `public_key_b64() -> str` (mints + persists the secret key on first use) and `open(sealed_b64: str) -> bytes` (raises `ValueError` on a bad blob). `available() -> bool` (False when the vault cannot store, i.e. no key).

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_sealed_box.py
import base64

import pytest
from nacl.public import PublicKey, SealedBox

from ari.infrastructure.email.sealed_box import AriSealedBox


class FakeVault:
    def __init__(self, writable=True):
        self._d, self._writable = {}, writable
    def get(self, name): return self._d.get(name)
    def set(self, name, value):
        if not self._writable:
            raise RuntimeError("no key")
        self._d[name] = value


def test_round_trip_and_key_is_persisted():
    box = AriSealedBox(FakeVault())
    pub = base64.b64decode(box.public_key_b64())
    sealed = SealedBox(PublicKey(pub)).encrypt(b"hello")
    assert box.open(base64.b64encode(sealed).decode()) == b"hello"
    # second instance on the SAME vault reuses the stored key
    box2 = AriSealedBox(box._vault)
    assert box2.public_key_b64() == box.public_key_b64()


def test_bad_blob_raises_value_error():
    box = AriSealedBox(FakeVault())
    box.public_key_b64()
    with pytest.raises(ValueError):
        box.open("not-base64-!!")


def test_unavailable_when_vault_cannot_store():
    box = AriSealedBox(FakeVault(writable=False))
    assert box.available() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_sealed_box.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write the implementation**

```python
# src/ari/infrastructure/email/sealed_box.py
import base64
import binascii

from nacl.public import PrivateKey, PublicKey, SealedBox

_KEY_NAME = "ARI_SEALEDBOX_SK"


class AriSealedBox:
    """Ari's long-lived X25519 key pair. The secret key is stored in the vault;
    the public key is handed to the enroll HTML. Opens sealed blobs clients make."""

    def __init__(self, vault, key_name: str = _KEY_NAME):
        self._vault = vault
        self._key_name = key_name

    def _secret_key(self) -> PrivateKey:
        stored = self._vault.get(self._key_name)
        if stored:
            return PrivateKey(base64.b64decode(stored))
        sk = PrivateKey.generate()
        self._vault.set(self._key_name, base64.b64encode(bytes(sk)).decode())
        return sk

    def available(self) -> bool:
        try:
            self._secret_key()
            return True
        except Exception:
            return False

    def public_key_b64(self) -> str:
        return base64.b64encode(bytes(self._secret_key().public_key)).decode()

    def open(self, sealed_b64: str) -> bytes:
        try:
            raw = base64.b64decode(sealed_b64, validate=True)
            return SealedBox(self._secret_key()).decrypt(raw)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("blob ilegible") from exc
        except Exception as exc:  # nacl.exceptions.CryptoError and friends
            raise ValueError("no pude descifrar el blob") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_sealed_box.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/email/sealed_box.py tests/infrastructure/test_sealed_box.py
git commit -m "feat(email): AriSealedBox X25519 keypair stored in the vault"
```

---

### Task 5: Enroll HTML form (vendored libsodium.js + provider presets)

**Files:**
- Create: `src/ari/infrastructure/email/assets/sodium.js` (vendored)
- Create: `src/ari/infrastructure/email/assets/__init__.py` (empty)
- Create: `src/ari/infrastructure/email/account_form.py`
- Test: `tests/infrastructure/test_account_form.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure render).
- Produces:
  - `PROVIDERS: dict[str, dict]` — presets `gmail`, `outlook`, `corp`, `custom` with imap/smtp host/port/secure.
  - `render_enroll_html(public_key_b64: str) -> str` — a self-contained HTML string that inlines `sodium.js`, embeds the public key, renders the form, and on submit outputs `ari-mail:v1:<base64-ORIGINAL>`.

- [ ] **Step 1: Vendor libsodium.js**

Download the standard browser build (it includes `crypto_box_seal`) and save it verbatim to `src/ari/infrastructure/email/assets/sodium.js`:

Run: `curl -fsSL https://cdn.jsdelivr.net/npm/libsodium-wrappers@0.7.15/dist/browsers/sodium.js -o src/ari/infrastructure/email/assets/sodium.js`
Expected: a non-empty JS file; verify `grep -c crypto_box_seal src/ari/infrastructure/email/assets/sodium.js` is ≥ 1.

- [ ] **Step 2: Write the failing test**

```python
# tests/infrastructure/test_account_form.py
from ari.infrastructure.email.account_form import PROVIDERS, render_enroll_html


def test_presets_cover_the_three_known_providers():
    assert {"gmail", "outlook", "corp", "custom"} <= set(PROVIDERS)
    assert PROVIDERS["gmail"]["imap_host"] == "imap.gmail.com"


def test_html_embeds_key_form_and_sodium():
    html = render_enroll_html("PUBKEYB64==")
    assert "PUBKEYB64==" in html
    assert "crypto_box_seal" in html          # inlined sodium is present
    assert "ari-mail:v1:" in html             # output prefix
    assert 'name="password"' in html
    assert "base64_variants.ORIGINAL" in html  # pinned variant (see Review Focus)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_account_form.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Write the implementation**

```python
# src/ari/infrastructure/email/account_form.py
import importlib.resources as res

PROVIDERS = {
    "gmail":   {"imap_host": "imap.gmail.com", "imap_port": 993, "imap_secure": True,
                "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_secure": True},
    "outlook": {"imap_host": "outlook.office365.com", "imap_port": 993, "imap_secure": True,
                "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_secure": False},
    "corp":    {"imap_host": "", "imap_port": 993, "imap_secure": True,
                "smtp_host": "", "smtp_port": 465, "smtp_secure": True},
    "custom":  {"imap_host": "", "imap_port": 993, "imap_secure": True,
                "smtp_host": "", "smtp_port": 465, "smtp_secure": True},
}


def _sodium_js() -> str:
    return res.files("ari.infrastructure.email.assets").joinpath("sodium.js").read_text()


def render_enroll_html(public_key_b64: str) -> str:
    import json
    presets = json.dumps(PROVIDERS)
    sodium = _sodium_js()
    # The submit handler builds the account JSON, seals it to Ari's public key,
    # and shows `ari-mail:v1:<base64 ORIGINAL>`. All client-side, no network.
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conectar correo a Ari</title></head><body>
<h1>Conectá tu correo</h1>
<p>Esto se cifra en tu dispositivo. Pegale el código a Ari cuando termines.</p>
<form id="f">
  <label>Proveedor <select id="provider"></select></label>
  <label>Etiqueta (ej. trabajo) <input name="label" required></label>
  <label>IMAP host <input name="imap_host" required></label>
  <label>IMAP puerto <input name="imap_port" type="number" value="993"></label>
  <label>SMTP host <input name="smtp_host" required></label>
  <label>SMTP puerto <input name="smtp_port" type="number" value="465"></label>
  <label>Correo / usuario <input name="email_user" required></label>
  <label>Contraseña (o App Password) <input name="password" type="password" required></label>
  <button type="submit">Generar código</button>
</form>
<textarea id="out" readonly rows="4" style="width:100%"></textarea>
<button id="copy" type="button">Copiar</button>
<script>{sodium}</script>
<script>
const PUB = "{public_key_b64}";
const PRESETS = {presets};
const sel = document.getElementById('provider');
Object.keys(PRESETS).forEach(k => {{ const o=document.createElement('option'); o.value=k; o.textContent=k; sel.appendChild(o); }});
function applyPreset() {{ const p = PRESETS[sel.value]; const f = document.getElementById('f');
  for (const k of ['imap_host','imap_port','smtp_host','smtp_port']) if (p[k] !== "") f[k].value = p[k]; }}
sel.addEventListener('change', applyPreset);
document.getElementById('f').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await sodium.ready;
  const f = e.target;
  const acct = {{
    label: f.label.value.trim().toLowerCase(),
    imap_host: f.imap_host.value.trim(), imap_port: parseInt(f.imap_port.value, 10),
    imap_secure: parseInt(f.imap_port.value,10) !== 143,
    smtp_host: f.smtp_host.value.trim(), smtp_port: parseInt(f.smtp_port.value, 10),
    smtp_secure: parseInt(f.smtp_port.value,10) === 465,
    email_user: f.email_user.value.trim(), password: f.password.value,
  }};
  const pub = sodium.from_base64(PUB, sodium.base64_variants.ORIGINAL);
  const sealed = sodium.crypto_box_seal(JSON.stringify(acct), pub);
  document.getElementById('out').value = "ari-mail:v1:" + sodium.to_base64(sealed, sodium.base64_variants.ORIGINAL);
}});
document.getElementById('copy').addEventListener('click', () => {{
  const o = document.getElementById('out'); o.select(); document.execCommand('copy'); }});
</script></body></html>"""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_account_form.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ari/infrastructure/email/assets src/ari/infrastructure/email/account_form.py tests/infrastructure/test_account_form.py
git commit -m "feat(email): offline enroll HTML with vendored libsodium and presets"
```

---

### Task 6: ConnectEmailAccount use-case (decrypt blob → store)

**Files:**
- Create: `src/ari/application/email/__init__.py` (empty)
- Create: `src/ari/application/email/connect_email_account.py`
- Test: `tests/application/test_connect_email_account.py`

**Interfaces:**
- Consumes: `AriSealedBox` (Task 4), `EmailAccountsPort` (Task 2), `valid_label` (Task 3).
- Produces:
  - `EMAIL_BLOB_PREFIX = "ari-mail:v1:"` and `is_email_blob(text: str) -> bool`.
  - `ConnectEmailAccount(sealed_box, accounts)` with `__call__(user_id: str, blob: str) -> str` returning the stored label. Raises `ValueError` with a user-facing Spanish message on any failure (bad blob, bad JSON, bad label, missing fields). Stores nothing on failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_connect_email_account.py
import base64
import json

import pytest
from cryptography.fernet import Fernet
from nacl.public import PublicKey, SealedBox

from ari.application.email.connect_email_account import (
    ConnectEmailAccount, is_email_blob,
)
from ari.infrastructure.crypto.fernet_cipher import FernetCipher
from ari.infrastructure.email.sealed_box import AriSealedBox
from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
from ari.infrastructure.persistence.db import connect


class FakeVault:
    def __init__(self): self._d = {}
    def get(self, n): return self._d.get(n)
    def set(self, n, v): self._d[n] = v


def _blob(box, payload):
    pub = base64.b64decode(box.public_key_b64())
    sealed = SealedBox(PublicKey(pub)).encrypt(json.dumps(payload).encode())
    return "ari-mail:v1:" + base64.b64encode(sealed).decode()


def _payload(label="trabajo"):
    return {"label": label, "imap_host": "imap.x.com", "imap_port": 993,
            "imap_secure": True, "smtp_host": "smtp.x.com", "smtp_port": 465,
            "smtp_secure": True, "email_user": "juan@x.com", "password": "pw"}


@pytest.fixture
async def ctx():
    conn = await connect(":memory:", embedding_dim=4)
    box = AriSealedBox(FakeVault())
    box.public_key_b64()  # mint
    accounts = SqliteEmailAccounts(conn, FernetCipher(Fernet.generate_key().decode()))
    yield box, accounts, conn
    await conn.close()


def test_is_email_blob():
    assert is_email_blob("ari-mail:v1:abc")
    assert not is_email_blob("hola")


async def test_valid_blob_stored_under_actor(ctx):
    box, accounts, _ = ctx
    use = ConnectEmailAccount(box, accounts)
    label = await use("7", _blob(box, _payload()))
    assert label == "trabajo"
    assert [a.email_user for a in await accounts.list_for_user("7")] == ["juan@x.com"]


async def test_bad_label_rejected_stores_nothing(ctx):
    box, accounts, _ = ctx
    use = ConnectEmailAccount(box, accounts)
    with pytest.raises(ValueError):
        await use("7", _blob(box, _payload(label="../evil")))
    assert await accounts.list_for_user("7") == []


async def test_garbage_blob_rejected(ctx):
    box, accounts, _ = ctx
    use = ConnectEmailAccount(box, accounts)
    with pytest.raises(ValueError):
        await use("7", "ari-mail:v1:not-base64-!!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_connect_email_account.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write the implementation**

```python
# src/ari/application/email/connect_email_account.py
import json

from ari.domain.email.entities import EmailAccount
from ari.infrastructure.email.server_spec import valid_label

EMAIL_BLOB_PREFIX = "ari-mail:v1:"
_FIELDS = ("imap_host", "imap_port", "smtp_host", "smtp_port", "email_user", "password")


def is_email_blob(text: str) -> bool:
    return text.strip().startswith(EMAIL_BLOB_PREFIX)


class ConnectEmailAccount:
    def __init__(self, sealed_box, accounts):
        self._box, self._accounts = sealed_box, accounts

    async def __call__(self, user_id: str, blob: str) -> str:
        body = blob.strip()[len(EMAIL_BLOB_PREFIX):]
        plain = self._box.open(body)  # raises ValueError on a bad blob
        try:
            data = json.loads(plain)
        except json.JSONDecodeError as exc:
            raise ValueError("el código no tenía un formato válido") from exc
        label = str(data.get("label", "")).strip().lower()
        if not valid_label(label):
            raise ValueError("la etiqueta debe ser corta, sin espacios ni símbolos raros")
        if any(not data.get(f) for f in _FIELDS):
            raise ValueError("faltan datos de la casilla (host, puerto, usuario o contraseña)")
        account = EmailAccount(
            user_id, label, data["imap_host"], int(data["imap_port"]),
            bool(data.get("imap_secure", True)), data["smtp_host"], int(data["smtp_port"]),
            bool(data.get("smtp_secure", True)), data["email_user"], data["password"])
        await self._accounts.add(account)
        return label
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_connect_email_account.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/email tests/application/test_connect_email_account.py
git commit -m "feat(email): ConnectEmailAccount decrypts the sealed blob and stores it"
```

---

### Task 7: Enroll bridge (queue + runner that sends the HTML document)

**Files:**
- Modify: `src/ari/infrastructure/persistence/db.py` (`_SCHEMA`: add `email_enroll_requests`)
- Create: `src/ari/infrastructure/persistence/sqlite_email_enroll_requests.py`
- Create: `src/ari/application/email/enroll_runner.py`
- Test: `tests/infrastructure/test_sqlite_email_enroll_requests.py`
- Test: `tests/application/test_email_enroll_runner.py`

**Interfaces:**
- Consumes: `AriSealedBox` (Task 4), `render_enroll_html` (Task 5).
- Produces:
  - `EnrollRequest` dataclass (`id, user_id, chat_id`) + `SqliteEmailEnrollRequests(conn)` with `add(user_id, chat_id) -> int`, `claim_pending() -> list[EnrollRequest]`, `finish(id)`.
  - `EmailEnrollRunner(requests, sealed_box, render, send_document)` with `__call__()`: for each pending request, render the HTML and call `send_document(chat_id, filename, html_bytes)`, then finish. `send_document` signature: `async (chat_id: str, filename: str, content: bytes) -> None`.

- [ ] **Step 1: Add the queue table to the schema**

Insert into `_SCHEMA` in `src/ari/infrastructure/persistence/db.py`:

```sql
CREATE TABLE IF NOT EXISTS email_enroll_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_email_enroll_status ON email_enroll_requests(status, id);
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/infrastructure/test_sqlite_email_enroll_requests.py
import pytest

from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_email_enroll_requests import (
    SqliteEmailEnrollRequests,
)


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteEmailEnrollRequests(conn)
    await conn.close()


async def test_add_claim_once_and_finish(store):
    rid = await store.add("7", "7")
    claimed = await store.claim_pending()
    assert [(r.id, r.user_id, r.chat_id) for r in claimed] == [(rid, "7", "7")]
    assert await store.claim_pending() == []
    await store.finish(rid)
    assert await store.claim_pending() == []
```

```python
# tests/application/test_email_enroll_runner.py
import pytest

from ari.application.email.enroll_runner import EmailEnrollRunner


class FakeRequests:
    def __init__(self, pending): self._p, self.finished = pending, []
    async def claim_pending(self): p, self._p = self._p, []; return p
    async def finish(self, rid): self.finished.append(rid)


class Req:
    def __init__(self, rid, chat): self.id, self.chat_id, self.user_id = rid, chat, chat


class FakeBox:
    def public_key_b64(self): return "PUB=="


async def test_runner_renders_and_sends_document():
    reqs = FakeRequests([Req(1, "7")])
    sent = []
    async def send_document(chat_id, filename, content):
        sent.append((chat_id, filename, content))
    runner = EmailEnrollRunner(reqs, FakeBox(), lambda pub: f"<html>{pub}</html>", send_document)
    await runner()
    assert sent[0][0] == "7"
    assert sent[0][1].endswith(".html")
    assert b"PUB==" in sent[0][2]
    assert reqs.finished == [1]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/test_sqlite_email_enroll_requests.py tests/application/test_email_enroll_runner.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Write the store and the runner**

```python
# src/ari/infrastructure/persistence/sqlite_email_enroll_requests.py
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

_PENDING, _TAKEN = "pending", "taken"


@dataclass(frozen=True, slots=True)
class EnrollRequest:
    id: int
    user_id: str
    chat_id: str


class SqliteEmailEnrollRequests:
    """Queue between the Ari MCP server (writer) and the bot (claimer/sender)."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    async def add(self, user_id: str, chat_id: str) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO email_enroll_requests (user_id, chat_id, status, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, chat_id, _PENDING,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            await self._conn.commit()
            return cur.lastrowid

    async def claim_pending(self) -> list[EnrollRequest]:
        async with self._lock:
            cur = await self._conn.execute(
                "UPDATE email_enroll_requests SET status = ? WHERE status = ? "
                "RETURNING id, user_id, chat_id", (_TAKEN, _PENDING))
            rows = await cur.fetchall()
            await self._conn.commit()
        return sorted((EnrollRequest(r["id"], r["user_id"], r["chat_id"]) for r in rows),
                      key=lambda r: r.id)

    async def finish(self, request_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM email_enroll_requests WHERE id = ?", (request_id,))
            await self._conn.commit()
```

```python
# src/ari/application/email/enroll_runner.py
import logging

log = logging.getLogger("ari.email")


class EmailEnrollRunner:
    """Claims enroll requests and sends each user the offline enroll HTML as a
    Telegram document (the MCP server can't send; the bot can)."""

    def __init__(self, requests, sealed_box, render, send_document):
        self._requests, self._box = requests, sealed_box
        self._render, self._send_document = render, send_document

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            try:
                html = self._render(self._box.public_key_b64())
                await self._send_document(req.chat_id, "conectar-correo.html",
                                          html.encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("enroll send failed for %s: %s", req.chat_id, exc)
            await self._requests.finish(req.id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/test_sqlite_email_enroll_requests.py tests/application/test_email_enroll_runner.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ari/infrastructure/persistence/db.py src/ari/infrastructure/persistence/sqlite_email_enroll_requests.py src/ari/application/email/enroll_runner.py tests/infrastructure/test_sqlite_email_enroll_requests.py tests/application/test_email_enroll_runner.py
git commit -m "feat(email): enroll request queue and runner that sends the HTML document"
```

---

### Task 8: Ari tools (conectar_correo / mis_correos / olvidar_correo) + registry

**Files:**
- Modify: `src/ari/application/ari_tools.py` (constructor dep `email_accounts`, `email_enroll`; 3 tool methods)
- Modify: `src/ari/domain/tools/ari_permissions.py` (add 3 tools to `ARI_TOOLS` and `_USER_CHAT`)
- Modify: `src/ari/application/schedule/schedule_actions.py` (`_INJECTION_RULE` adds the 3 tools)
- Modify: `src/ari/mcp_server/server.py` (3 `@tool` wrappers)
- Modify: `src/ari/mcp_server/__main__.py` (`_get_tools`: build `SqliteEmailAccounts(conn, None)` + `SqliteEmailEnrollRequests(conn)`, pass to `AriTools`; add email delete to `_on_revoke`)
- Test: `tests/application/test_ari_tools_email.py`

**Interfaces:**
- Consumes: `EmailAccountsPort.summaries_for/remove` (Task 2), the enroll queue `add` (Task 7).
- Produces: `AriTools.conectar_correo() -> str`, `AriTools.mis_correos() -> str`, `AriTools.olvidar_correo(label) -> str`. New kwargs on `AriTools.__init__`: `email_accounts=None`, `email_enroll=None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_ari_tools_email.py
import pytest
from zoneinfo import ZoneInfo
from datetime import UTC, datetime

from ari.application.ari_tools import AriTools
from ari.domain.agent.actor import Actor  # existing actor type
from ari.domain.email.entities import EmailAccount
from ari.infrastructure.crypto.fernet_cipher import FernetCipher
from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_email_enroll_requests import (
    SqliteEmailEnrollRequests,
)
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from cryptography.fernet import Fernet


def _actor(context="chat", is_owner=False):
    return Actor(user_id="7", chat_id="7", name="Juan", is_owner=is_owner,
                 context=context, turn_id="t1")


@pytest.fixture
async def tools():
    conn = await connect(":memory:", embedding_dim=4)
    accounts = SqliteEmailAccounts(conn, FernetCipher(Fernet.generate_key().decode()))
    enroll = SqliteEmailEnrollRequests(conn)
    t = AriTools(_actor(), schedule=None, memory=None, turn_log=SqliteTurnLog(conn),
                 tz=ZoneInfo("UTC"), max_items=50, clock=lambda: datetime.now(UTC),
                 email_accounts=accounts, email_enroll=enroll)
    yield t, accounts, enroll
    await conn.close()


async def test_conectar_correo_enqueues(tools):
    t, _, enroll = tools
    out = await t.conectar_correo()
    assert "correo" in out.lower()
    assert len(await enroll.claim_pending()) == 1


async def test_mis_correos_masks_address(tools):
    t, accounts, _ = tools
    await accounts.add(EmailAccount("7", "trabajo", "h", 993, True, "h", 465, True,
                                    "juan@x.com", "pw"))
    out = await t.mis_correos()
    assert "trabajo" in out and "ju***@x.com" in out
    assert "pw" not in out


async def test_olvidar_correo_removes(tools):
    t, accounts, _ = tools
    await accounts.add(EmailAccount("7", "trabajo", "h", 993, True, "h", 465, True,
                                    "juan@x.com", "pw"))
    out = await t.olvidar_correo("trabajo")
    assert "trabajo" in out
    assert await accounts.summaries_for("7") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_ari_tools_email.py -v`
Expected: FAIL (TypeError: unexpected kwarg email_accounts)

- [ ] **Step 3: Extend the constructor and add the tool methods**

In `AriTools.__init__`, add params `email_accounts=None, email_enroll=None` and store:

```python
        self._email_accounts = email_accounts  # EmailAccountsPort | None
        self._email_enroll = email_enroll      # SqliteEmailEnrollRequests | None
```

Add the methods (near the credentials section):

```python
    # ---- per-user email ---------------------------------------------------

    async def conectar_correo(self) -> str:
        if not self._allowed("conectar_correo"):
            return DENIED
        if self._email_enroll is None:
            return "No puedo conectar correo ahora mismo."
        await self._email_enroll.add(self._a.user_id, self._a.chat_id)
        return await self._receipt(
            "📧 Te mando un archivo para conectar tu correo. Abrilo, cargá tus datos "
            "y pegame acá el código que te genera.")

    async def mis_correos(self) -> str:
        if not self._allowed("mis_correos"):
            return DENIED
        if self._email_accounts is None:
            return "No puedo ver tus correos ahora mismo."
        sums = await self._email_accounts.summaries_for(self._a.user_id)
        if not sums:
            return "No tenés casillas conectadas. Decime «conectá mi correo» para agregar una."
        return "\n".join(f"📧 {s.label}: {s.address}" for s in sums)

    async def olvidar_correo(self, label: str) -> str:
        if not self._allowed("olvidar_correo"):
            return DENIED
        if self._email_accounts is None:
            return "No puedo borrar correos ahora mismo."
        name = (label or "").strip().lower()
        removed = await self._email_accounts.remove(self._a.user_id, name)
        if not removed:
            return f"No encontré una casilla «{name}»."
        return await self._receipt(f"🗑️ Desconecté la casilla «{name}».")
```

- [ ] **Step 4: Register the tools (permissions + injection rule)**

In `src/ari/domain/tools/ari_permissions.py`: add `"conectar_correo", "mis_correos", "olvidar_correo"` to `ARI_TOOLS`, and add the same three to the `_USER_CHAT` set (every user manages their own mailboxes).

In `src/ari/application/schedule/schedule_actions.py`: append `conectar_correo` and `olvidar_correo` to the tool list named in `_INJECTION_RULE` (they are sensitive — only on the user's own request).

- [ ] **Step 5: Expose the tools on the MCP server**

In `src/ari/mcp_server/server.py`, add (mirroring `pedir_credenciales`):

```python
    @tool("conectar_correo")
    async def conectar_correo() -> str:
        """Conecta una casilla de correo del usuario (IMAP/SMTP). Te mando un archivo
        para cargar los datos de forma segura; el usuario pega de vuelta un código."""
        return await (await get_tools()).conectar_correo()

    @tool("mis_correos")
    async def mis_correos() -> str:
        """Lista las casillas de correo que el usuario tiene conectadas (sin mostrar
        la contraseña)."""
        return await (await get_tools()).mis_correos()

    @tool("olvidar_correo")
    async def olvidar_correo(label: str) -> str:
        """Desconecta una casilla de correo del usuario por su etiqueta."""
        return await (await get_tools()).olvidar_correo(label)
```

In `src/ari/mcp_server/__main__.py` `_get_tools`, build the stores (cipher-less — no vault key here) and pass them:

```python
        from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
        from ari.infrastructure.persistence.sqlite_email_enroll_requests import (
            SqliteEmailEnrollRequests,
        )
        email_accounts = SqliteEmailAccounts(conn, None)
        email_enroll = SqliteEmailEnrollRequests(conn)
```

Add `email_accounts=email_accounts, email_enroll=email_enroll` to the `AriTools(...)` call, and add to `_on_revoke`:

```python
            await email_accounts.delete_for_user(user_id)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_ari_tools_email.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Verify the actor constructor shape**

If `Actor` import path or fields differ from the test above, adjust the test's `_actor()` to match the real `Actor` (check `ari/application/ari_tools.py` imports). Re-run Step 6.

- [ ] **Step 8: Commit**

```bash
git add src/ari/application/ari_tools.py src/ari/domain/tools/ari_permissions.py src/ari/application/schedule/schedule_actions.py src/ari/mcp_server/server.py src/ari/mcp_server/__main__.py tests/application/test_ari_tools_email.py
git commit -m "feat(email): conectar_correo/mis_correos/olvidar_correo tools + registry"
```

---

### Task 9: Inject per-user mailboxes at the turn seam

**Files:**
- Modify: `src/ari/application/tools/tool_policy.py` (`__init__` gains `email_accounts=None`; `turn` injects `mail_<label>`; `view` lists mailboxes)
- Test: `tests/application/test_tool_policy_email.py`

**Interfaces:**
- Consumes: `EmailAccountsPort.list_for_user` (Task 2), `email_server_spec`/`server_name` (Task 3).
- Produces: `ToolPolicy(..., email_accounts=None)`; `turn` adds `servers["mail_<label>"]` for the acting user before writing the per-turn config; `view` appends one line per mailbox (masked).

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_tool_policy_email.py
import json
import pytest

from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy
from ari.domain.email.entities import EmailAccount


class FakeRegistry:
    def servers_for(self, is_owner): return ((), None)
    def resolved(self, is_owner): return {}
    def descriptions(self, is_owner): return []
    def degraded_for(self, is_owner): return []
    def status(self): return []


class FakeWriter:
    def __init__(self): self.written = None
    def write(self, servers): self.written = servers; return "/tmp/turn.json"
    def remove(self, path): pass


class FakeAccounts:
    def __init__(self, by_user): self._by = by_user
    async def list_for_user(self, user_id): return self._by.get(user_id, [])
    async def summaries_for(self, user_id): return []


def _acct(user_id, label):
    return EmailAccount(user_id, label, "imap.x.com", 993, True, "smtp.x.com", 465, True,
                        f"{label}@x.com", "pw")


@pytest.fixture
def writer():
    return FakeWriter()


async def test_turn_injects_only_acting_users_mailboxes(writer):
    accounts = FakeAccounts({"7": [_acct("7", "trabajo")], "9": [_acct("9", "otra")]})
    policy = ToolPolicy(FakeRegistry(), lambda uid: False,
                        ari=AriServerSpec("py", ("-m", "x")), writer=writer,
                        email_accounts=accounts)
    async with policy.turn("7") as turn:
        assert "mail_trabajo" in writer.written
        assert "mail_otra" not in writer.written
        assert writer.written["mail_trabajo"]["env"]["EMAIL_USER"] == "trabajo@x.com"
        assert "mcp__mail_trabajo" in turn.toolset.allowed_tools


async def test_turn_without_email_accounts_is_unchanged(writer):
    policy = ToolPolicy(FakeRegistry(), lambda uid: False,
                        ari=AriServerSpec("py", ("-m", "x")), writer=writer)
    async with policy.turn("7"):
        assert not any(k.startswith("mail_") for k in writer.written)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_tool_policy_email.py -v`
Expected: FAIL (TypeError: unexpected kwarg email_accounts)

- [ ] **Step 3: Extend ToolPolicy**

In `__init__`, add `email_accounts=None` and store `self._email_accounts = email_accounts`.

Add a helper and call it inside `turn`, right after `servers = self._registry.resolved(owner)` and before `self._ari`/`ARI_SERVER` is added (so the allowlist comprehension that follows picks the `mail_*` names up automatically):

```python
    async def _inject_email(self, servers: dict, user_id: str) -> None:
        if self._email_accounts is None:
            return
        from ari.infrastructure.email.server_spec import email_server_spec, server_name
        try:
            accounts = await self._email_accounts.list_for_user(user_id)
        except Exception:  # no cipher / unreadable: degrade to no mailboxes
            return
        for acct in accounts:
            servers[server_name(acct.label)] = email_server_spec(acct)
```

In `turn`, after `servers = self._registry.resolved(owner)`:

```python
        await self._inject_email(servers, user_id)
```

In `view`, after the server lines are built, append the acting user's mailboxes:

```python
        if self._email_accounts is not None:
            try:
                for s in await self._email_accounts.summaries_for(user_id):
                    lines.append(f"- 📧 mail_{s.label}: tu casilla {s.label} ({s.address})")
            except Exception:
                pass
```

(Note: `view` is currently sync. Make `view` async and `await` it in `turn`/callers, OR add a separate `async def view_lines`. Prefer making `view` async and updating its callers — check `turn` and `test_tool_policy.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_tool_policy_email.py tests/application/test_tool_policy.py tests/application/test_tool_policy_turn.py -v`
Expected: PASS (new tests pass; existing tool-policy tests still pass after the `view` async change)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/tools/tool_policy.py tests/application/test_tool_policy_email.py
git commit -m "feat(email): inject per-user mailboxes into the per-turn MCP config"
```

---

### Task 10: Gateway wiring — blob interception, document send, runner, revoke

**Files:**
- Modify: `src/ari/main.py` (build sealed box + cipher + email stores + ConnectEmailAccount + EmailEnrollRunner; intercept blobs in `_dispatch`; schedule the runner; pass `email_accounts` to `ToolPolicy`; add email delete to `on_revoke`)
- Create: `src/ari/application/email/intercept.py` (pure `is_email_blob` already in Task 6; add `EmailBlobInterceptor`)
- Test: `tests/application/test_email_blob_interceptor.py`

**Interfaces:**
- Consumes: `ConnectEmailAccount` (Task 6), `is_email_blob` (Task 6).
- Produces: `EmailBlobInterceptor(connect_use_case)` with `async handle(user_id, text) -> str` returning the user-facing reply (success or error). This isolates the testable logic from `main.py`'s Telegram plumbing.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_email_blob_interceptor.py
import pytest

from ari.application.email.intercept import EmailBlobInterceptor


class FakeConnect:
    def __init__(self, result): self._result = result
    async def __call__(self, user_id, blob):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_success_reply_names_the_label():
    interceptor = EmailBlobInterceptor(FakeConnect("trabajo"))
    reply = await interceptor.handle("7", "ari-mail:v1:xxx")
    assert "trabajo" in reply and "conectada" in reply.lower()


async def test_failure_returns_error_not_raises():
    interceptor = EmailBlobInterceptor(FakeConnect(ValueError("blob ilegible")))
    reply = await interceptor.handle("7", "ari-mail:v1:bad")
    assert "blob ilegible" in reply
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_email_blob_interceptor.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write the interceptor**

```python
# src/ari/application/email/intercept.py
class EmailBlobInterceptor:
    """Turns a pasted ari-mail blob into a stored account + a chat reply, never
    raising into the dispatch loop."""

    def __init__(self, connect_use_case):
        self._connect = connect_use_case

    async def handle(self, user_id: str, text: str) -> str:
        try:
            label = await self._connect(user_id, text)
        except ValueError as exc:
            return f"No pude conectar la casilla: {exc}."
        return f"📧 Casilla «{label}» conectada ✅"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/application/test_email_blob_interceptor.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Wire everything into `main.py`**

In `build(...)` (where `vault` and `tools` are created):

```python
    from ari.infrastructure.crypto.fernet_cipher import FernetCipher
    from ari.infrastructure.email.sealed_box import AriSealedBox
    from ari.infrastructure.email.sqlite_email_accounts import SqliteEmailAccounts
    email_cipher = FernetCipher(settings.vault_key) if settings.vault_key else None
    email_accounts = SqliteEmailAccounts(conn, email_cipher)
    sealed_box = AriSealedBox(vault)
```

Pass `email_accounts=email_accounts` to the `ToolPolicy(...)` constructor. Add `sealed_box`, `email_accounts`, and a new `SqliteEmailEnrollRequests(conn)` to the returned `Components` (extend the dataclass).

In `_post_init`:
- Build `ConnectEmailAccount(c.sealed_box, c.email_accounts)` and `EmailBlobInterceptor(...)`; store on `app.bot_data["email_interceptor"]`.
- Define `async def send_document(chat_id, filename, content): await app.bot.send_document(chat_id=int(chat_id), document=content, filename=filename)`.
- Build `EmailEnrollRunner(c.email_enroll, c.sealed_box, render_enroll_html, send_document)` and schedule it on the same cadence as `CredentialRequestRunner` (find where `CredentialRequestRunner(...)` is scheduled and add the enroll runner beside it).
- In `_on_revoke`, add `await c.email_accounts.delete_for_user(user_id)`.

In `_dispatch`, immediately after `user_id = str(msg.from_user.id)` and the admit check, before the lifecycle/handler path:

```python
        from ari.application.email.connect_email_account import is_email_blob
        if is_email_blob(text):
            interceptor = app.bot_data["email_interceptor"]
            reply = await interceptor.handle(user_id, text)
            try:
                await msg.delete()
            except Exception:  # noqa: BLE001
                pass
            await _reply_parts(msg, reply)
            return
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all green, including the pre-existing suite). Investigate any failure before continuing.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/ari/application/email src/ari/infrastructure/email src/ari/application/tools/tool_policy.py`
Expected: no new findings (pre-existing BLE001 elsewhere is acceptable per repo history).

- [ ] **Step 8: Commit**

```bash
git add src/ari/main.py src/ari/application/email/intercept.py tests/application/test_email_blob_interceptor.py
git commit -m "feat(email): intercept pasted blobs, send enroll HTML, inject accounts, revoke"
```

---

## Self-Review

- **Spec coverage:** Storage (Task 2), encryption (Task 1), runtime injection (Task 9), sealed-box loading (Tasks 4-6), bridge/document send (Task 7, Task 10), tools + registry + injection rule (Task 8), isolation + revocation (Task 2 `delete_for_user`, wired in Task 8 MCP + Task 10 bot), blob interception before memory (Task 10). All spec sections map to a task.
- **Review Focus coverage:** base64 variant (Task 6 test decrypts a standard-b64 blob; Task 5 asserts ORIGINAL pinned in HTML); vault key unset (Task 4 `available`, Task 9 degrade path, Task 1 empty-key raise); unsafe label (Task 3 `valid_label`, Task 6 rejection); malformed blob not leaking (Task 6 `is_email_blob` + Task 10 interceptor returns, never raises, dispatch returns early); MCP cannot decrypt (Task 2 `summaries_for`/`remove` with `cipher=None`).
- **Type consistency:** `EmailAccount` fields are used identically in Tasks 2, 3, 6, 9. `server_name`/`email_server_spec` names match across Tasks 3 and 9. `is_email_blob`/`EMAIL_BLOB_PREFIX` defined once in Task 6, reused in Task 10.
- **Known unknowns to confirm at execution time (verify, don't guess):** the exact `Actor` import/fields (Task 8 Step 7), where `CredentialRequestRunner` is scheduled in `_post_init` (Task 10 Step 5), and whether `view` has other callers to update when it becomes async (Task 9 Step 3). Each has a verification step in its task.
