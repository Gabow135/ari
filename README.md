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
