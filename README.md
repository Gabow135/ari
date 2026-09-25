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

> **Use `python3` explicitly.** On macOS/Homebrew there is no `python` alias, and
> bare `pip`/`pytest` may resolve to a different (older) interpreter. This project
> requires Python **3.12+** — always invoke it as `python3 -m ...`.

- Python 3.12+ (`python3 --version`)
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

## Run

```bash
python3 -m ari.main
```

Then message your bot on Telegram. It replies with context and remembers facts
and past exchanges across conversations.

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
| `ARI_EMBEDDING_MODEL` | no | `intfloat/multilingual-e5-large` | fastembed model (multilingual) |
| `ARI_CLAUDE_BIN` | no | `claude` | Path/name of the Claude Code CLI binary |

No `ANTHROPIC_API_KEY` is used — authentication is handled by the Claude Code CLI.
