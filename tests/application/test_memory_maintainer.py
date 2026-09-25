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
