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
