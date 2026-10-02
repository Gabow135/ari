from datetime import UTC, datetime

from ari.domain.agent.message import Message
from ari.domain.memory.entities import Fact, Recall, Summary


class FakeLLM:
    def __init__(self, reply: str = "ok"):
        self.reply = reply
        self.calls: list[tuple[str, list[Message]]] = []
        self.toolsets: list = []

    async def complete(self, system, messages, max_tokens=1024, toolset=None) -> str:
        self.calls.append((system, list(messages)))
        self.toolsets.append(toolset)
        return self.reply


class MultiReplyFakeLLM:
    """FakeLLM that returns different replies per call (in order), then repeats the last."""

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls: list[tuple[str, list[Message]]] = []

    async def complete(self, system, messages, max_tokens=1024, toolset=None) -> str:
        self.calls.append((system, list(messages)))
        idx = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[idx]


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
        self._history: list[dict] = []
        self.fail_retrieval = fail_retrieval

    async def recent_messages(self, user_id, limit):
        msgs = [m for m in self._messages if m.user_id == user_id]
        return msgs[-limit:]

    async def append_message(self, message):
        self._messages.append(message)

    async def store_recall(self, user_id, content, embedding, metadata):
        self._recalls.append(
            Recall(None, user_id, content, metadata, created_at=datetime.now(UTC))
        )

    async def retrieve_recalls(self, user_id, query_embedding, k):
        if self.fail_retrieval:
            raise RuntimeError("boom")
        return [r for r in self._recalls if r.user_id == user_id][:k]

    async def get_facts(self, user_id):
        return [f for (u, _), f in self._facts.items() if u == user_id]

    async def upsert_fact(self, user_id, key, value):
        self._facts[(user_id, key)] = Fact(user_id, key, value)

    async def delete_fact(self, user_id, key):
        return self._facts.pop((user_id, key), None) is not None

    async def get_fact(self, user_id: str, key: str):
        return self._facts.get((user_id, key))

    async def add_fact_history(
        self, user_id: str, key: str, old_value, new_value: str, resolution: str
    ) -> None:
        self._history.append(
            {"user_id": user_id, "key": key, "old_value": old_value,
             "new_value": new_value, "resolution": resolution}
        )

    async def get_fact_history(self, user_id: str, key: str) -> list[dict]:
        return [h for h in self._history if h["user_id"] == user_id and h["key"] == key]

    async def get_summary(self, user_id):
        return self._summaries.get(user_id)

    async def upsert_summary(self, user_id, content):
        self._summaries[user_id] = Summary(user_id, content)


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
