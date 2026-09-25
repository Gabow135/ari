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


async def test_prompt_uses_soul_and_role():
    llm = FakeLLM(reply="ok")
    handler = HandleMessage(
        memory=FakeMemory(), llm=llm, embeddings=FakeEmbeddings(), agent=AgentService(),
        soul=lambda: "Soy Ari, alma de test.", is_owner=lambda uid: uid == "boss")
    await handler(IncomingMessage("boss", "c1", "hola"))
    await handler(IncomingMessage("u2", "c2", "hola"))
    owner_prompt, user_prompt = llm.calls[0][0], llm.calls[1][0]
    assert "alma de test" in owner_prompt and "/restart" in owner_prompt
    assert "alma de test" in user_prompt and "/restart" not in user_prompt


class _FakeActions:
    def __init__(self):
        self.applied = []

    async def context(self, user_id):
        return "## CONTEXTO-AGENDA"

    async def apply(self, user_id, chat_id, reply, allow=True):
        self.applied.append((user_id, chat_id, allow))
        return reply.replace("<blk>", "") + " ✅"


async def test_actions_context_in_prompt_and_reply_processed():
    llm = FakeLLM(reply="hecho<blk>")
    acts = _FakeActions()
    mem = FakeMemory()
    handler = HandleMessage(memory=mem, llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), actions=acts)
    out = await handler(IncomingMessage("u1", "c1", "recuérdame algo"))
    assert "## CONTEXTO-AGENDA" in llm.calls[0][0]
    assert out.text == "hecho ✅"
    assert (await mem.recent_messages("u1", 10))[-1].content == "hecho ✅"
    await handler(IncomingMessage("u1", "c1", "tarea"), allow_actions=False)
    assert acts.applied == [("u1", "c1", True), ("u1", "c1", False)]
