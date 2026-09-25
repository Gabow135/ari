# Ari — Phase 1: Telegram Gateway + Layered Memory (Design)

- **Date:** 2026-09-24
- **Status:** Draft — pending user approval
- **Scope:** Phase 1 only (Gateway + Memory). Tools, multi-user hardening, and
  production deployment are later phases and are explicitly out of scope here.

## 1. Purpose

Ari is a Python conversational agent reachable over Telegram. Phase 1 delivers a
working agent that:

1. Receives a message from a Telegram user.
2. Reasons with Claude (Anthropic) using relevant context.
3. Remembers the user across turns and across sessions via a layered memory.
4. Replies in the same chat.

Success for Phase 1: a user writes to the bot, gets a coherent answer, and Ari
demonstrably recalls facts and past exchanges from earlier conversations — not
just the current message window.

### Intended outcome (from brainstorming)

The overall product must eventually support three roles: (a) a personal
assistant for the owner, (b) an agent that executes tasks via tools, and (c) a
multi-user assistant for a team/company (Sukasa). Phase 1 does **not** build
tools or multi-user administration, but its architecture must not block them.
Concretely: every stored record is partitioned by `user_id` from day one, and
the LLM, channel, and storage are all behind ports so later phases swap adapters
instead of rewriting the core.

## 2. Locked decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM | Claude (Anthropic) | Strongest tool-calling / agent reasoning for later phases |
| Memory strategy | Custom layered memory | Full control, no framework lock-in |
| Storage | SQLite + `sqlite-vec` | Zero-infra start; migrate to Postgres + pgvector when multi-user demands it |
| Architecture | Hexagonal (Ports & Adapters) | Swap Telegram/Claude/SQLite without touching the core |
| Embeddings | `fastembed` with a **multilingual** model | No per-token cost, no data leaves the host; Claude has no native embeddings; multilingual is required because users write in Spanish; swappable to Voyage AI later |
| Telegram lib | `python-telegram-bot` v21+ (async) | Mature, async-native |
| Language / runtime | Python 3.12+, fully async | — |

## 3. Architecture

Hexagonal. The domain core has no knowledge of Telegram, Anthropic, or SQLite.
Those live in `infrastructure` and are wired together in `main.py` (composition
root) via dependency injection.

### 3.1 Directory layout

```
ari/
  pyproject.toml
  .env.example
  src/ari/
    domain/                          # core — no external dependencies
      agent/
        message.py                   # Message entity (role, content, user_id, ts)
        conversation.py              # Conversation entity (ordered messages)
        agent_service.py             # builds the prompt, orchestrates one turn
      memory/
        entities.py                  # Recall, Fact, Summary value objects
        memory_port.py               # MemoryPort (interface)
      ports/
        llm_port.py                  # LLMPort (interface)
        gateway_port.py              # GatewayPort (channel in/out interface)
    application/
      handle_message.py              # use case: process one inbound message
    infrastructure/
      llm/
        anthropic_adapter.py         # implements LLMPort with the Anthropic SDK
      gateway/
        telegram_adapter.py          # implements GatewayPort with python-telegram-bot
      memory/
        sqlite_memory_adapter.py     # implements MemoryPort with SQLite + sqlite-vec
        embeddings.py                # EmbeddingsPort + fastembed implementation
      persistence/
        db.py                        # connection, schema migrations
    config/
      settings.py                    # env loading via pydantic-settings
    main.py                          # composition root: build adapters, inject, run
  tests/
    domain/
    application/
    infrastructure/
```

### 3.2 Ports (interfaces owned by the domain)

- **`GatewayPort`** — an inbound/outbound channel.
  - `async def start(handler: Callable[[IncomingMessage], Awaitable[OutgoingMessage]]) -> None`
    registers the message handler and runs the channel loop.
  - The domain never imports Telegram types; the adapter maps Telegram updates to
    `IncomingMessage(user_id, chat_id, text)` and `OutgoingMessage(chat_id, text)`.
- **`LLMPort`** — text completion with a system prompt and message history.
  - `async def complete(system: str, messages: list[Message], max_tokens: int) -> str`
  - Adapter handles model selection, retries, and API specifics.
- **`MemoryPort`** — the layered memory contract (see §4).
- **`EmbeddingsPort`** — `async def embed(texts: list[str]) -> list[list[float]]`.

## 4. Layered memory (Phase 1 core)

All records are keyed by `user_id`. Four cooperating layers:

### 4.1 Working memory (short-term)
The last `N` raw messages of the current conversation (default `N = 20`,
configurable). Passed verbatim to the model. Backed by the `messages` table.

### 4.2 Episodic memory (long-term, semantic)
Each completed exchange (user message + assistant reply, or a meaningful unit)
is embedded and stored. On a new inbound message, the system embeds the query
and retrieves the top-`k` most similar past recalls (default `k = 5`) via
`sqlite-vec` cosine similarity, then injects them into the prompt. This is what
lets Ari recall conversations from days or weeks earlier.

### 4.3 Facts (structured profile)
Stable per-user facts (name, preferences, recurring context) stored as
key-value rows. Extracted by a lightweight Claude call **in the background**
after a turn completes, so it never blocks the reply. Facts are always injected
into the prompt (they are small and high-value).

### 4.4 Summarization (compression)
When a conversation exceeds a message/token threshold, older messages are
summarized by Claude into a rolling `Summary` and dropped from working memory,
keeping context and cost bounded. The summary is injected into the prompt.

### 4.5 MemoryPort contract

```
async def recent_messages(user_id, limit) -> list[Message]
async def append_message(message) -> None
async def store_recall(user_id, text, embedding, metadata) -> None
async def retrieve_recalls(user_id, query_embedding, k) -> list[Recall]
async def get_facts(user_id) -> list[Fact]
async def upsert_fact(user_id, key, value) -> None
async def get_summary(user_id) -> Summary | None
async def upsert_summary(user_id, summary) -> None
```

### 4.6 Storage schema (SQLite)

- `messages(id, user_id, role, content, created_at)`
- `recalls(id, user_id, content, metadata_json, created_at)`
- `recalls_vec` — `sqlite-vec` virtual table linking `recalls.id` → embedding
- `facts(user_id, key, value, updated_at)` — PK `(user_id, key)`
- `summaries(user_id, content, updated_at)` — PK `user_id`

## 5. Agent turn (data flow)

```
Telegram update
  → TelegramAdapter maps to IncomingMessage(user_id, chat_id, text)
  → HandleMessage use case:
      1. load working memory (recent_messages, last N)
      2. embed(text) → retrieve_recalls(top-k) ; get_facts ; get_summary
      3. AgentService builds prompt:
           system + facts + summary + recalls + recent history + new message
      4. LLMPort.complete(...) → Claude reply
      5. persist turn: append_message(user), append_message(assistant),
         store_recall(exchange, embedding)
      6. background: fact extraction; summarization if over threshold
  → OutgoingMessage(chat_id, reply)
  → TelegramAdapter sends to chat
```

## 6. Configuration

`config/settings.py` via `pydantic-settings`, loaded from `.env`:

- `ANTHROPIC_API_KEY` (required)
- `TELEGRAM_BOT_TOKEN` (required)
- `ARI_MODEL` (default: `claude-sonnet-4-6`)
- `ARI_DB_PATH` (default: `./ari.db`)
- `ARI_WORKING_MEMORY_SIZE` (default: 20)
- `ARI_RECALL_TOP_K` (default: 5)
- `ARI_EMBEDDING_MODEL` (default: a multilingual fastembed model, e.g.
  `intfloat/multilingual-e5-large` or `BAAI/bge-m3`; must handle Spanish)

`.env.example` ships with every key documented and no secrets.

## 7. Error handling & resilience

- **Claude calls:** retry with exponential backoff on rate limits / 429 / 529;
  bounded attempts; surface a friendly fallback message on final failure.
- **Memory retrieval failure:** degrade gracefully — answer using working memory
  alone rather than dropping the turn. Log the failure.
- **Telegram:** respect send timeouts; split messages longer than the platform
  limit; ignore non-text updates in Phase 1 (log and skip).
- **Background tasks (facts/summary):** failures are logged and never affect the
  user-facing reply.
- **Logging:** structured logging throughout; no secrets in logs.

## 8. Testing (TDD — strict mode enabled)

- **Domain + application:** unit tests against **fake ports** (in-memory
  `MemoryPort`, fake `LLMPort`, fake `EmbeddingsPort`). No network, no real DB.
  RED → GREEN → REFACTOR for every unit.
- **Infrastructure adapters:** integration tests — SQLite in-memory for the
  memory adapter (with `sqlite-vec` loaded), a mocked Anthropic client for the
  LLM adapter, a stubbed update for the Telegram adapter mapping.
- **Runner:** `pytest` + `pytest-asyncio`.

## 9. Out of scope (later phases)

- Tool-calling / task execution (Phase 2).
- Multi-user administration, roles/permissions, Postgres migration, deployment
  (Phase 3).
- Additional channels (WhatsApp/web), internal integrations, observability
  stack (Phase 4).

## 10. Acceptance criteria (Phase 1)

1. Sending a Telegram message returns a Claude-generated reply in the same chat.
2. Facts stated by the user in one conversation are recalled in a later,
   separate conversation.
3. Semantically related past exchanges are retrieved and demonstrably influence
   answers beyond the last `N` messages.
4. All records are partitioned by `user_id`; two users never see each other's
   memory.
5. A Claude API failure yields a graceful fallback, not a crash.
6. Domain and application logic pass unit tests using fake ports, with no
   network or real database access.
