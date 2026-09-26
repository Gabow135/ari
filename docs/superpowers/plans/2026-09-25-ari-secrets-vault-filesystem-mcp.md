# Ari Secrets Vault & Filesystem MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an encrypted-at-rest secrets vault the MCP registry reads before `.env`, and an owner-only filesystem MCP server whose work root is guarded so Ari can never reach its own secrets.

**Architecture:** A `SecretVault` port (domain) with a `FernetVault` file adapter (`~/.ari/vault.enc`, key from `ARI_VAULT_KEY`). `McpRegistry._lookup` resolves `vault → OS env → .env` (approach A: `servers.json` keeps `${VAR}`). A pure `unsafe_fs_root` domain function backs a `"guarded"` server field; the registry disables any guarded server whose resolved root would expose a sensitive path. A `python3 -m ari.vault` CLI is the only place a human types plaintext.

**Tech Stack:** Python ≥3.11, `cryptography` (Fernet), pydantic-settings, pytest + pytest-asyncio. Claude CLI subprocess (existing).

**Spec:** `docs/superpowers/specs/2026-09-25-ari-secrets-vault-filesystem-mcp-design.md`

## Global Constraints

- Python `>=3.11` (`pyproject.toml`). No new runtime dep except `cryptography`.
- Code artifacts (identifiers, comments, JSON keys) in **English**. User-facing status
  `detail` strings stay **Spanish**, matching the existing registry convention
  (`"falta DBPASS"`, `"no se encontró '…' en el PATH"`).
- Run tests with `python3 -m pytest` (bare `pytest`/`pip` point to Python 3.9 on this
  machine). Ruff line-length 100.
- Secrets never on argv: the vault CLI reads values via `getpass`.
- `${VAR}` resolution order is exactly `vault → os.environ → .env`; missing everywhere
  keeps the existing rule (that server is disabled with a warning).

## Review Focus

- **Vault key unset** (`ARI_VAULT_KEY` empty): vault is transparently disabled and
  resolution falls through to env/`.env`; Ari does not crash. → Task 1.
- **Wrong/corrupt vault** (bad key → `InvalidToken`, or truncated file): `get()` returns
  `None` + ERROR log, never raises to the registry. → Task 1.
- **Secret in both vault and `.env`**: the vault value wins. → Task 3.
- **`ARI_FS_ROOT` unset**: the filesystem server is disabled via the normal missing-var
  path (`"falta ARI_FS_ROOT"`), not a guard error. → Task 5.
- **Sibling-not-ancestor root** (root `~/.ari/work` while vault is `~/.ari/vault.enc`):
  guard must allow it — it must block only roots that *contain* a sensitive path, not
  over-block siblings. → Task 4.

---

### Task 1: SecretVault port + FernetVault adapter

**Files:**
- Modify: `pyproject.toml:5-13` (add `cryptography`)
- Create: `src/ari/domain/vault/__init__.py` (empty)
- Create: `src/ari/domain/vault/secret_vault.py`
- Create: `src/ari/infrastructure/vault/__init__.py` (empty)
- Create: `src/ari/infrastructure/vault/fernet_vault.py`
- Test: `tests/infrastructure/test_fernet_vault.py`

**Interfaces:**
- Produces: `SecretVault` ABC with `get(name: str) -> str | None`, `set(name, value)`,
  `delete(name)`, `names() -> list[str]`.
- Produces: `FernetVault(path: str, key: str)` implementing `SecretVault`. Empty `key`
  → vault disabled (`get` → `None`, `names` → `[]`, `set`/`delete` raise `RuntimeError`).

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"cryptography>=43"` to the `dependencies` list:

```toml
dependencies = [
  "python-telegram-bot>=21",
  "aiosqlite>=0.20",
  "sqlite-vec>=0.1.3",
  "fastembed>=0.4",
  "pydantic-settings>=2.5",
  "croniter>=2.0",
  "tzdata>=2024.1",
  "cryptography>=43",
]
```

Then install it: `python3 -m pip install -e ".[dev]"` (or `python3 -m pip install cryptography`).

- [ ] **Step 2: Write the failing test**

Create `tests/infrastructure/test_fernet_vault.py`:

```python
import os

import pytest
from cryptography.fernet import Fernet

from ari.infrastructure.vault.fernet_vault import FernetVault

KEY = Fernet.generate_key().decode()


def _vault(tmp_path, key=KEY):
    return FernetVault(str(tmp_path / ".ari" / "vault.enc"), key)


def test_set_get_roundtrip(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    assert v.get("GID") == "gid-1"


def test_names_returns_names_only_sorted(tmp_path):
    v = _vault(tmp_path)
    v.set("B", "2")
    v.set("A", "1")
    assert v.names() == ["A", "B"]


def test_delete_removes_entry(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    v.delete("GID")
    assert v.get("GID") is None


def test_missing_key_disables_vault(tmp_path):
    v = _vault(tmp_path, key="")
    assert v.get("GID") is None
    assert v.names() == []
    with pytest.raises(RuntimeError):
        v.set("GID", "x")


def test_missing_file_returns_none(tmp_path):
    v = _vault(tmp_path)  # nothing written yet
    assert v.get("GID") is None


def test_wrong_key_falls_back_without_raising(tmp_path, caplog):
    _vault(tmp_path).set("GID", "gid-1")
    other = FernetVault(str(tmp_path / ".ari" / "vault.enc"),
                        Fernet.generate_key().decode())
    assert other.get("GID") is None  # cannot decrypt, but no raise


def test_corrupt_file_falls_back_without_raising(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    path = tmp_path / ".ari" / "vault.enc"
    path.write_bytes(b"not a fernet token")
    assert v.get("GID") is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX perms")
def test_file_is_0600_and_dir_0700(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    path = tmp_path / ".ari" / "vault.enc"
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert (os.stat(tmp_path / ".ari").st_mode & 0o777) == 0o700


def test_no_leftover_temp_file(tmp_path):
    v = _vault(tmp_path)
    v.set("GID", "gid-1")
    files = os.listdir(tmp_path / ".ari")
    assert files == ["vault.enc"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_fernet_vault.py -q`
Expected: FAIL — `ModuleNotFoundError: ari.infrastructure.vault.fernet_vault`.

- [ ] **Step 4: Write the port**

Create `src/ari/domain/vault/__init__.py` (empty) and
`src/ari/domain/vault/secret_vault.py`:

```python
from abc import ABC, abstractmethod


class SecretVault(ABC):
    """A store Ari resolves secrets from without the LLM ever seeing them.
    Names only are ever listed; there is no bulk value dump by design."""

    @abstractmethod
    def get(self, name: str) -> str | None: ...

    @abstractmethod
    def set(self, name: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    @abstractmethod
    def names(self) -> list[str]: ...
```

- [ ] **Step 5: Write the adapter**

Create `src/ari/infrastructure/vault/__init__.py` (empty) and
`src/ari/infrastructure/vault/fernet_vault.py`:

```python
"""Encrypted-at-rest secret store: a single Fernet-encrypted JSON blob on disk.
The key comes from ARI_VAULT_KEY; without it the vault is transparently disabled
so the registry falls back to env/.env."""
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from ari.domain.vault.secret_vault import SecretVault

log = logging.getLogger("ari.vault")


class FernetVault(SecretVault):
    def __init__(self, path: str, key: str):
        self._path = os.path.expanduser(path)
        self._fernet = Fernet(key.encode()) if key else None

    def _load(self) -> dict:
        if self._fernet is None or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "rb") as f:
                return json.loads(self._fernet.decrypt(f.read()).decode())
        except (InvalidToken, ValueError, OSError) as exc:
            log.error("vault unreadable (%s); falling back to env/.env", exc)
            return {}

    def _save(self, data: dict) -> None:
        if self._fernet is None:
            raise RuntimeError("ARI_VAULT_KEY no está configurada")
        parent = os.path.dirname(self._path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
            os.chmod(parent, 0o700)
        token = self._fernet.encrypt(json.dumps(data).encode())
        tmp = self._path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(token)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> None:
        data = self._load()
        data.pop(name, None)
        self._save(data)

    def names(self) -> list[str]:
        return sorted(self._load())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/infrastructure/test_fernet_vault.py -q`
Expected: PASS (all 9).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/ari/domain/vault src/ari/infrastructure/vault tests/infrastructure/test_fernet_vault.py
git commit -m "feat: encrypted secret vault (SecretVault port + FernetVault)"
```

---

### Task 2: Vault CLI (`python3 -m ari.vault`)

**Files:**
- Create: `src/ari/vault.py`
- Test: `tests/test_vault_cli.py`

**Interfaces:**
- Consumes: `FernetVault` (Task 1), `Settings` (existing).
- Produces: `main(argv: list[str] | None = None) -> None` with subcommands
  `init`, `set NAME`, `list`, `delete NAME`. `init` prints a fresh Fernet key and does
  NOT touch `Settings` or the vault.

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_cli.py`:

```python
from cryptography.fernet import Fernet

import ari.vault as vault_cli
from ari.infrastructure.vault.fernet_vault import FernetVault


def test_init_prints_a_valid_fernet_key(capsys):
    vault_cli.main(["init"])
    printed = capsys.readouterr().out.strip()
    Fernet(printed.encode())  # raises if not a valid key


def test_set_reads_value_via_getpass_not_argv(tmp_path, monkeypatch, capsys):
    v = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    monkeypatch.setattr(vault_cli, "_vault", lambda: v)
    monkeypatch.setattr(vault_cli.getpass, "getpass", lambda prompt="": "s3cret")
    vault_cli.main(["set", "DBPASS"])
    assert v.get("DBPASS") == "s3cret"


def test_list_prints_names_only(tmp_path, monkeypatch, capsys):
    v = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    v.set("A", "1")
    monkeypatch.setattr(vault_cli, "_vault", lambda: v)
    vault_cli.main(["list"])
    assert capsys.readouterr().out.strip() == "A"


def test_delete_removes(tmp_path, monkeypatch):
    v = FernetVault(str(tmp_path / "vault.enc"), Fernet.generate_key().decode())
    v.set("A", "1")
    monkeypatch.setattr(vault_cli, "_vault", lambda: v)
    vault_cli.main(["delete", "A"])
    assert v.get("A") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vault_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.vault'`.

- [ ] **Step 3: Write the CLI**

Create `src/ari/vault.py`:

```python
"""Human-only entry point to load secrets into the vault: `python3 -m ari.vault`.
Values are read with getpass so they never appear on argv or in shell history."""
import argparse
import getpass

from cryptography.fernet import Fernet

from ari.config.settings import Settings
from ari.infrastructure.vault.fernet_vault import FernetVault


def _vault() -> FernetVault:
    s = Settings()
    return FernetVault(s.vault_path, s.vault_key)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ari.vault")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="print a fresh ARI_VAULT_KEY to paste into .env")
    p_set = sub.add_parser("set", help="store one secret (value read hidden)")
    p_set.add_argument("name")
    sub.add_parser("list", help="print stored names only")
    p_del = sub.add_parser("delete", help="remove one secret")
    p_del.add_argument("name")
    args = parser.parse_args(argv)

    if args.cmd == "init":
        print(Fernet.generate_key().decode())
        return

    vault = _vault()
    if args.cmd == "set":
        value = getpass.getpass(f"Valor de {args.name}: ")
        vault.set(args.name, value)
        print(f"Guardado {args.name}.")
    elif args.cmd == "list":
        for name in vault.names():
            print(name)
    elif args.cmd == "delete":
        vault.delete(args.name)
        print(f"Borrado {args.name}.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vault_cli.py -q`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add src/ari/vault.py tests/test_vault_cli.py
git commit -m "feat: ari.vault CLI (init/set/list/delete via getpass)"
```

---

### Task 3: Registry resolves vault → env → .env

**Files:**
- Modify: `src/ari/infrastructure/tools/mcp_registry.py:44-58` (`__init__`), `:106-114` (`_lookup`)
- Modify: `tests/fakes.py` (add `FakeVault`)
- Modify: `tests/infrastructure/test_mcp_registry.py` (extend `_registry` helper + new tests)

**Interfaces:**
- Consumes: `SecretVault` (Task 1).
- Produces: `McpRegistry(..., vault: SecretVault | None = None)`. `None` → current
  behavior. Resolution order becomes `vault.get → os.environ → .env`.
- Produces: `FakeVault(secrets: dict | None = None)` in `tests/fakes.py`.

- [ ] **Step 1: Add FakeVault to tests/fakes.py**

Append to `tests/fakes.py`:

```python
class FakeVault:
    """In-memory SecretVault for registry/policy tests (no crypto, no disk)."""

    def __init__(self, secrets: dict | None = None):
        self._secrets = dict(secrets or {})

    def get(self, name):
        return self._secrets.get(name)

    def set(self, name, value):
        self._secrets[name] = value

    def delete(self, name):
        self._secrets.pop(name, None)

    def names(self):
        return sorted(self._secrets)
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/infrastructure/test_mcp_registry.py`. First extend the `_registry`
helper to accept a vault (replace its current definition):

```python
def _registry(tmp_path, config=CONFIG, environ=ENV, platform="posix", which=_which,
              env_text="", vault=None):
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    return McpRegistry(str(cfg), str(env_file), str(tmp_path / "out"),
                       environ=dict(environ), platform=platform, which=which,
                       vault=vault), cfg, env_file
```

Then add the import at the top of the file:

```python
from tests.fakes import FakeVault
```

And add these tests:

```python
def test_vault_value_is_used(tmp_path):
    reg, _, _ = _registry(tmp_path, environ={}, vault=FakeVault(dict(ENV)))
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "gid-1"


def test_vault_wins_over_env_and_dotenv(tmp_path):
    reg, _, _ = _registry(tmp_path, env_text="GID=from-file\n",
                          vault=FakeVault({"GID": "from-vault", "DB": "ventas",
                                           "DBPASS": "s3cret", "DOCS_TOKEN": "tok"}))
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "from-vault"


def test_env_used_when_absent_from_vault(tmp_path):
    reg, _, _ = _registry(tmp_path, vault=FakeVault({}))  # empty vault
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "gid-1"  # from ENV


def test_missing_everywhere_disables_server(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "DBPASS"}
    reg, _, _ = _registry(tmp_path, environ=env, vault=FakeVault({}))
    status = {s.name: s for s in reg.status()}
    assert not status["mysql"].ok and status["mysql"].detail == "falta DBPASS"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/infrastructure/test_mcp_registry.py -q`
Expected: FAIL — `McpRegistry.__init__() got an unexpected keyword argument 'vault'`.

- [ ] **Step 4: Add the vault parameter**

In `mcp_registry.py`, change `__init__` (line 45-47) to accept `vault`:

```python
    def __init__(self, config_path: str, env_file: str, out_dir: str, *,
                 environ: Mapping[str, str] | None = None, platform: str = os.name,
                 which=shutil.which, vault=None):
```

And store it at the end of `__init__` (after line 57):

```python
        self._vault = vault
```

- [ ] **Step 5: Resolve the vault first in `_lookup`**

Replace `_lookup` (lines 106-114) with:

```python
    def _lookup(self, name: str) -> str | None:
        if self._vault is not None:
            vaulted = self._vault.get(name)
            if vaulted:
                return vaulted
        value = self._environ.get(name)
        if value:
            return value
        if _stamp(self._env_file) is not None:
            file_value = dotenv_values(self._env_file).get(name)
            if file_value:
                return file_value
        return None
```

- [ ] **Step 6: Run the full registry suite**

Run: `python3 -m pytest tests/infrastructure/test_mcp_registry.py -q`
Expected: PASS (existing + 4 new). The existing tests pass `vault=None` implicitly.

- [ ] **Step 7: Commit**

```bash
git add src/ari/infrastructure/tools/mcp_registry.py tests/fakes.py tests/infrastructure/test_mcp_registry.py
git commit -m "feat: MCP registry resolves secrets vault before env/.env"
```

---

### Task 4: Filesystem-root safety guard

**Files:**
- Create: `src/ari/domain/tools/fs_guard.py`
- Test: `tests/domain/test_fs_guard.py`
- Modify: `src/ari/infrastructure/tools/mcp_registry.py:16` (`_ARI_FIELDS`), `:143-174` (`_rebuild`), add import + helper
- Modify: `tests/infrastructure/test_mcp_registry.py` (guard tests)

**Interfaces:**
- Produces: `unsafe_fs_root(root: str, sensitive: Iterable[str]) -> str | None` —
  returns the first sensitive path the root would expose (root equals it or is an
  ancestor of it), else `None`.
- Produces: `McpRegistry(..., sensitive_paths: tuple[str, ...] = ())`; a server with
  Ari-only field `"guarded": true` is disabled with detail `"root inseguro: expone <p>"`
  when any resolved arg exposes a sensitive path.

- [ ] **Step 1: Write the failing guard test**

Create `tests/domain/test_fs_guard.py`:

```python
import os

from ari.domain.tools.fs_guard import unsafe_fs_root


def test_root_equal_to_sensitive_is_unsafe(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("x")
    assert unsafe_fs_root(str(secret), [str(secret)]) == str(secret)


def test_root_ancestor_of_sensitive_is_unsafe(tmp_path):
    project = tmp_path / "proj"
    (project).mkdir()
    env = project / ".env"
    env.write_text("x")
    # root is the parent that CONTAINS the project's .env
    assert unsafe_fs_root(str(tmp_path), [str(env)]) == str(env)


def test_sibling_root_is_safe(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    vault = tmp_path / ".ari" / "vault.enc"
    vault.parent.mkdir()
    vault.write_text("x")
    # work does NOT contain the vault → safe
    assert unsafe_fs_root(str(work), [str(vault)]) is None


def test_child_root_not_flagged_by_sibling_file(tmp_path):
    # root ~/.ari/work, vault ~/.ari/vault.enc — vault is not under work
    ari = tmp_path / ".ari"
    (ari / "work").mkdir(parents=True)
    vault = ari / "vault.enc"
    vault.write_text("x")
    assert unsafe_fs_root(str(ari / "work"), [str(vault)]) is None


def test_symlink_root_into_project_is_unsafe(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("x")
    link = tmp_path / "link"
    os.symlink(project, link)
    assert unsafe_fs_root(str(link), [str(project / ".env")]) == str(project / ".env")


def test_no_sensitive_paths_is_safe(tmp_path):
    assert unsafe_fs_root(str(tmp_path), []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_fs_guard.py -q`
Expected: FAIL — `ModuleNotFoundError: ari.domain.tools.fs_guard`.

- [ ] **Step 3: Write the guard function**

Create `src/ari/domain/tools/fs_guard.py`:

```python
"""A filesystem MCP root must not let Ari reach any secret. A root is unsafe when
it equals, or is an ancestor of, a sensitive path (so that path lies inside it).
Symlinks are resolved so a link into the project cannot slip past."""
import os
from collections.abc import Iterable


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def unsafe_fs_root(root: str, sensitive: Iterable[str]) -> str | None:
    root_real = _real(root)
    for s in sensitive:
        s_real = _real(s)
        if s_real == root_real or s_real.startswith(root_real + os.sep):
            return s  # original (unexpanded) form, for a readable message
    return None
```

- [ ] **Step 4: Run guard test to verify it passes**

Run: `python3 -m pytest tests/domain/test_fs_guard.py -q`
Expected: PASS (6).

- [ ] **Step 5: Write the failing registry-guard tests**

Add to `tests/infrastructure/test_mcp_registry.py`:

```python
def test_guarded_server_disabled_when_root_exposes_sensitive(tmp_path):
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx",
        "args": ["-y", "server-filesystem", "${ARI_FS_ROOT}"],
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    # root == the sensitive .env's parent (contains it)
    root = str(tmp_path)
    sensitive = (str(tmp_path / ".env"),)
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": root}, which=_which,
                      sensitive_paths=sensitive)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == f"root inseguro: expone {tmp_path / '.env'}"


def test_guarded_server_enabled_for_safe_root(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx",
        "args": ["-y", "server-filesystem", "${ARI_FS_ROOT}"],
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": str(work)}, which=_which,
                      sensitive_paths=(str(tmp_path / ".ari" / "vault.enc"),))
    names, path = reg.servers_for(True)
    assert names == ("filesystem",)
    servers = _load(path)
    assert "guarded" not in servers["filesystem"]  # Ari-only field stripped


def test_guarded_server_not_offered_to_users(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx", "args": ["-y", "sf", "${ARI_FS_ROOT}"],
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": str(work)}, which=_which,
                      sensitive_paths=())
    assert reg.servers_for(is_owner=False) == ((), None)
```

- [ ] **Step 6: Run to verify they fail**

Run: `python3 -m pytest tests/infrastructure/test_mcp_registry.py -k guarded -q`
Expected: FAIL — `unexpected keyword argument 'sensitive_paths'`.

- [ ] **Step 7: Wire the guard into the registry**

In `mcp_registry.py`:

Add the import near the top (after line 11):

```python
from ari.domain.tools.fs_guard import unsafe_fs_root
```

Extend `_ARI_FIELDS` (line 16):

```python
_ARI_FIELDS = ("access", "description", "guarded")
```

Add `sensitive_paths` to `__init__` (the `*`-only params) and store it:

```python
                 which=shutil.which, vault=None, sensitive_paths=()):
```
```python
        self._sensitive = tuple(sensitive_paths)
```

Add a helper method to the class:

```python
    def _exposed_arg(self, cli: dict) -> str | None:
        for arg in cli.get("args", []):
            if isinstance(arg, str):
                exposed = unsafe_fs_root(arg, self._sensitive)
                if exposed is not None:
                    return exposed
        return None
```

In `_rebuild`, replace the `else:` block body (lines 155-167) with the guarded version:

```python
            else:
                try:
                    # Ari-only fields are stripped; every other key (command, args,
                    # env, url, headers, type…) goes to the CLI with ${VAR} resolved.
                    cli = {k: self._subst(v) for k, v in spec.items()
                           if k not in _ARI_FIELDS}
                    if spec.get("guarded"):
                        exposed = self._exposed_arg(cli)
                        if exposed is not None:
                            detail = f"root inseguro: expone {exposed}"
                    if not detail:
                        cli = self._launcher(cli)
                except _Missing as exc:
                    detail = f"falta {exc.var}"
                except FileNotFoundError:
                    # Show the RAW (unresolved) command, never a resolved ${VAR}
                    # value, so a secret substituted into it never leaks here.
                    detail = f"no se encontró '{spec.get('command')}' en el PATH"
```

- [ ] **Step 8: Run the guard + full registry suite**

Run: `python3 -m pytest tests/infrastructure/test_mcp_registry.py tests/domain/test_fs_guard.py -q`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add src/ari/domain/tools/fs_guard.py tests/domain/test_fs_guard.py src/ari/infrastructure/tools/mcp_registry.py tests/infrastructure/test_mcp_registry.py
git commit -m "feat: guard filesystem MCP root against exposing secret paths"
```

---

### Task 5: Settings, servers.json filesystem entry, and composition wiring

**Files:**
- Modify: `src/ari/config/settings.py:34-36` (add vault fields)
- Modify: `mcp/servers.json` (add `filesystem` server)
- Modify: `src/ari/main.py:39-46` (import), `:97-98` (build vault + sensitive_paths)
- Test: `tests/config/test_settings.py` (or the existing settings test file)

**Interfaces:**
- Consumes: `FernetVault` (Task 1), `McpRegistry(..., vault, sensitive_paths)` (Tasks 3-4).
- Produces: `Settings.vault_key` (`ARI_VAULT_KEY`), `Settings.vault_path`
  (`ARI_VAULT_PATH`, default `~/.ari/vault.enc`). `ARI_FS_ROOT` stays a raw env var
  read by the registry via `${ARI_FS_ROOT}`.

- [ ] **Step 1: Write the failing settings test**

Add to `tests/config/test_settings.py` (create the file if the exact name differs;
match the existing settings test module):

```python
def test_vault_settings_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    from ari.config.settings import Settings
    s = Settings()
    assert s.vault_key == ""
    assert s.vault_path == "~/.ari/vault.enc"


def test_vault_key_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ARI_VAULT_KEY", "abc")
    from ari.config.settings import Settings
    assert Settings().vault_key == "abc"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/config/test_settings.py -k vault -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'vault_key'`.

- [ ] **Step 3: Add the settings fields**

In `settings.py`, after line 36 (`chat_timeout_seconds`), add:

```python
    # Secrets vault (filesystem MCP root ARI_FS_ROOT is read raw by the registry)
    vault_key: str = ""
    vault_path: str = "~/.ari/vault.enc"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/config/test_settings.py -k vault -q`
Expected: PASS (2).

- [ ] **Step 5: Add the filesystem server to servers.json**

In `mcp/servers.json`, add inside `mcpServers` (alongside `google` and `mysql`):

```json
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${ARI_FS_ROOT}"],
      "guarded": true,
      "access": "owner",
      "description": "Archivos del computador (carpeta de trabajo del dueño)"
    }
```

- [ ] **Step 6: Wire the vault + guard into main.py**

In `main.py`, add the import (after line 45, near the other tools import):

```python
from ari.infrastructure.vault.fernet_vault import FernetVault
```

Replace the registry construction (lines 97-98) with:

```python
    vault = FernetVault(settings.vault_path, settings.vault_key)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sensitive = (project_root, settings.vault_path,
                 os.path.join(project_root, ".env"), settings.claude_config_dir)
    registry = McpRegistry(settings.mcp_config, ".env",
                           os.path.join(settings.claude_config_dir, "mcp"),
                           vault=vault, sensitive_paths=sensitive)
```

- [ ] **Step 7: Verify the whole suite and a clean import**

Run: `python3 -m pytest -q`
Expected: PASS (whole suite, no regressions).

Run: `python3 -c "import ari.main; import ari.vault; print('ok')"`
Expected: prints `ok` (composition root imports cleanly with the new wiring).

- [ ] **Step 8: Commit**

```bash
git add src/ari/config/settings.py mcp/servers.json src/ari/main.py tests/config/test_settings.py
git commit -m "feat: wire vault + guarded filesystem MCP server into composition root"
```

---

## Setup notes (for the README / owner, after implementation)

1. `python3 -m ari.vault init` → copy the printed key into `.env` as `ARI_VAULT_KEY=…`.
2. For each MCP secret: `python3 -m ari.vault set GOOGLE_OAUTH_CLIENT_ID` (etc.), then
   remove that line from `.env`.
3. Set `ARI_FS_ROOT=/absolute/path/to/a/dedicated/work/dir` in `.env` — a directory that
   does **not** contain the Ari checkout, `~/.ari`, or `.env`. Leave it unset to keep the
   filesystem server off.
4. `/conexiones` in Telegram to confirm: `filesystem` shows `✅ configurado`, or the
   guard reason if the root is unsafe.

## Self-Review

- **Spec coverage:** §3 vault → Tasks 1-2; §4 registry integration → Task 3; §5 +
  §5.1 filesystem server + guard → Tasks 4-5; §7 config → Task 5; §8 architecture →
  all; §10 testing → each task's tests; §9 error handling → Tasks 1 (vault) + 3-4
  (missing var / guard). Acceptance criteria 1-6 map to Task 2 (init/set), Task 1
  (wrong key), Task 3 (no secret in stream — resolved into file only), Task 4/5 (unsafe
  vs safe root), Task 4 (non-owner exclusion).
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `SecretVault.get/set/delete/names` identical across port, adapter,
  `FakeVault`, and CLI. `unsafe_fs_root(root, sensitive) -> str | None` used consistently
  in guard tests and registry helper `_exposed_arg`. `McpRegistry` kwargs `vault` (Task 3)
  and `sensitive_paths` (Task 4) added additively.
- **Review Focus:** vault-key-unset (Task 1 `test_missing_key_disables_vault`),
  wrong/corrupt vault (Task 1 `test_wrong_key…`/`test_corrupt_file…`), vault-wins
  (Task 3 `test_vault_wins_over_env_and_dotenv`), `ARI_FS_ROOT` unset → normal missing-var
  (covered by existing `test_missing_var_disables_only_that_server` pattern + the
  `${ARI_FS_ROOT}` → `_Missing` path), sibling-not-ancestor (Task 4
  `test_sibling_root_is_safe` / `test_child_root_not_flagged_by_sibling_file`).
