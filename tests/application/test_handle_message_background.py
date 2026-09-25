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
    for coro in scheduled:   # run what would have been background tasks
        await coro
    facts = {f.key: f.value for f in await mem.get_facts("u1")}
    assert facts.get("name") == "Gabo"
