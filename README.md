# Ari

A Python conversational agent over Telegram that reasons with **Claude Code**
and remembers each user across sessions via a layered memory. Built with a
hexagonal (ports & adapters) architecture.

**Phase 1** (this repo): Telegram gateway + layered memory + Claude reasoning.

## How it works

- **Domain core** (`src/ari/domain`) — pure, no external dependencies: entities,
  ports (`LLMPort`, `MemoryPort`, `EmbeddingsPort`, `GatewayPort`), and the
  `AgentService` that assembles the prompt.
- **Application** (`src/ari/application`) — `HandleMessage` (one agent turn) and
  `MemoryMaintainer` (background fact extraction + summarization).
- **Adapters** (`src/ari/infrastructure`):
  - LLM: the **Claude Code CLI** (`claude -p`, headless) — uses the CLI's own
    login (e.g. a Claude Code subscription); **no `ANTHROPIC_API_KEY`**.
  - Memory: SQLite + `sqlite-vec`, partitioned by `user_id` (per-user isolation).
  - Embeddings: `fastembed` with a multilingual model (local, no API).
  - Gateway: Telegram via `python-telegram-bot`.
- **Composition root** (`src/ari/main.py`) wires everything on a single event loop.

### Memory layers
1. **Working** — the last N messages, verbatim.
2. **Episodic** — past exchanges embedded and retrieved by semantic similarity.
3. **Facts** — stable per-user key/value profile, extracted by Claude.
4. **Summary** — a rolling summary of older conversation.

## Requirements

Runs on **macOS, Linux and Windows**.

> **Use `python3` explicitly on macOS/Linux.** On macOS/Homebrew there is no
> `python` alias, and bare `pip`/`pytest` may resolve to a different (older)
> interpreter — always invoke it as `python3 -m ...`. On Windows use the
> virtualenv's `python` (see below).

- Python 3.11+ (`python3 --version`)
- The **Claude Code CLI** installed and logged in (`claude login`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

```bash
# 1. Install (editable, with dev extras) — note: python3 -m pip, not bare pip
python3 -m pip install -e ".[dev]"

# 2. Configure — copy the example and set your token (NO API key needed)
cp .env.example .env
# edit .env and set TELEGRAM_BOT_TOKEN=...

# 3. Authenticate the Claude Code CLI (uses your subscription)
claude login
```

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # then set TELEGRAM_BOT_TOKEN=...
claude login
```

Notes:
- The npm-installed `claude.cmd` shim is detected automatically and Ari calls the
  real `claude.exe` behind it (running the `.cmd` would mangle multi-line prompts
  through `cmd.exe`). `ARI_CLAUDE_BIN` can point to a specific `claude.exe`.
- The Hugging Face cache warns that symlinks are unsupported unless Developer Mode
  is on; it still works. Silence it with `$env:HF_HUB_DISABLE_SYMLINKS_WARNING="1"`.

## Run

```bash
python3 -m ari.main                    # macOS / Linux
```

```powershell
.\.venv\Scripts\python -m ari.main    # Windows
```

Then message your bot on Telegram. It replies with context and remembers facts
and past exchanges across conversations.

While Ari works you see *typing…* and a live progress message (🤔 thinking,
📖/✏️/▶️ tool steps, the answer as it is written). It is deleted when the final
reply arrives, so only the answer stays in the chat.

## Soul and capabilities

Ari's identity lives in [`soul/SOUL.md`](soul/SOUL.md): who Ari is, how it
talks (neutral Spanish, *tuteo*), how it works and its limits. Edit it freely —
it is re-read when it changes, no restart needed.

What Ari can do comes from one registry,
[`capabilities.py`](src/ari/domain/agent/capabilities.py). It feeds both Ari's
prompt ("Tus capacidades", filtered by role: owners see admin commands, other
users don't) and Telegram's "/" menu. **When you add a feature, add it there**
and remove whatever it solves from `LIMITATIONS`, so Ari never promises what it
can't do.

Ari's chat runs the Claude CLI isolated (`--tools ""`, `--strict-mcp-config`,
`--setting-sources project`, `--disable-slash-commands`), so the host user's
claude.ai connectors, hooks, plugins and skills never leak into its replies.

## Proactivity

- **Reminders & tasks in natural language:** "recuérdame mañana a las 9 llamar a
  Juan", "cada lunes a las 8 resúmeme mis pendientes", "cancela el 12". Ari
  confirms with the stored `#id` and time; `/recordatorios` lists them.
  Reminders send the text; tasks run as a normal Ari turn and send the result.
- **Heartbeat (owner only):** every `ARI_HEARTBEAT_MINUTES` Ari reviews
  [`soul/HEARTBEAT.md`](soul/HEARTBEAT.md), your memory and upcoming items and
  writes only if it's worthwhile (max 3/day).
- **System notices (owner only):** stale access requests, Claude CLI failing,
  paused tasks.
- **Quiet hours** (`ARI_QUIET_HOURS`, default 22–7 local): no heartbeat, notices
  are queued until morning. Your own reminders still arrive on time.
- Everything is stored in SQLite and survives `/restart` and outages.

Ari's own actions are tools of its internal MCP server (`python -m ari.mcp_server`,
started by the Claude CLI per turn): `agendar`, `listar_agenda`, `cancelar`,
`recordar_dato`, `olvidar_dato`, `ver_datos` for everyone, plus `aprobar_acceso`,
`revocar_acceso`, `ver_accesos`, `enviar_mensaje`, `proponer_codigo` for the owner.
Scheduled tasks and the heartbeat only get read-only tools. Every change is confirmed
by a code-generated receipt appended to Ari's reply; messages to other users are
signed. Proposing code only prepares the usual `/code` plan; nothing changes until
the owner replies `dale`. `/code` runs with `--strict-mcp-config --setting-sources
project`: the target repo's project settings (`.claude/settings.json`) and CLAUDE.md
still apply, but `.claude/settings.local.json` and user-level settings don't.

## Tools and connections (MCP)

Everyone approved can get answers researched on the **web**. The owner also gets
the **MCP servers** declared in [`mcp/servers.json`](mcp/servers.json) (Google
Workspace and a read-only MySQL database out of the box). Secrets go in `.env` and
are referenced as `${VAR}`; a server with a missing variable or launcher is
disabled. `access: "users"` opens a server to approved users. Check the state with
`/conexiones`. The file and `.env` are re-read automatically.

Setup, once:
1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (provides `uvx`).
   Ari only checks for launchers (`uvx`, `npx`, …) when it re-reads
   `mcp/servers.json`, so after installing one, touch the file (e.g. `echo. >>
   mcp/servers.json` or just save it) or send `/restart` — otherwise it keeps
   using the "no se encontró" result from before you installed it.
2. Google: in Google Cloud create an OAuth client of type *Desktop*, enable the
   Gmail, Calendar and Drive APIs, and set `GOOGLE_OAUTH_CLIENT_ID` /
   `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`. Run `uvx workspace-mcp` once in a
   terminal to authorize your account (tokens are stored locally).
3. MySQL: create a user with `SELECT` only and set `ARI_MYSQL_HOST`,
   `ARI_MYSQL_USER`, `ARI_MYSQL_PASS`, `ARI_MYSQL_DB` in `.env`.

`mcp/servers.json` pins each MCP server's package version (`pkg@X.Y.Z` for npm,
`pkg==X.Y.Z` for uvx/PyPI) so an upstream release can't silently start running
without review. To bump one, look up the new version yourself — don't guess —
with `npm view <package> version` (npm packages) or `pip index versions
<package>` / the PyPI JSON API (`https://pypi.org/pypi/<package>/json`, field
`info.version`) for `uvx`/PyPI packages, then edit the pin in
`mcp/servers.json` and touch the file (see above) or `/restart`.

On Windows, `.cmd`/`.bat` launchers (`npx`, `uvx`) run through `cmd /c`, and
`cmd.exe` interprets characters like `&`, `|`, `%` in **arguments**. Keep
secrets in a server's `env` block, never in `args` — `env` values are passed
through the environment, not the command line, so this doesn't apply to them
and they're never exposed on argv either way.

Content read through tools is treated as data, never as instructions (see
`soul/SOUL.md`). Google write tools are enabled for the owner.

## Access control

Only **owners** (`ARI_OWNER_IDS`) and users an owner approved can talk to Ari.

1. An unknown user writes to the bot (or sends `/start`) and gets a pairing code
   like `K7QM-X3PA`. Their message is not sent to Claude nor stored.
2. Every owner is notified once: `@juan (id 12345) pide acceso a Ari.`
3. An owner approves with `/aprobar K7QM-X3PA`; the user is told they're in.

Owner commands: `/aprobar CÓDIGO`, `/revocar USER_ID`, `/accesos` (list pending
and approved). Approvals persist in the SQLite `access` table.

> Set `ARI_OWNER_IDS` to your Telegram user id (ask [@userinfobot](https://t.me/userinfobot)).
> If it is empty nobody can approve, so **every user is blocked**. Owners must
> have opened a chat with the bot once to receive access notifications.

## Stop / restart from Telegram

Owners can send `/stop` or `/restart`; Ari asks for confirmation and acts only
if you reply `dale` (or `sí`/`ok`) within 60 s — any other reply cancels.

- `/stop` shuts Ari down cleanly. Start it again from the host machine.
- `/restart` shuts down cleanly and relaunches `python -m ari.main` with the same
  interpreter (so it picks up code changes), then messages you
  "Listo, Ari está de vuelta." On macOS/Linux the process is replaced in place
  (same PID, supervisor-friendly); on Windows a new process starts in the same
  console.

## Test

```bash
python3 -m pytest -m "not slow"     # fast unit + integration tests
python3 -m pytest -m slow           # also runs tests that download a model / hit the CLI
```

## Configuration (`.env`)

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | yes | — | Telegram bot token from @BotFather |
| `ARI_MODEL` | no | `claude-sonnet-4-6` | Claude model id passed to the CLI |
| `ARI_DB_PATH` | no | `./ari.db` | SQLite database path |
| `ARI_WORKING_MEMORY_SIZE` | no | `20` | Recent messages kept in working memory |
| `ARI_RECALL_TOP_K` | no | `5` | Episodic recalls retrieved per turn |
| `ARI_EMBEDDING_MODEL` | no | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | fastembed multilingual model (single-file ONNX) |
| `ARI_CLAUDE_BIN` | no | `claude` | Path/name of the Claude Code CLI binary |
| `ARI_OWNER_IDS` | yes* | — | Comma-separated owner Telegram ids: approve access and use coding mode. *Empty blocks everyone |
| `ARI_SOUL_DIR` | no | `./soul` | Folder holding `SOUL.md` (Ari's identity) |
| `ARI_CLAUDE_OAUTH_TOKEN` | recommended | — | Ari's own CLI login from `claude setup-token`. Keeps your account (and its email) out of Ari's context |
| `ARI_CLAUDE_CONFIG_DIR` | no | `./.ari-claude` | Ari's private claude CLI config dir (used with the token) |
| `ARI_TIMEZONE` | no | `America/Guayaquil` | Local time for reminders, cron and quiet hours |
| `ARI_QUIET_HOURS` | no | `22-7` | Quiet window (local hours); empty = none |
| `ARI_HEARTBEAT_MINUTES` | no | `60` | Heartbeat interval; `0` disables it |
| `ARI_MAX_ITEMS_PER_USER` | no | `20` | Active reminders/tasks per user |
| `ARI_MCP_CONFIG` | no | `./mcp/servers.json` | MCP server declarations |
| `ARI_CHAT_TIMEOUT_SECONDS` | no | `180` | Max seconds per Claude call with tools |

No `ANTHROPIC_API_KEY` is used — authentication is handled by the Claude Code CLI.

## Troubleshooting

**`onnxruntime ... External data path escapes model directory` on startup.**
This happens with large split ONNX embedding models (e.g.
`intfloat/multilingual-e5-large`, which ships `model.onnx` + `model.onnx_data`):
recent `onnxruntime` versions reject external-data files that the HuggingFace
blob cache stores in a separate directory. Use a **single-file** model — the
default `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` avoids it.
If you want the higher-quality e5-large, you'd need to pin an older `onnxruntime`
or download the model into a flat local directory; not needed for Phase 1.

**First run downloads the embedding model** (a few hundred MB) into a local
fastembed cache — that's expected and happens once.
