# Ari — Secrets vault & filesystem MCP (Design)

- **Date:** 2026-09-25
- **Status:** Draft — pending user approval
- **Part of:** Autonomy (3A tools & MCP → **this: vault + filesystem** → 3C proactive `/code`)
- **Builds on:** 3A tools & MCP (`mcp/servers.json`, `McpRegistry`, `ToolPolicy`,
  `Toolset`, `--mcp-config` + `--strict-mcp-config`), chat isolation, Phase 2 workspace
  sandbox (`Workspace`, `allowed_root`).

## 1. Purpose

Two additions on top of 3A, and one correction of a false premise:

- **The MCP handler already exists** (`McpRegistry`). It is NOT rebuilt. Secrets are
  already kept out of the LLM stream: `${VAR}` is resolved into a role-specific config
  file the `claude` CLI reads, so Ari's reasoning only ever sees `mcp__<server>__*`.
- **Secrets vault:** today MCP secrets live in **plaintext** in `.env`. Add an
  encrypted-at-rest vault so a disk/repo leak does not expose them. Ari keeps using the
  secret without seeing the plaintext; only the human owner ever types it, via a CLI.
- **Filesystem MCP:** give the owner a `filesystem` MCP server so Ari can read/write
  files in a **scoped work directory** — never the directory that holds Ari's secrets.

Success: the owner loads a password once with `ari.vault set`; Ari uses the matching
MCP server with no plaintext anywhere on disk except a transient `0600` launch file;
Ari can work with files under the configured work root and **cannot** read `.env`,
the vault, or its own OAuth token.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Vault storage | Encrypted **file**, `~/.ari/vault.enc`, `0600` | Portable; user choice over macOS Keychain |
| Crypto | **Fernet** (`cryptography`): AES-128-CBC + HMAC-SHA256 | Standard, authenticated; no home-grown crypto |
| Master key | `ARI_VAULT_KEY` in `.env` (a `Fernet.generate_key()` value) | User choice; `.env` is already the trust boundary (Telegram + OAuth tokens live there) |
| Resolution order | **vault → OS env → `.env`** → disable server | Vault-first; existing `.env` path kept as fallback (approach A) |
| Secret entry | `python3 -m ari.vault` CLI, `getpass` (never argv) | Only the human owner types plaintext; never via Telegram/prompt |
| Filesystem server | `@modelcontextprotocol/server-filesystem` via `npx`, `access: "owner"` | Official server; owner-only |
| FS root | `ARI_FS_ROOT` (a path, not a secret); unset → server disabled | Explicit, safe default (off) |
| FS safety guard | FS root must not equal/contain the project root, vault, `.env`, or `.ari-claude` → server **disabled + ERROR** (fail-safe) | This is what enforces "Ari never sees the key" |

## 3. Secrets vault

### 3.1 Port (domain, pure)

`domain/secrets/secret_vault.py`

```python
class SecretVault(ABC):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def names(self) -> list[str]: ...          # names only, never values
```

### 3.2 Adapter (infrastructure)

`infrastructure/secrets/fernet_vault.py` — `FernetVault(SecretVault)`

- State on disk: `vault.enc` = `Fernet(key).encrypt(json.dumps({name: value}).encode())`.
- Key: `ARI_VAULT_KEY` (a 32-byte url-safe base64 Fernet key). Missing key → vault
  **unavailable** (not an error): `get()` returns `None`, resolution falls back to
  env/`.env`. This keeps 3A working before any migration.
- Wrong key / corrupt file (`InvalidToken`, bad JSON) → vault **unavailable** + a loud
  ERROR log; secrets fall back to env/`.env`. Ari does not crash.
- Writes are atomic (temp file + `os.replace`) and `chmod 0600`; the parent dir
  (`~/.ari/`) is created `0700` if absent. First write to a missing file starts from
  an empty `{}`.
- `names()` returns keys only. There is no bulk "dump values" method.

### 3.3 CLI (human-only entry point)

`python3 -m ari.vault` — the ONLY place a plaintext secret is entered, by the owner:

| Command | Behavior |
|---|---|
| `init` | Print a fresh `Fernet.generate_key()` once, for the owner to paste into `.env` as `ARI_VAULT_KEY`. Does not touch the vault. |
| `set NAME` | Read the value with `getpass` (hidden, not argv, not shell history); store encrypted. |
| `list` | Print stored names only. |
| `delete NAME` | Remove one entry. |

`get` is intentionally omitted from the everyday surface (printing plaintext defeats
the purpose). Migration is manual: `ari.vault set` each secret, then remove it from `.env`.

## 4. Registry integration (approach A)

`McpRegistry` gains an injected `SecretVault` (optional; `None` → 3A behavior).
`_lookup(name)` changes from `env or .env` to:

```
vault.get(name)  ->  os.environ.get(name)  ->  dotenv_values(.env).get(name)  ->  None
```

`None` (missing everywhere) keeps the existing rule: **that server is disabled with a
warning**. `mcp/servers.json` is **unchanged** — it keeps using `${VAR}` placeholders.
The vault is wired in the composition root (`main.py`) and passed to the registry.

## 5. Filesystem MCP

New owner server in `mcp/servers.json`:

```json
"filesystem": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "${ARI_FS_ROOT}"],
  "access": "owner",
  "description": "Archivos del computador (carpeta de trabajo)"
}
```

- `${ARI_FS_ROOT}` resolves like any placeholder (it is a path, not a secret, so it
  normally comes from env/`.env`, not the vault). Unset/empty → server disabled
  (existing missing-var behavior). No filesystem access by default.
- `ToolPolicy` already generates `mcp__<server>__*`; the owner's `--allowed-tools`
  gains `mcp__filesystem` when the server is enabled. Non-owners never receive it
  (`access: "owner"`).

### 5.1 Safety guard (the core of "sin ver la clave")

Before enabling the `filesystem` server, the registry validates `ARI_FS_ROOT`
(`realpath`-resolved) against a deny set of `realpath`-resolved sensitive paths:

- the Ari **project root**,
- `vault_path` (`~/.ari/vault.enc`) and its parent,
- the project `.env`,
- `claude_config_dir` (`./.ari-claude`, which holds the resolved MCP configs **and**
  Ari's OAuth token).

Rule: `ARI_FS_ROOT` must be **neither equal to nor an ancestor of** any sensitive path.
If it is (e.g. `~/Documents/Proyectos`, which contains the Ari project), the filesystem
server is **disabled**, an ERROR is logged, and `/conexiones` shows the reason. Fail-safe:
no filesystem access is always preferred over unsafe access. This is consistent with
3A's "bad config disables the server" philosophy — it does not crash the bot.

The recommended `ARI_FS_ROOT` is therefore a **dedicated work directory** that does not
nest the Ari checkout (e.g. `~/AriFiles`), not a parent of it.

## 6. Data flow & security boundary (honest)

- **Load (human):** owner runs `ari.vault set X` → `getpass` → `Fernet.encrypt` →
  `~/.ari/vault.enc` (`0600`).
- **Runtime:** `McpRegistry` resolves `${X}` from the vault → writes the role config to
  `./.ari-claude/mcp/<role>.json` (`0600`) → `claude` CLI reads it via `--mcp-config`
  → launches the MCP server with the secret. The LLM stream only sees `mcp__<server>__*`.
- **What it protects:** `vault.enc` is encrypted at rest — a stolen disk/repo without
  `ARI_VAULT_KEY` yields nothing.
- **What it does NOT protect (stated plainly):** with the master key in `.env`, a
  filesystem-empowered Ari could in principle read `.env` + the vault and self-decrypt.
  The §5.1 guard is what removes that path by keeping every secret out of `ARI_FS_ROOT`.
  The transient `./.ari-claude/mcp/<role>.json` is plaintext at launch (unavoidable —
  the subprocess needs the secret); it is `0600`, inside the guarded project dir, and
  regenerated per run.

## 7. Configuration (additions)

| Variable | Default | Purpose |
|---|---|---|
| `ARI_VAULT_KEY` | — | Fernet key; unset → vault disabled, env/`.env` fallback |
| `ARI_VAULT_PATH` | `~/.ari/vault.enc` | Encrypted vault file location |
| `ARI_FS_ROOT` | — (unset → filesystem server off) | Work directory the filesystem MCP may access |

`Settings` gains `vault_key`, `vault_path`, `fs_mcp_root` (all `ARI_`-prefixed, from `.env`).

## 8. Architecture (new/changed)

```
domain/secrets/
  secret_vault.py        SecretVault ABC (pure)
infrastructure/secrets/
  fernet_vault.py        FernetVault(SecretVault): encrypted file, ARI_VAULT_KEY
ari/vault_cli.py         python3 -m ari.vault  (init/set/list/delete, getpass)
infrastructure/tools/
  mcp_registry.py        _lookup: vault -> env -> .env; FS-root safety guard
mcp/servers.json         + "filesystem" owner server
config/settings.py       + vault_key, vault_path, fs_mcp_root
main.py                  build FernetVault, inject into McpRegistry
```

New dependency: `cryptography` (Fernet).

## 9. Error handling

- Missing `ARI_VAULT_KEY` → vault disabled, fallback to env/`.env`, INFO log.
- Wrong key / corrupt vault → vault unavailable, ERROR log, fallback; no crash.
- Secret missing in vault + env + `.env` → that server disabled with a warning (3A rule).
- `ARI_FS_ROOT` fails the §5.1 guard → filesystem server disabled + ERROR + shown in
  `/conexiones`.
- `ARI_FS_ROOT` unset → filesystem server simply absent.

## 10. Testing (pytest + fakes)

Fast (no real crypto servers, no MCP subprocess):

- `test_fernet_vault.py`: set/get/delete round-trip; `list` returns names only; missing
  key → `get` is `None`; wrong key / corrupt file → unavailable (no raise to caller);
  file perms `0600`, dir `0700`; atomic write leaves no partial file.
- `FakeVault` (in-memory dict) added to `tests/fakes.py` for registry/policy tests.
- `test_mcp_registry.py` (extend): vault-first resolution; fallback to env then `.env`;
  missing everywhere disables the server.
- FS guard: `ARI_FS_ROOT` equal to / ancestor of project root, vault, `.env`, or
  `.ari-claude` → filesystem server disabled + reason; a safe dedicated root → enabled
  with `mcp__filesystem` only for the owner, never for users.
- `servers.json` with `filesystem` parses; argv/`--allowed-tools` include
  `mcp__filesystem` for owner turns only.
- `vault_cli`: `set` reads via `getpass` (patched), never from argv; `list` prints names;
  `init` prints a valid Fernet key.

Slow (skipped without a configured root/creds):

- With `ARI_FS_ROOT` set to a temp dir, owner asks Ari to list/read a file → real result.
- A secret migrated to the vault drives its MCP server end-to-end.

## 11. Out of scope

- macOS Keychain / passphrase master-key modes (user chose env-var file vault).
- Explicit `${vault:NAME}` syntax (approach B) — can be layered later without breaking A.
- Per-user (non-owner) filesystem access; per-path (not per-root) permissions.
- Secret rotation tooling, audit log, and `.env → vault` bulk import (manual for now).
- Replacing `.env` for non-MCP tokens (Telegram/OAuth stay as-is).

## 12. Acceptance criteria

1. `ari.vault init` prints a Fernet key; after pasting it and `ari.vault set X`, the
   secret is gone from plaintext `.env` yet the matching MCP server still works.
2. `vault.enc` opened without `ARI_VAULT_KEY` is unreadable ciphertext; wrong key logs
   an ERROR and Ari keeps running.
3. Ari's LLM stream never contains a resolved secret value (only `mcp__<server>__*`).
4. `ARI_FS_ROOT` set to a dir containing the Ari project → filesystem server disabled,
   `/conexiones` shows the reason.
5. `ARI_FS_ROOT` set to a safe dedicated dir → owner can read/write files there; the
   same tools are absent for approved non-owner users.
6. With a safe root, Ari cannot read `.env`, `vault.enc`, or `.ari-claude` (outside root).
