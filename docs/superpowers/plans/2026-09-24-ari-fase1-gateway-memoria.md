# Ari — Phase 1 (Telegram Gateway + Layered Memory) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Python agent reachable over Telegram that reasons with Claude and remembers users across sessions via a layered memory.

**Architecture:** Hexagonal (Ports & Adapters). A dependency-free domain core (entities, ports, `AgentService`) plus an `application` use case (`HandleMessage`). Telegram, Anthropic, SQLite+`sqlite-vec`, and embeddings are infrastructure adapters wired in `main.py`. Everything is partitioned by `user_id`.

**Tech Stack:** Python 3.12+, asyncio, `anthropic`, `python-telegram-bot` v21+, `aiosqlite`, `sqlite-vec`, `fastembed` (multilingual model), `pydantic-settings`, `pytest`, `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-09-24-ari-fase1-gateway-memoria-design.md`

## Global Constraints

- Python 3.12+; all I/O is `async`. Test runner: `pytest` + `pytest-asyncio` (`asyncio_mode = auto`).
- Package layout is `src/`-based; import root is `ari`.
- Domain (`src/ari/domain/**`) and application (`src/ari/application/**`) MUST NOT import `anthropic`, `telegram`, `aiosqlite`, `sqlite_vec`, `fastembed`, or `pydantic` — enforced by an import test.
- Every stored record is keyed by `user_id`. No cross-user data may ever be returned.
- Secrets come only from env via `pydantic-settings`; never hard-coded, never logged.
- Default model id: `claude-sonnet-4-6`. Default embedding model: a multilingual fastembed model (`intfloat/multilingual-e5-large`), configurable.
- Commits use Conventional Commits. No AI attribution / no `Co-Authored-By` trailer.

## Review Focus

- **User isolation on semantic recall** — a query from user A must never return user B's recalls; the `sqlite-vec` search MUST filter by `user_id`. (Test in Task 7.)
- **Empty / whitespace-only inbound text** — must not embed or call the LLM with empty content; skip with a gentle reply. (Test in Task 5.)
- **Reply longer than Telegram's 4096-char limit** — must be split into multiple sends, not truncated or dropped. (Test in Task 10.)
- **Memory retrieval failure mid-turn** — the turn must still answer from working memory, not crash. (Test in Task 5.)
- **Non-text Telegram updates (photo/sticker/empty update)** — must be skipped and logged, not raised. (Test in Task 10.)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Project metadata, deps, pytest/ruff config |
| `.env.example` | Documented env keys, no secrets |
| `src/ari/domain/agent/message.py` | `Role`, `Message` value objects |
| `src/ari/domain/agent/conversation.py` | `Conversation` aggregate of messages |
| `src/ari/domain/memory/entities.py` | `Recall`, `Fact`, `Summary` value objects |
| `src/ari/domain/memory/memory_port.py` | `MemoryPort` protocol |
| `src/ari/domain/ports/llm_port.py` | `LLMPort` protocol |
| `src/ari/domain/ports/embeddings_port.py` | `EmbeddingsPort` protocol |
| `src/ari/domain/ports/gateway_port.py` | `GatewayPort`, `IncomingMessage`, `OutgoingMessage` |
| `src/ari/domain/agent/agent_service.py` | Builds the prompt from all memory layers |
| `src/ari/application/handle_message.py` | Orchestrates one turn |
| `src/ari/application/memory_maintainer.py` | Fact extraction + summarization (background) |
| `src/ari/config/settings.py` | `Settings` via pydantic-settings |
| `src/ari/infrastructure/persistence/db.py` | Connection + schema migrations |
| `src/ari/infrastructure/memory/sqlite_memory_adapter.py` | `MemoryPort` on SQLite+sqlite-vec |
| `src/ari/infrastructure/memory/embeddings.py` | `EmbeddingsPort` on fastembed |
| `src/ari/infrastructure/llm/anthropic_adapter.py` | `LLMPort` on Anthropic SDK, retries |
| `src/ari/infrastructure/gateway/telegram_adapter.py` | `GatewayPort` on python-telegram-bot |
| `src/ari/main.py` | Composition root; wires and runs |
| `tests/**` | Mirrors the package |

Test fakes live in `tests/fakes.py`: `FakeMemory`, `FakeLLM`, `FakeEmbeddings`.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/ari/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an importable `ari` package; `pytest` runs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smoke.py
import ari


def test_package_imports():
    assert ari.__name__ == "ari"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ari'`

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[project]
name = "ari"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "anthropic>=0.40",
  "python-telegram-bot>=21",
  "aiosqlite>=0.20",
  "sqlite-vec>=0.1.3",
  "fastembed>=0.4",
  "pydantic-settings>=2.5",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ari"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]
```

```python
# src/ari/__init__.py
"""Ari — conversational agent core."""
```

```python
# tests/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -e ".[dev]"` then `pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ari/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: scaffold ari package with pytest"
```

---

### Task 2: Domain entities (Message, Conversation, memory value objects)

**Files:**
- Create: `src/ari/domain/__init__.py`, `src/ari/domain/agent/__init__.py`, `src/ari/domain/memory/__init__.py`
- Create: `src/ari/domain/agent/message.py`
- Create: `src/ari/domain/agent/conversation.py`
- Create: `src/ari/domain/memory/entities.py`
- Test: `tests/domain/test_entities.py`

**Interfaces:**
- Produces:
  - `Role` = `Literal["user", "assistant"]`
  - `Message(user_id: str, role: Role, content: str, created_at: datetime)` — frozen; `content` must be non-empty after strip.
  - `Conversation(user_id: str, messages: list[Message])` with `add(message) -> None` and `last(n: int) -> list[Message]`.
  - `Recall(id: int | None, user_id: str, content: str, metadata: dict, created_at: datetime)`
  - `Fact(user_id: str, key: str, value: str)`
  - `Summary(user_id: str, content: str)`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_entities.py
from datetime import datetime, timezone

import pytest

from ari.domain.agent.message import Message
from ari.domain.agent.conversation import Conversation


def _msg(content="hi", role="user"):
    return Message(user_id="u1", role=role, content=content,
                   created_at=datetime.now(timezone.utc))


def test_message_rejects_empty_content():
    with pytest.raises(ValueError):
        _msg(content="   ")


def test_conversation_last_returns_tail_in_order():
    convo = Conversation(user_id="u1", messages=[])
    for i in range(5):
        convo.add(_msg(content=f"m{i}"))
    tail = convo.last(3)
    assert [m.content for m in tail] == ["m2", "m3", "m4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_entities.py -v`
Expected: FAIL with `ModuleNotFoundError` for `ari.domain.agent.message`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/agent/message.py
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    user_id: str
    role: Role
    content: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.content or not self.content.strip():
            raise ValueError("Message.content must not be empty")
```

```python
# src/ari/domain/agent/conversation.py
from dataclasses import dataclass, field

from ari.domain.agent.message import Message


@dataclass(slots=True)
class Conversation:
    user_id: str
    messages: list[Message] = field(default_factory=list)

    def add(self, message: Message) -> None:
        self.messages.append(message)

    def last(self, n: int) -> list[Message]:
        return self.messages[-n:] if n > 0 else []
```

```python
# src/ari/domain/memory/entities.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Recall:
    id: int | None
    user_id: str
    content: str
    metadata: dict = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Fact:
    user_id: str
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class Summary:
    user_id: str
    content: str
```

Also create the empty `__init__.py` files listed above.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_entities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain tests/domain/test_entities.py
git commit -m "feat: add domain entities for messages and memory"
```

---

### Task 3: Ports (protocols) + test fakes + import-boundary guard

**Files:**
- Create: `src/ari/domain/ports/__init__.py`
- Create: `src/ari/domain/ports/llm_port.py`
- Create: `src/ari/domain/ports/embeddings_port.py`
- Create: `src/ari/domain/ports/gateway_port.py`
- Create: `src/ari/domain/memory/memory_port.py`
- Create: `tests/fakes.py`
- Test: `tests/domain/test_ports.py`

**Interfaces:**
- Produces:
  - `LLMPort.complete(system: str, messages: list[Message], max_tokens: int = 1024) -> str`
  - `EmbeddingsPort.embed(texts: list[str]) -> list[list[float]]`
  - `IncomingMessage(user_id: str, chat_id: str, text: str)`, `OutgoingMessage(chat_id: str, text: str)`
  - `GatewayPort.start(handler: Callable[[IncomingMessage], Awaitable[OutgoingMessage]]) -> None`
  - `MemoryPort` methods: `recent_messages(user_id, limit) -> list[Message]`, `append_message(message) -> None`, `store_recall(user_id, content, embedding, metadata) -> None`, `retrieve_recalls(user_id, query_embedding, k) -> list[Recall]`, `get_facts(user_id) -> list[Fact]`, `upsert_fact(user_id, key, value) -> None`, `get_summary(user_id) -> Summary | None`, `upsert_summary(user_id, content) -> None`
  - Fakes: `FakeLLM`, `FakeEmbeddings`, `FakeMemory` implementing the above for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_ports.py
import importlib
import pkgutil

import ari.domain
from tests.fakes import FakeLLM, FakeMemory, FakeEmbeddings
from ari.domain.agent.message import Message
from datetime import datetime, timezone


async def test_fakes_satisfy_ports():
    llm = FakeLLM(reply="ok")
    emb = FakeEmbeddings(dim=4)
    mem = FakeMemory()
    msg = Message("u1", "user", "hola", datetime.now(timezone.utc))

    assert await llm.complete("sys", [msg]) == "ok"
    assert len(await emb.embed(["hola"])) == 1
    await mem.append_message(msg)
    assert (await mem.recent_messages("u1", 10))[0].content == "hola"


def test_domain_has_no_infra_imports():
    forbidden = {"anthropic", "telegram", "aiosqlite", "sqlite_vec",
                 "fastembed", "pydantic"}
    for mod in pkgutil.walk_packages(ari.domain.__path__, "ari.domain."):
        module = importlib.import_module(mod.name)
        src = getattr(module, "__file__", "") or ""
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for name in forbidden:
            assert f"import {name}" not in text, f"{mod.name} imports {name}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_ports.py -v`
Expected: FAIL with `ModuleNotFoundError` for `tests.fakes` / the port modules

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/ports/llm_port.py
from typing import Protocol

from ari.domain.agent.message import Message


class LLMPort(Protocol):
    async def complete(self, system: str, messages: list[Message],
                       max_tokens: int = 1024) -> str: ...
```

```python
# src/ari/domain/ports/embeddings_port.py
from typing import Protocol


class EmbeddingsPort(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

```python
# src/ari/domain/ports/gateway_port.py
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    user_id: str
    chat_id: str
    text: str


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    chat_id: str
    text: str


Handler = Callable[[IncomingMessage], Awaitable[OutgoingMessage]]


class GatewayPort(Protocol):
    async def start(self, handler: Handler) -> None: ...
```

```python
# src/ari/domain/memory/memory_port.py
from typing import Protocol

from ari.domain.agent.message import Message
from ari.domain.memory.entities import Fact, Recall, Summary


class MemoryPort(Protocol):
    async def recent_messages(self, user_id: str, limit: int) -> list[Message]: ...
    async def append_message(self, message: Message) -> None: ...
    async def store_recall(self, user_id: str, content: str,
                           embedding: list[float], metadata: dict) -> None: ...
    async def retrieve_recalls(self, user_id: str, query_embedding: list[float],
                               k: int) -> list[Recall]: ...
    async def get_facts(self, user_id: str) -> list[Fact]: ...
    async def upsert_fact(self, user_id: str, key: str, value: str) -> None: ...
    async def get_summary(self, user_id: str) -> Summary | None: ...
    async def upsert_summary(self, user_id: str, content: str) -> None: ...
```

```python
# tests/fakes.py
from ari.domain.agent.message import Message
from ari.domain.memory.entities import Fact, Recall, Summary


class FakeLLM:
    def __init__(self, reply: str = "ok"):
        self.reply = reply
        self.calls: list[tuple[str, list[Message]]] = []

    async def complete(self, system, messages, max_tokens=1024) -> str:
        self.calls.append((system, list(messages)))
        return self.reply


class FakeEmbeddings:
    def __init__(self, dim: int = 4):
        self.dim = dim

    async def embed(self, texts):
        # Deterministic pseudo-embedding from text length.
        return [[float(len(t))] * self.dim for t in texts]


class FakeMemory:
    def __init__(self, fail_retrieval: bool = False):
        self._messages: list[Message] = []
        self._recalls: list[Recall] = []
        self._facts: dict[tuple[str, str], Fact] = {}
        self._summaries: dict[str, Summary] = {}
        self.fail_retrieval = fail_retrieval

    async def recent_messages(self, user_id, limit):
        msgs = [m for m in self._messages if m.user_id == user_id]
        return msgs[-limit:]

    async def append_message(self, message):
        self._messages.append(message)

    async def store_recall(self, user_id, content, embedding, metadata):
        self._recalls.append(Recall(None, user_id, content, metadata))

    async def retrieve_recalls(self, user_id, query_embedding, k):
        if self.fail_retrieval:
            raise RuntimeError("boom")
        return [r for r in self._recalls if r.user_id == user_id][:k]

    async def get_facts(self, user_id):
        return [f for (u, _), f in self._facts.items() if u == user_id]

    async def upsert_fact(self, user_id, key, value):
        self._facts[(user_id, key)] = Fact(user_id, key, value)

    async def get_summary(self, user_id):
        return self._summaries.get(user_id)

    async def upsert_summary(self, user_id, content):
        self._summaries[user_id] = Summary(user_id, content)
```

Also create `src/ari/domain/ports/__init__.py` (empty).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_ports.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/ports src/ari/domain/memory/memory_port.py tests/fakes.py tests/domain/test_ports.py
git commit -m "feat: add domain ports and test fakes"
```

---

### Task 4: AgentService — prompt assembly

**Files:**
- Create: `src/ari/domain/agent/agent_service.py`
- Test: `tests/domain/test_agent_service.py`

**Interfaces:**
- Consumes: `Message`, `Fact`, `Recall`, `Summary`.
- Produces: `AgentService.build_prompt(facts, summary, recalls, history) -> str` returning the system prompt string, and `SYSTEM_PREAMBLE` constant. History (list[Message]) is passed separately to the LLM; `build_prompt` composes the *system* text (preamble + facts + summary + recalls).

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_agent_service.py
from ari.domain.agent.agent_service import AgentService
from ari.domain.memory.entities import Fact, Recall, Summary


def test_build_prompt_includes_all_layers():
    svc = AgentService()
    prompt = svc.build_prompt(
        facts=[Fact("u1", "name", "Gabo")],
        summary=Summary("u1", "Talked about the roadmap."),
        recalls=[Recall(1, "u1", "User prefers async Python.")],
    )
    assert "Gabo" in prompt
    assert "roadmap" in prompt
    assert "async Python" in prompt


def test_build_prompt_handles_empty_layers():
    svc = AgentService()
    prompt = svc.build_prompt(facts=[], summary=None, recalls=[])
    assert svc.SYSTEM_PREAMBLE in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_agent_service.py -v`
Expected: FAIL with `ModuleNotFoundError` for `agent_service`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/agent/agent_service.py
from ari.domain.memory.entities import Fact, Recall, Summary


class AgentService:
    SYSTEM_PREAMBLE = (
        "You are Ari, a helpful, concise assistant. "
        "Use the user's known facts and recalled context when relevant. "
        "If you are unsure, say so."
    )

    def build_prompt(self, facts: list[Fact], summary: Summary | None,
                     recalls: list[Recall]) -> str:
        parts = [self.SYSTEM_PREAMBLE]
        if facts:
            lines = "\n".join(f"- {f.key}: {f.value}" for f in facts)
            parts.append(f"Known facts about the user:\n{lines}")
        if summary:
            parts.append(f"Summary of earlier conversation:\n{summary.content}")
        if recalls:
            lines = "\n".join(f"- {r.content}" for r in recalls)
            parts.append(f"Relevant recalled context:\n{lines}")
        return "\n\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/agent/agent_service.py tests/domain/test_agent_service.py
git commit -m "feat: assemble system prompt from memory layers"
```

---

### Task 5: HandleMessage use case

**Files:**
- Create: `src/ari/application/__init__.py`
- Create: `src/ari/application/handle_message.py`
- Test: `tests/application/test_handle_message.py`

**Interfaces:**
- Consumes: `LLMPort`, `EmbeddingsPort`, `MemoryPort`, `AgentService`, `IncomingMessage`, `OutgoingMessage`.
- Produces: `HandleMessage(memory, llm, embeddings, agent, working_memory_size=20, recall_top_k=5)` with `async def __call__(incoming: IncomingMessage) -> OutgoingMessage`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_handle_message.py
import pytest

from ari.application.handle_message import HandleMessage
from ari.domain.agent.agent_service import AgentService
from ari.domain.ports.gateway_port import IncomingMessage
from tests.fakes import FakeEmbeddings, FakeLLM, FakeMemory


def _handler(mem=None, llm=None):
    return HandleMessage(
        memory=mem or FakeMemory(),
        llm=llm or FakeLLM(reply="hello back"),
        embeddings=FakeEmbeddings(),
        agent=AgentService(),
    )


async def test_returns_llm_reply_and_persists_turn():
    mem = FakeMemory()
    handler = _handler(mem=mem, llm=FakeLLM(reply="hello back"))
    out = await handler(IncomingMessage("u1", "c1", "hi"))
    assert out.text == "hello back"
    assert out.chat_id == "c1"
    stored = await mem.recent_messages("u1", 10)
    assert [m.role for m in stored] == ["user", "assistant"]


async def test_blank_message_short_circuits_without_llm():
    llm = FakeLLM(reply="should not be used")
    handler = _handler(llm=llm)
    out = await handler(IncomingMessage("u1", "c1", "   "))
    assert llm.calls == []
    assert out.text  # a gentle non-empty reply


async def test_degrades_when_retrieval_fails():
    mem = FakeMemory(fail_retrieval=True)
    handler = _handler(mem=mem, llm=FakeLLM(reply="still answered"))
    out = await handler(IncomingMessage("u1", "c1", "hi"))
    assert out.text == "still answered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_handle_message.py -v`
Expected: FAIL with `ModuleNotFoundError` for `handle_message`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/handle_message.py
import logging
from datetime import datetime, timezone

from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.memory.memory_port import MemoryPort
from ari.domain.ports.embeddings_port import EmbeddingsPort
from ari.domain.ports.gateway_port import IncomingMessage, OutgoingMessage
from ari.domain.ports.llm_port import LLMPort

log = logging.getLogger("ari.handle_message")
BLANK_REPLY = "Mandame un mensaje de texto y con gusto te ayudo."


class HandleMessage:
    def __init__(self, memory: MemoryPort, llm: LLMPort,
                 embeddings: EmbeddingsPort, agent: AgentService,
                 working_memory_size: int = 20, recall_top_k: int = 5):
        self._memory = memory
        self._llm = llm
        self._embeddings = embeddings
        self._agent = agent
        self._n = working_memory_size
        self._k = recall_top_k

    async def __call__(self, incoming: IncomingMessage) -> OutgoingMessage:
        text = incoming.text.strip()
        if not text:
            return OutgoingMessage(incoming.chat_id, BLANK_REPLY)

        now = datetime.now(timezone.utc)
        user_msg = Message(incoming.user_id, "user", text, now)
        await self._memory.append_message(user_msg)

        history = await self._memory.recent_messages(incoming.user_id, self._n)
        facts = await self._safe(self._memory.get_facts(incoming.user_id), [])
        summary = await self._safe(self._memory.get_summary(incoming.user_id), None)
        recalls = await self._retrieve(incoming.user_id, text)

        system = self._agent.build_prompt(facts, summary, recalls)
        reply = await self._llm.complete(system, history)

        await self._memory.append_message(
            Message(incoming.user_id, "assistant", reply, datetime.now(timezone.utc)))
        await self._store_recall(incoming.user_id, text, reply)
        return OutgoingMessage(incoming.chat_id, reply)

    async def _retrieve(self, user_id, text):
        try:
            vec = (await self._embeddings.embed([text]))[0]
            return await self._memory.retrieve_recalls(user_id, vec, self._k)
        except Exception:  # graceful degradation
            log.exception("recall retrieval failed; answering from working memory")
            return []

    async def _store_recall(self, user_id, text, reply):
        try:
            content = f"User: {text}\nAri: {reply}"
            vec = (await self._embeddings.embed([content]))[0]
            await self._memory.store_recall(user_id, content, vec, {})
        except Exception:
            log.exception("storing recall failed")

    @staticmethod
    async def _safe(awaitable, default):
        try:
            return await awaitable
        except Exception:
            log.exception("memory read failed; using default")
            return default
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/application/test_handle_message.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application tests/application/test_handle_message.py
git commit -m "feat: orchestrate one agent turn in HandleMessage use case"
```

---

### Task 6: Settings

**Files:**
- Create: `src/ari/config/__init__.py`
- Create: `src/ari/config/settings.py`
- Create: `.env.example`
- Test: `tests/config/test_settings.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings) with fields `anthropic_api_key`, `telegram_bot_token`, `model="claude-sonnet-4-6"`, `db_path="./ari.db"`, `working_memory_size=20`, `recall_top_k=5`, `embedding_model="intfloat/multilingual-e5-large"`. Env prefix `ARI_` except the two provider keys read from `ANTHROPIC_API_KEY` / `TELEGRAM_BOT_TOKEN`.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_settings.py
from ari.config.settings import Settings


def test_defaults_and_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-test")
    monkeypatch.setenv("ARI_RECALL_TOP_K", "7")
    s = Settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.telegram_bot_token == "tg-test"
    assert s.model == "claude-sonnet-4-6"
    assert s.recall_top_k == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError` for `settings`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/config/settings.py
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARI_", env_file=".env",
                                      extra="ignore")

    anthropic_api_key: str = Field(
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ARI_ANTHROPIC_API_KEY"))
    telegram_bot_token: str = Field(
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "ARI_TELEGRAM_BOT_TOKEN"))
    model: str = "claude-sonnet-4-6"
    db_path: str = "./ari.db"
    working_memory_size: int = 20
    recall_top_k: int = 5
    embedding_model: str = "intfloat/multilingual-e5-large"
```

```bash
# .env.example
# Anthropic API key (required)
ANTHROPIC_API_KEY=
# Telegram bot token from @BotFather (required)
TELEGRAM_BOT_TOKEN=
# Optional overrides:
ARI_MODEL=claude-sonnet-4-6
ARI_DB_PATH=./ari.db
ARI_WORKING_MEMORY_SIZE=20
ARI_RECALL_TOP_K=5
ARI_EMBEDDING_MODEL=intfloat/multilingual-e5-large
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/config .env.example tests/config/test_settings.py
git commit -m "feat: add typed settings and env example"
```

---

### Task 7: SQLite persistence + memory adapter

**Files:**
- Create: `src/ari/infrastructure/__init__.py`, `src/ari/infrastructure/persistence/__init__.py`, `src/ari/infrastructure/memory/__init__.py`
- Create: `src/ari/infrastructure/persistence/db.py`
- Create: `src/ari/infrastructure/memory/sqlite_memory_adapter.py`
- Test: `tests/infrastructure/test_sqlite_memory.py`

**Interfaces:**
- Consumes: `MemoryPort` contract, entities.
- Produces:
  - `async def connect(db_path: str) -> aiosqlite.Connection` (loads sqlite-vec, runs migrations).
  - `SqliteMemoryAdapter(conn, embedding_dim: int)` implementing every `MemoryPort` method. `retrieve_recalls` MUST filter by `user_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_sqlite_memory.py
import pytest

from ari.infrastructure.persistence.db import connect
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter


@pytest.fixture
async def adapter():
    conn = await connect(":memory:")
    yield SqliteMemoryAdapter(conn, embedding_dim=4)
    await conn.close()


async def test_facts_and_summary_roundtrip(adapter):
    await adapter.upsert_fact("u1", "name", "Gabo")
    await adapter.upsert_fact("u1", "name", "Gabriel")  # upsert overwrites
    facts = await adapter.get_facts("u1")
    assert [(f.key, f.value) for f in facts] == [("name", "Gabriel")]
    await adapter.upsert_summary("u1", "hello")
    assert (await adapter.get_summary("u1")).content == "hello"


async def test_recall_search_is_user_scoped(adapter):
    await adapter.store_recall("u1", "user one secret", [1.0, 0.0, 0.0, 0.0], {})
    await adapter.store_recall("u2", "user two secret", [1.0, 0.0, 0.0, 0.0], {})
    results = await adapter.retrieve_recalls("u1", [1.0, 0.0, 0.0, 0.0], k=5)
    assert all(r.user_id == "u1" for r in results)
    assert any("user one" in r.content for r in results)
    assert not any("user two" in r.content for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_sqlite_memory.py -v`
Expected: FAIL with `ModuleNotFoundError` for `db` / `sqlite_memory_adapter`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/persistence/db.py
import aiosqlite
import sqlite_vec

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

CREATE TABLE IF NOT EXISTS recalls (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_recalls_user ON recalls(user_id);

CREATE TABLE IF NOT EXISTS facts (
  user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (user_id, key));

CREATE TABLE IF NOT EXISTS summaries (
  user_id TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


async def connect(db_path: str, embedding_dim: int = 1024) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    await conn.enable_load_extension(True)
    await conn.load_extension(sqlite_vec.loadable_path())
    await conn.enable_load_extension(False)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SCHEMA)
    await conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS recalls_vec "
        f"USING vec0(recall_id INTEGER PRIMARY KEY, embedding FLOAT[{embedding_dim}])")
    await conn.commit()
    return conn
```

```python
# src/ari/infrastructure/memory/sqlite_memory_adapter.py
import json
import struct
from datetime import datetime, timezone

import aiosqlite

from ari.domain.agent.message import Message
from ari.domain.memory.entities import Fact, Recall, Summary


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteMemoryAdapter:
    def __init__(self, conn: aiosqlite.Connection, embedding_dim: int = 1024):
        self._conn = conn
        self._dim = embedding_dim

    async def recent_messages(self, user_id: str, limit: int) -> list[Message]:
        rows = await self._conn.execute_fetchall(
            "SELECT user_id, role, content, created_at FROM messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        msgs = [Message(r["user_id"], r["role"], r["content"],
                        datetime.fromisoformat(r["created_at"])) for r in rows]
        return list(reversed(msgs))

    async def append_message(self, message: Message) -> None:
        await self._conn.execute(
            "INSERT INTO messages (user_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (message.user_id, message.role, message.content,
             message.created_at.isoformat()))
        await self._conn.commit()

    async def store_recall(self, user_id, content, embedding, metadata) -> None:
        cur = await self._conn.execute(
            "INSERT INTO recalls (user_id, content, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, content, json.dumps(metadata), _now()))
        await self._conn.execute(
            "INSERT INTO recalls_vec (recall_id, embedding) VALUES (?, ?)",
            (cur.lastrowid, _pack(embedding)))
        await self._conn.commit()

    async def retrieve_recalls(self, user_id, query_embedding, k) -> list[Recall]:
        rows = await self._conn.execute_fetchall(
            "SELECT r.id, r.user_id, r.content, r.metadata_json "
            "FROM recalls_vec v JOIN recalls r ON r.id = v.recall_id "
            "WHERE r.user_id = ? AND v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (user_id, _pack(query_embedding), k))
        return [Recall(r["id"], r["user_id"], r["content"],
                       json.loads(r["metadata_json"])) for r in rows]

    async def get_facts(self, user_id) -> list[Fact]:
        rows = await self._conn.execute_fetchall(
            "SELECT key, value FROM facts WHERE user_id = ? ORDER BY key", (user_id,))
        return [Fact(user_id, r["key"], r["value"]) for r in rows]

    async def upsert_fact(self, user_id, key, value) -> None:
        await self._conn.execute(
            "INSERT INTO facts (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at", (user_id, key, value, _now()))
        await self._conn.commit()

    async def get_summary(self, user_id) -> Summary | None:
        rows = await self._conn.execute_fetchall(
            "SELECT content FROM summaries WHERE user_id = ?", (user_id,))
        return Summary(user_id, rows[0]["content"]) if rows else None

    async def upsert_summary(self, user_id, content) -> None:
        await self._conn.execute(
            "INSERT INTO summaries (user_id, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET content=excluded.content, "
            "updated_at=excluded.updated_at", (user_id, content, _now()))
        await self._conn.commit()
```

> **Note on `connect(":memory:")`:** the fixture passes `embedding_dim=4` implicitly by constructing the adapter with 4, but `connect` defaults the vec table to 1024. For the test, call `connect(":memory:")` then create the vec table at dim 4 — simplest fix: add an `embedding_dim` arg to `connect` and pass `connect(":memory:", embedding_dim=4)` in the fixture. Update the fixture accordingly if the dims must match.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_sqlite_memory.py -v`
Expected: PASS (adjust `connect` dim in the fixture to 4 as noted)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/persistence src/ari/infrastructure/memory/__init__.py src/ari/infrastructure/memory/sqlite_memory_adapter.py tests/infrastructure/test_sqlite_memory.py
git commit -m "feat: add SQLite+sqlite-vec memory adapter with user-scoped recall"
```

---

### Task 8: Embeddings adapter (fastembed, multilingual)

**Files:**
- Create: `src/ari/infrastructure/memory/embeddings.py`
- Test: `tests/infrastructure/test_embeddings.py`

**Interfaces:**
- Produces: `FastEmbedEmbeddings(model_name: str)` implementing `EmbeddingsPort`; `embed` runs the (blocking) fastembed call in a thread via `asyncio.to_thread`; exposes `dim` after first use.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_embeddings.py
import pytest

from ari.infrastructure.memory.embeddings import FastEmbedEmbeddings


@pytest.mark.slow
async def test_embed_returns_vectors():
    emb = FastEmbedEmbeddings("intfloat/multilingual-e5-small")
    vectors = await emb.embed(["hola mundo", "how are you"])
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_embeddings.py -v -m slow`
Expected: FAIL with `ModuleNotFoundError` for `embeddings`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/memory/embeddings.py
import asyncio

from fastembed import TextEmbedding


class FastEmbedEmbeddings:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-large"):
        self._model = TextEmbedding(model_name=model_name)
        self.dim: int | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await asyncio.to_thread(lambda: list(self._model.embed(texts)))
        result = [v.tolist() for v in vectors]
        if result:
            self.dim = len(result[0])
        return result
```

Register the `slow` marker in `pyproject.toml` under `[tool.pytest.ini_options]`:
`markers = ["slow: downloads a model / hits the network"]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_embeddings.py -v -m slow`
Expected: PASS (downloads the small model on first run)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/memory/embeddings.py tests/infrastructure/test_embeddings.py pyproject.toml
git commit -m "feat: add fastembed multilingual embeddings adapter"
```

---

### Task 9: Anthropic LLM adapter (with retries)

**Files:**
- Create: `src/ari/infrastructure/llm/__init__.py`
- Create: `src/ari/infrastructure/llm/anthropic_adapter.py`
- Test: `tests/infrastructure/test_anthropic_adapter.py`

**Interfaces:**
- Produces: `AnthropicAdapter(api_key: str, model: str, max_retries: int = 3)` implementing `LLMPort`. Maps `Message` list to Anthropic `messages`; system prompt passed separately; retries on `APIStatusError` (429/529) with exponential backoff.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_anthropic_adapter.py
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from ari.infrastructure.llm.anthropic_adapter import AnthropicAdapter
from ari.domain.agent.message import Message


def _reply_obj(text):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


async def test_complete_maps_messages_and_returns_text(monkeypatch):
    adapter = AnthropicAdapter(api_key="k", model="claude-sonnet-4-6")
    adapter._client.messages.create = AsyncMock(return_value=_reply_obj("hi there"))
    msgs = [Message("u1", "user", "hola", datetime.now(timezone.utc))]

    out = await adapter.complete("system text", msgs)

    assert out == "hi there"
    kwargs = adapter._client.messages.create.call_args.kwargs
    assert kwargs["system"] == "system text"
    assert kwargs["messages"] == [{"role": "user", "content": "hola"}]
    assert kwargs["model"] == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_anthropic_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError` for `anthropic_adapter`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/llm/anthropic_adapter.py
import asyncio
import logging

from anthropic import AsyncAnthropic, APIStatusError

from ari.domain.agent.message import Message

log = logging.getLogger("ari.anthropic")


class AnthropicAdapter:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 max_retries: int = 3):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_retries = max_retries

    async def complete(self, system: str, messages: list[Message],
                       max_tokens: int = 1024) -> str:
        payload = [{"role": m.role, "content": m.content} for m in messages]
        delay = 1.0
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._client.messages.create(
                    model=self._model, system=system, messages=payload,
                    max_tokens=max_tokens)
                return "".join(b.text for b in resp.content if hasattr(b, "text"))
            except APIStatusError as exc:
                if exc.status_code not in (429, 529) or attempt == self._max_retries:
                    raise
                log.warning("Anthropic %s; retry %s/%s", exc.status_code,
                            attempt, self._max_retries)
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_anthropic_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/llm tests/infrastructure/test_anthropic_adapter.py
git commit -m "feat: add Anthropic LLM adapter with backoff retries"
```

---

### Task 10: Telegram gateway adapter

**Files:**
- Create: `src/ari/infrastructure/gateway/__init__.py`
- Create: `src/ari/infrastructure/gateway/telegram_adapter.py`
- Test: `tests/infrastructure/test_telegram_adapter.py`

**Interfaces:**
- Produces: `TelegramAdapter(token: str)` implementing `GatewayPort`; helpers `to_incoming(update) -> IncomingMessage | None` (returns `None` for non-text/empty updates) and `split_text(text, limit=4096) -> list[str]` (static). `start(handler)` registers a text handler and runs polling.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_telegram_adapter.py
from types import SimpleNamespace

from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter


def _update(text, user_id=42, chat_id=99):
    msg = SimpleNamespace(text=text, chat_id=chat_id,
                          from_user=SimpleNamespace(id=user_id))
    return SimpleNamespace(message=msg, effective_message=msg)


def test_to_incoming_maps_text():
    inc = TelegramAdapter.to_incoming(_update("hola"))
    assert inc.user_id == "42"
    assert inc.chat_id == "99"
    assert inc.text == "hola"


def test_to_incoming_skips_non_text():
    assert TelegramAdapter.to_incoming(_update(None)) is None
    assert TelegramAdapter.to_incoming(SimpleNamespace(
        message=None, effective_message=None)) is None


def test_split_text_respects_limit():
    parts = TelegramAdapter.split_text("x" * 9000, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == "x" * 9000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infrastructure/test_telegram_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError` for `telegram_adapter`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/gateway/telegram_adapter.py
import logging

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from ari.domain.ports.gateway_port import Handler, IncomingMessage

log = logging.getLogger("ari.telegram")
TELEGRAM_LIMIT = 4096


class TelegramAdapter:
    def __init__(self, token: str):
        self._app = Application.builder().token(token).build()

    @staticmethod
    def to_incoming(update) -> IncomingMessage | None:
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if msg is None or not getattr(msg, "text", None):
            return None
        return IncomingMessage(
            user_id=str(msg.from_user.id), chat_id=str(msg.chat_id), text=msg.text)

    @staticmethod
    def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
        return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]

    async def start(self, handler: Handler) -> None:
        async def _on_message(update: Update, _context) -> None:
            incoming = self.to_incoming(update)
            if incoming is None:
                log.info("skipping non-text update")
                return
            out = await handler(incoming)
            for part in self.split_text(out.text):
                await update.effective_message.reply_text(part)

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
        self._app.run_polling()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/infrastructure/test_telegram_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/gateway tests/infrastructure/test_telegram_adapter.py
git commit -m "feat: add Telegram gateway adapter with message splitting"
```

---

### Task 11: Memory maintainer (fact extraction + summarization)

**Files:**
- Create: `src/ari/application/memory_maintainer.py`
- Test: `tests/application/test_memory_maintainer.py`

**Interfaces:**
- Consumes: `LLMPort`, `MemoryPort`.
- Produces: `MemoryMaintainer(memory, llm, summary_threshold=40)` with `async def extract_facts(user_id, user_text) -> None` (asks LLM for `key: value` lines, upserts each) and `async def maybe_summarize(user_id) -> None` (summarizes when message count exceeds threshold). Both are best-effort (never raise to the caller).

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_memory_maintainer.py
from ari.application.memory_maintainer import MemoryMaintainer
from tests.fakes import FakeLLM, FakeMemory


async def test_extract_facts_parses_and_upserts():
    mem = FakeMemory()
    llm = FakeLLM(reply="name: Gabo\ncity: Guayaquil\ngarbage line")
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "me llamo Gabo, vivo en Guayaquil")
    facts = {f.key: f.value for f in await mem.get_facts("u1")}
    assert facts == {"name": "Gabo", "city": "Guayaquil"}


async def test_extract_facts_never_raises_on_llm_error():
    class Boom(FakeLLM):
        async def complete(self, *a, **k):
            raise RuntimeError("down")

    maint = MemoryMaintainer(FakeMemory(), Boom())
    await maint.extract_facts("u1", "hi")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_memory_maintainer.py -v`
Expected: FAIL with `ModuleNotFoundError` for `memory_maintainer`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/memory_maintainer.py
import logging

from ari.domain.agent.message import Message
from ari.domain.memory.memory_port import MemoryPort
from ari.domain.ports.llm_port import LLMPort

log = logging.getLogger("ari.memory_maintainer")

_FACT_SYSTEM = (
    "Extract durable facts about the user from their message. "
    "Reply ONLY with lines 'key: value'. If none, reply with nothing.")


class MemoryMaintainer:
    def __init__(self, memory: MemoryPort, llm: LLMPort, summary_threshold: int = 40):
        self._memory = memory
        self._llm = llm
        self._threshold = summary_threshold

    async def extract_facts(self, user_id: str, user_text: str) -> None:
        try:
            from datetime import datetime, timezone
            probe = [Message(user_id, "user", user_text, datetime.now(timezone.utc))]
            raw = await self._llm.complete(_FACT_SYSTEM, probe, max_tokens=256)
            for line in raw.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    key, value = key.strip(), value.strip()
                    if key and value:
                        await self._memory.upsert_fact(user_id, key, value)
        except Exception:
            log.exception("fact extraction failed")

    async def maybe_summarize(self, user_id: str) -> None:
        try:
            history = await self._memory.recent_messages(user_id, self._threshold + 1)
            if len(history) <= self._threshold:
                return
            joined = "\n".join(f"{m.role}: {m.content}" for m in history)
            probe = [Message(user_id, "user", joined, history[-1].created_at)]
            summary = await self._llm.complete(
                "Summarize this conversation in a few sentences.", probe, max_tokens=512)
            await self._memory.upsert_summary(user_id, summary)
        except Exception:
            log.exception("summarization failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/application/test_memory_maintainer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/memory_maintainer.py tests/application/test_memory_maintainer.py
git commit -m "feat: add background fact extraction and summarization"
```

---

### Task 12: Composition root (main.py) + background wiring

**Files:**
- Create: `src/ari/main.py`
- Modify: `src/ari/application/handle_message.py` (accept optional `maintainer` and schedule its calls as background tasks)
- Test: `tests/application/test_handle_message_background.py`

**Interfaces:**
- Consumes: everything.
- Produces: `build_handler(settings) -> (HandleMessage, TelegramAdapter, conn)` factory used by `main()`; `HandleMessage` gains `maintainer: MemoryMaintainer | None = None` and, after replying, schedules `extract_facts` + `maybe_summarize` via `asyncio.create_task` (or a provided scheduler for tests).

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_handle_message_background.py
from ari.application.handle_message import HandleMessage
from ari.application.memory_maintainer import MemoryMaintainer
from ari.domain.agent.agent_service import AgentService
from ari.domain.ports.gateway_port import IncomingMessage
from tests.fakes import FakeEmbeddings, FakeLLM, FakeMemory


async def test_background_extracts_facts_via_scheduler():
    mem = FakeMemory()
    scheduled = []
    handler = HandleMessage(
        memory=mem, llm=FakeLLM(reply="name: Gabo"), embeddings=FakeEmbeddings(),
        agent=AgentService(),
        maintainer=MemoryMaintainer(mem, FakeLLM(reply="name: Gabo")),
        scheduler=lambda coro: scheduled.append(coro),
    )
    await handler(IncomingMessage("u1", "c1", "me llamo Gabo"))
    for coro in scheduled:  # run what would have been background tasks
        await coro
    facts = {f.key: f.value for f in await mem.get_facts("u1")}
    assert facts.get("name") == "Gabo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_handle_message_background.py -v`
Expected: FAIL — `HandleMessage` has no `maintainer` / `scheduler` params

- [ ] **Step 3: Write minimal implementation**

Extend `HandleMessage.__init__` to accept `maintainer: MemoryMaintainer | None = None` and `scheduler=None` (default schedules with `asyncio.create_task`). After building the reply and before returning, if `maintainer` is set, schedule both maintenance coroutines:

```python
# additions to src/ari/application/handle_message.py
import asyncio  # add to imports

# in __init__ signature add:  maintainer=None, scheduler=None
        self._maintainer = maintainer
        self._schedule = scheduler or (lambda coro: asyncio.create_task(coro))

# just before `return OutgoingMessage(...)`:
        if self._maintainer is not None:
            self._schedule(self._maintainer.extract_facts(incoming.user_id, text))
            self._schedule(self._maintainer.maybe_summarize(incoming.user_id))
```

```python
# src/ari/main.py
import asyncio
import logging

from ari.application.handle_message import HandleMessage
from ari.application.memory_maintainer import MemoryMaintainer
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter
from ari.infrastructure.llm.anthropic_adapter import AnthropicAdapter
from ari.infrastructure.memory.embeddings import FastEmbedEmbeddings
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect

logging.basicConfig(level=logging.INFO)


async def build(settings: Settings):
    embeddings = FastEmbedEmbeddings(settings.embedding_model)
    dim = len((await embeddings.embed(["probe"]))[0])
    conn = await connect(settings.db_path, embedding_dim=dim)
    memory = SqliteMemoryAdapter(conn, embedding_dim=dim)
    llm = AnthropicAdapter(settings.anthropic_api_key, settings.model)
    handler = HandleMessage(
        memory=memory, llm=llm, embeddings=embeddings, agent=AgentService(),
        working_memory_size=settings.working_memory_size,
        recall_top_k=settings.recall_top_k,
        maintainer=MemoryMaintainer(memory, llm))
    return handler, TelegramAdapter(settings.telegram_bot_token), conn


def main() -> None:
    settings = Settings()
    handler, gateway, _conn = asyncio.get_event_loop().run_until_complete(
        build(settings))
    asyncio.get_event_loop().run_until_complete(gateway.start(handler))


if __name__ == "__main__":
    main()
```

> **Note:** `connect` needs an `embedding_dim` parameter (added in Task 7). Ensure the vec table is created with the runtime embedding dimension discovered from the model, so stored vectors match.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/application/test_handle_message_background.py -v` then the full suite `pytest -v` (excluding slow: `pytest -v -m "not slow"`).
Expected: PASS

- [ ] **Step 5: Manual smoke test + commit**

Manual: create `.env` from `.env.example` with real `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN`, run `python -m ari.main`, message the bot on Telegram, confirm a reply and that a stated fact is recalled in a new chat.

```bash
git add src/ari/main.py src/ari/application/handle_message.py tests/application/test_handle_message_background.py
git commit -m "feat: wire composition root and background memory maintenance"
```

---

## Self-Review Notes

- **Spec coverage:** §3 layout → Tasks 2–12; §4 memory layers → working (Task 7/5), episodic (Task 7 + retrieval in 5), facts (Task 11), summarization (Task 11); §5 flow → Task 5 + 12; §6 config → Task 6; §7 resilience → Tasks 5 (degradation), 9 (retries), 10 (splitting/skip); §8 testing → every task; §10 acceptance → covered by Tasks 5,7,9,10,12 tests + manual smoke.
- **Import boundary** enforced by test in Task 3.
- **User isolation** (Review Focus #1) pinned in Task 7 `test_recall_search_is_user_scoped`.
- **Blank input** (#2) in Task 5; **oversized reply** (#3) in Task 10; **retrieval failure** (#4) in Task 5; **non-text update** (#5) in Task 10.
- **Embedding dimension consistency:** `connect` must accept `embedding_dim` and the vec table must be created at the model's true dim (noted in Tasks 7 and 12).
