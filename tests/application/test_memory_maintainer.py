from datetime import datetime, timezone

from ari.application.memory_maintainer import MemoryMaintainer
from ari.domain.agent.message import Message
from tests.fakes import FakeLLM, FakeMemory


def _msg(user_id, role, content):
    return Message(user_id, role, content, datetime.now(timezone.utc))


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


async def test_maybe_summarize_below_threshold_does_nothing():
    mem = FakeMemory()
    llm = FakeLLM(reply="summary")
    maint = MemoryMaintainer(mem, llm, summary_threshold=5)
    for i in range(5):
        await mem.append_message(_msg("u1", "user", f"hi {i}"))
    await maint.maybe_summarize("u1")
    assert await mem.get_summary("u1") is None
    assert llm.calls == []


async def test_maybe_summarize_incorporates_prior_summary():
    """When a previous summary exists, it must appear in the LLM prompt input."""
    mem = FakeMemory()
    llm = FakeLLM(reply="updated summary")
    maint = MemoryMaintainer(mem, llm, summary_threshold=5)

    # Seed a prior summary.
    await mem.upsert_summary("u1", "PRIOR_SUMMARY_SENTINEL")

    # Add enough messages to cross the threshold.
    for i in range(6):
        await mem.append_message(_msg("u1", "user", f"message {i}"))

    await maint.maybe_summarize("u1")

    # LLM must have been called exactly once.
    assert len(llm.calls) == 1
    _system, messages = llm.calls[0]

    # The user message content fed to the LLM must contain the prior summary text.
    combined_content = " ".join(m.content for m in messages)
    assert "PRIOR_SUMMARY_SENTINEL" in combined_content

    # The updated summary must be stored.
    stored = await mem.get_summary("u1")
    assert stored is not None
    assert stored.content == "updated summary"


async def test_maybe_summarize_no_prior_summary_still_works():
    """Without a previous summary the prompt must not crash and summary is stored."""
    mem = FakeMemory()
    llm = FakeLLM(reply="first summary")
    maint = MemoryMaintainer(mem, llm, summary_threshold=3)

    for i in range(4):
        await mem.append_message(_msg("u1", "user", f"msg {i}"))

    await maint.maybe_summarize("u1")

    assert len(llm.calls) == 1
    stored = await mem.get_summary("u1")
    assert stored is not None
    assert stored.content == "first summary"


async def test_maybe_summarize_never_raises_on_llm_error():
    class Boom(FakeLLM):
        async def complete(self, *a, **k):
            raise RuntimeError("down")

    mem = FakeMemory()
    maint = MemoryMaintainer(mem, Boom(), summary_threshold=2)
    for i in range(3):
        await mem.append_message(_msg("u1", "user", f"msg {i}"))
    await maint.maybe_summarize("u1")  # must not raise
