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
