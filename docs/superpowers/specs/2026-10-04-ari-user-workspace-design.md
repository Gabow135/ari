# Ari per-user workspace (`.ari/`) — Design

- **Date:** 2026-10-04
- **Status:** Approved (conversational design) — pending written-spec review
- **Author:** brainstorming session (Gabriel + Ari assistant)
- **Topic:** A per-user, path-jailed workspace where Ari can create files/scripts and SQLite databases, and (owner only) execute arbitrary commands.

## 1. Context & problem

Ari is a Telegram assistant powered by Claude. Today all per-user state lives
in a single SQLite file (`ari.db`) partitioned by `user_id`; there is **no
per-user filesystem area**. The owner can already run arbitrary shell commands
through `proponer_comando` / `ShellRunner`
(`src/ari/infrastructure/command/shell_runner.py`), owner-gated and
confirmation-gated, but **without any path jail**.

The user wants a `.ari/`-style directory where Ari can create scripts, create
SQLite databases, and "use Ari for everything" — including execution.

## 2. Locked decisions (from brainstorming)

Five clarifying questions fixed the scope:

1. **Capability:** create **and** execute.
2. **Scope:** per-user, isolated workspaces.
3. **Requested runtime:** any command (maximum risk).
4. **Deployment reality:** no Docker on the host and not easy to add; Ari runs
   as a plain `python -m ari.main` process targeting macOS/Linux/Windows.
5. **Chosen path — "B: split by trust":**
   - **Owner** → full workspace: create/edit files + execute **arbitrary
     commands** (cwd path-jailed to their workspace; extends `ShellRunner`).
   - **Other users** → create/edit files + a **jailed SQL tool** against their
     own SQLite files. **No arbitrary code execution.**

### Why path B

Arbitrary multi-user command execution **cannot be safely isolated without a
real sandbox** (containers). With no container infrastructure, a path-jail plus
`subprocess` is trivially escapable (`os.system`, absolute paths, `..`,
fork-bombs, reading `~/.ari/vault.enc`). Granting that to unknown Telegram users
would be a remote-code-execution hole. Owner execution is acceptable because it
runs on the owner's own host under an existing trust model. The full-container
path (approach "A") is deferred until container infra exists.

## 3. Goals / non-goals

**Goals**
- Give every user a private, path-jailed workspace directory.
- File create/read/list/delete within that workspace (all users).
- Create and query SQLite databases within that workspace (all users), hardened.
- Arbitrary command execution scoped to the owner's workspace (owner only).
- Reuse existing patterns: the `Workspace` jail, the `AriTools` tool methods,
  the `allowed_ari_tools` role/context gating, the per-turn actor context.

**Non-goals (out of scope — YAGNI)**
- Containers / real sandboxing (deferred approach A).
- Persistent per-user runtime, dependency installation, network tooling.
- Binary file handling, cross-user workspace sharing, a web UI.
- Execution for non-owner users (explicitly excluded by path B).

## 4. Architecture

### 4.1 Data location
- New setting `ARI_WORKSPACES_DIR`, default `~/.ari/workspaces`
  (`src/ari/config/settings.py`). Lives **outside** the repo.
- Per user: `~/.ari/workspaces/<user_id>/`, created on first use.
  `user_id` is sanitized before use as a path segment.
- The vault (`~/.ari/vault.enc`) is a **sibling**, never inside any workspace
  jail root.
- `ARI_WORKSPACES_DIR` is added to the `sensitive` tuple in
  `src/ari/main.py` (~line 157) so no user-declared MCP server can expose it.

### 4.2 New components
- **`UserWorkspace`** (`src/ari/infrastructure/workspace/user_workspace.py`,
  new): generalizes the jail logic from
  `src/ari/infrastructure/coder/workspace.py`. Root = `<base>/<actor_id>/`.
  Every op resolves with `os.path.realpath` and rejects anything outside root
  (blocks `..` and symlink escapes). File ops: `write`, `read`, `list`,
  `delete`, with a per-file size cap.
- **`SqliteSandbox`** (`src/ari/infrastructure/workspace/sqlite_sandbox.py`,
  new): opens a `.sqlite` file **inside** the workspace (path-jailed) with a
  hardened connection (see §4.4). Allows DDL/DML/SELECT on the user's own
  databases.
- **Execution runner**: reuse/extend `ShellRunner`
  (`src/ari/infrastructure/command/shell_runner.py`) with `cwd` jailed to the
  owner's workspace, a wall-clock timeout, and an output cap.
- **`AriTools`** (`src/ari/application/ari_tools.py`): new methods, each
  resolving the workspace from `self._actor.id` and checking
  `self._allowed(<tool>)`, consistent with existing tools.

### 4.3 Tool surface

Tool identifiers are Spanish to match the existing convention (`agendar`,
`recordar_dato`, `proponer_comando`).

| Tool | Who | Behavior |
|---|---|---|
| `escribir_archivo(ruta, contenido)` | all | create/overwrite a text file in the caller's workspace |
| `leer_archivo(ruta)` | all | read a file from the caller's workspace |
| `listar_archivos(ruta=".")` | all | list the caller's workspace |
| `borrar_archivo(ruta)` | all | delete within the caller's workspace |
| `consultar_sql(base, sql)` | all | run SQL against a `.sqlite` in the caller's workspace (create tables, insert, query) |
| `ejecutar(comando)` | **owner only** | run an arbitrary command with `cwd` in the owner's workspace |

**Gating** (`src/ari/domain/tools/ari_permissions.py`):
- All six names added to `ARI_TOOLS`.
- File + SQL tools added to `_USER_CHAT` (available to users in chat).
- `ejecutar` is **not** in `_USER_CHAT` → owner-only, and only in context
  `CHAT` (never `TASK` / `HEARTBEAT`).
- Double enforcement: `allowed_ari_tools(is_owner, context)` **and** the
  `self._allowed(...)` check inside the `AriTools` method.

### 4.4 Security model & limits
- **Files / DB:** path jail on every op (realpath + inside-root); per-file size
  cap (~1 MB); text-only for now.
- **SQL (`consultar_sql`):** reject `ATTACH` / `DETACH`; `load_extension`
  disabled (default off in Python `sqlite3`); per-statement timeout (~5 s via
  a timer calling `Connection.interrupt`); result row cap (~1000); output size
  cap. DDL/DML allowed only on the caller's own databases.
- **`ejecutar` (owner):** wall-clock timeout (~60 s); stdout/stderr cap
  (~64 KB); `cwd` jailed to the owner's workspace. Runs with the Ari process's
  privileges — **no further sandbox** (accepted owner trust model; identical
  to today's `proponer_comando`). Network is not removed (not possible without
  a real sandbox; it is the owner's host).
- **Users never receive `ejecutar`** — enforced in both gating layers.

### 4.5 Data flow
1. `tool_policy.turn()` already injects `ARI_ACTOR_ID`, `ARI_ROLE`,
   `ARI_CONTEXT`, etc. into the per-turn MCP config
   (`src/ari/application/tools/tool_policy.py`).
2. The ari MCP server builds `AriTools` via `actor_from_env`
   (`src/ari/mcp_server/server.py`, `src/ari/mcp_server/__main__.py`).
3. Each new tool method resolves the workspace from the actor id:
   `UserWorkspace(settings.workspaces_dir, actor.id)`.
4. `ejecutar` additionally relies on the owner role baked into the turn (via
   `allowed_ari_tools`) plus `self._allowed("ejecutar")`.

## 5. Testing strategy

Pytest + pytest-asyncio (`asyncio_mode = "auto"`), following
`tests/infrastructure/test_workspace.py` and
`tests/infrastructure/test_mcp_server.py`.

- Jail escape attempts (`..`, absolute paths, symlinks) are rejected.
- `consultar_sql`: `ATTACH` rejected; statement timeout fires; row cap applied;
  a created table persists in the right file within the workspace.
- File size caps enforced; read/write/list/delete happy paths.
- `ejecutar` is owner-only: a non-owner turn does not expose it and the method
  returns the DENIED sentinel; owner path respects timeout + output cap.
- Each workspace is isolated: user A cannot read user B's files via any tool.

## 6. Risks & open questions

- **Owner `ejecutar` is not sandboxed** beyond cwd+timeout — accepted trust
  decision, but documented so it is never mistaken for isolation.
- **Limit values** (~1 MB, ~5 s, ~1000 rows, ~60 s, ~64 KB) are first-pass
  defaults; confirm during implementation / make them settings if needed.
- **Workspace location** (`~/.ari/workspaces`) assumes `~/.ari/` is writable in
  all target environments (it already hosts the vault, so this holds).
- **`user_id` sanitization**: Telegram ids are numeric and safe, but the
  sanitizer must still defend against unexpected values.

## 7. Future work (not now)

- Approach "A": ephemeral per-execution containers (Docker/Podman) to safely
  extend execution to non-owner users with real isolation.
- Approach "B+": persistent per-user containers for warm execution and stateful
  environments.
