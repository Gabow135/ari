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


class _ContextOnly:
    async def context(self, user_id):
        return "## CONTEXTO-AGENDA"


class _BrokenContextActions:
    async def context(self, user_id):
        raise RuntimeError("db is down")


async def test_context_read_failure_does_not_break_the_reply():
    llm = FakeLLM(reply="hello back")
    handler = HandleMessage(memory=FakeMemory(), llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), actions=_BrokenContextActions())
    out = await handler(IncomingMessage("u1", "c1", "hola"))
    assert out.text == "hello back"


async def test_actions_context_in_prompt_and_legacy_blocks_hidden():
    llm = FakeLLM(reply='hecho <ari-action>{"type":"cancel","id":1}</ari-action>')
    handler = HandleMessage(memory=FakeMemory(), llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), actions=_ContextOnly())
    out = await handler(IncomingMessage("u1", "c1", "hola"))
    assert "## CONTEXTO-AGENDA" in llm.calls[0][0]
    assert out.text == "hecho"


from ari.domain.ports.llm_port import LLMTimeoutError
from ari.domain.tools.toolset import Toolset, ToolsView


from contextlib import asynccontextmanager

from ari.domain.tools.toolset import Turn


class _FakePolicy:
    def __init__(self):
        self.turns = []

    @asynccontextmanager
    async def turn(self, user_id, context="chat", actor_name="", chat_id=None):
        self.turns.append((user_id, context, actor_name, chat_id))
        yield Turn(Toolset(("WebSearch",), ("WebSearch",), f"/cfg/{user_id}.json"),
                   ToolsView(f"## Tus herramientas y conexiones\n- 🌐 web ({user_id})",
                             True, False), f"turn-{user_id}")


class _FakeTurnLog:
    def __init__(self, receipts):
        self._r = receipts

    async def receipts(self, turn_id):
        return self._r.get(turn_id, [])


class _RecordingMaintainer:
    def __init__(self, llm):
        self._llm = llm

    async def extract_facts(self, user_id, text):
        await self._llm.complete("facts", [])

    async def maybe_summarize(self, user_id):
        return None


async def test_chat_uses_sender_toolset_and_view_but_maintenance_gets_none():
    llm = FakeLLM(reply="hola")
    ran = []
    policy = _FakePolicy()
    handler = HandleMessage(memory=FakeMemory(), llm=llm, embeddings=FakeEmbeddings(),
                            agent=AgentService(), tools=policy,
                            maintainer=_RecordingMaintainer(llm),
                            scheduler=lambda coro: ran.append(coro))
    await handler(IncomingMessage("u9", "c9", "busca algo", display_name="Ana"))
    for coro in ran:
        await coro
    assert policy.turns == [("u9", "chat", "Ana", "c9")]
    assert llm.toolsets[0].mcp_config_path == "/cfg/u9.json"
    assert "🌐 web (u9)" in llm.calls[0][0]
    assert llm.toolsets[1:] == [None]


async def test_receipts_appended_and_after_turn_called():
    flushed = []

    async def after_turn():
        flushed.append(True)

    handler = HandleMessage(memory=FakeMemory(), llm=FakeLLM(reply="Listo."),
                            embeddings=FakeEmbeddings(), agent=AgentService(),
                            tools=_FakePolicy(),
                            turn_log=_FakeTurnLog({"turn-u1": ["✅ Recordatorio #1: x — sáb 26/09 09:00"]}),
                            after_turn=after_turn)
    out = await handler(IncomingMessage("u1", "c1", "recuérdame x"))
    assert out.text == "Listo.\n\n✅ Recordatorio #1: x — sáb 26/09 09:00"
    assert flushed == [True]


async def test_timeout_after_a_tool_ran_still_shows_receipt_and_flushes():
    class SlowLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise LLMTimeoutError("claude timed out after 180s")

    flushed = []

    async def after_turn():
        flushed.append(True)

    from ari.application.handle_message import TIMEOUT_REPLY
    out = await HandleMessage(memory=FakeMemory(), llm=SlowLLM(), embeddings=FakeEmbeddings(),
                              agent=AgentService(), tools=_FakePolicy(),
                              turn_log=_FakeTurnLog({"turn-u1": ["🧠 Guardé: a = b"]}),
                              after_turn=after_turn)(IncomingMessage("u1", "c1", "hola"))
    assert out.text == f"{TIMEOUT_REPLY}\n\n🧠 Guardé: a = b"
    assert flushed == [True]


async def test_scheduled_run_uses_task_context():
    policy = _FakePolicy()
    await HandleMessage(memory=FakeMemory(), llm=FakeLLM(reply="ok"), embeddings=FakeEmbeddings(),
                        agent=AgentService(), tools=policy)(
        IncomingMessage("u1", "c1", "resumen"), allow_actions=False)
    assert policy.turns[0][1] == "task"


async def test_llm_error_after_a_tool_ran_still_shows_receipt_and_flushes():
    class BrokenLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise RuntimeError("claude CLI returned an error")

    flushed = []

    async def after_turn():
        flushed.append(True)

    out = await HandleMessage(memory=FakeMemory(), llm=BrokenLLM(), embeddings=FakeEmbeddings(),
                              agent=AgentService(), tools=_FakePolicy(),
                              turn_log=_FakeTurnLog({"turn-u1": ["🧠 Guardé: a = b"]}),
                              after_turn=after_turn)(IncomingMessage("u1", "c1", "hola"))
    assert out.text.startswith("Tuve un problema")
    assert "🧠 Guardé: a = b" in out.text
    assert flushed == [True]


async def test_llm_error_without_receipts_propagates_but_still_flushes():
    class BrokenLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise RuntimeError("claude CLI returned an error")

    flushed = []

    async def after_turn():
        flushed.append(True)

    handler = HandleMessage(memory=FakeMemory(), llm=BrokenLLM(), embeddings=FakeEmbeddings(),
                            agent=AgentService(), tools=_FakePolicy(),
                            turn_log=_FakeTurnLog({}), after_turn=after_turn)
    with pytest.raises(RuntimeError):
        await handler(IncomingMessage("u1", "c1", "hola"))
    assert flushed == [True]


async def test_task_timeout_reraises_and_still_flushes():
    class SlowLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise LLMTimeoutError("claude timed out after 180s")

    flushed = []

    async def after_turn():
        flushed.append(True)

    handler = HandleMessage(memory=FakeMemory(), llm=SlowLLM(), embeddings=FakeEmbeddings(),
                            agent=AgentService(), tools=_FakePolicy(), after_turn=after_turn)
    with pytest.raises(LLMTimeoutError):
        await handler(IncomingMessage("u1", "c1", "hola"), allow_actions=False)
    assert flushed == [True]


async def test_after_turn_failure_does_not_break_the_reply():
    async def after_turn():
        raise RuntimeError("outbox flush boom")

    handler = HandleMessage(memory=FakeMemory(), llm=FakeLLM(reply="hola"),
                            embeddings=FakeEmbeddings(), agent=AgentService(),
                            tools=_FakePolicy(), after_turn=after_turn)
    out = await handler(IncomingMessage("u1", "c1", "hola"))
    assert out.text == "hola"


async def test_timeout_gives_a_reply_instead_of_silence():
    class SlowLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise LLMTimeoutError("claude timed out after 180s")

    from ari.application.handle_message import TIMEOUT_REPLY
    out = await HandleMessage(memory=FakeMemory(), llm=SlowLLM(), embeddings=FakeEmbeddings(),
                              agent=AgentService())(IncomingMessage("u1", "c1", "hola"))
    assert out.text == TIMEOUT_REPLY
    assert "Me tardé demasiado" in TIMEOUT_REPLY


async def test_timeout_reraises_for_scheduled_tasks_so_they_count_as_failures():
    class SlowLLM(FakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            raise LLMTimeoutError("claude timed out after 180s")

    handler = HandleMessage(memory=FakeMemory(), llm=SlowLLM(), embeddings=FakeEmbeddings(),
                            agent=AgentService())
    with pytest.raises(LLMTimeoutError):
        await handler(IncomingMessage("u1", "c1", "hola"), allow_actions=False)
