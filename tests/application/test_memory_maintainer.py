from datetime import datetime, timezone

from ari.application.memory_maintainer import MemoryMaintainer
from ari.domain.agent.message import Message
from tests.fakes import FakeLLM, FakeMemory, MultiReplyFakeLLM


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


# ---------------------------------------------------------------------------
# T4 — conflict judge
# ---------------------------------------------------------------------------


async def test_new_key_stored_with_resolution_new():
    mem = FakeMemory()
    # first call = extraction reply, no judge needed for new key
    llm = FakeLLM(reply="city: Madrid")
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "vivo en Madrid")
    assert (await mem.get_fact("u1", "city")).value == "Madrid"
    history = await mem.get_fact_history("u1", "city")
    assert len(history) == 1
    assert history[0]["resolution"] == "new"
    assert history[0]["old_value"] is None


async def test_supersede_updates_fact_and_logs_history():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")
    # call 1: extraction returns "city: Barcelona"
    # call 2: judge returns SUPERSEDE
    llm = MultiReplyFakeLLM(["city: Barcelona", "SUPERSEDE"])
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "me mudé a Barcelona")
    assert (await mem.get_fact("u1", "city")).value == "Barcelona"
    history = await mem.get_fact_history("u1", "city")
    assert len(history) == 1
    assert history[0]["resolution"] == "supersede"
    assert history[0]["old_value"] == "Madrid"
    assert history[0]["new_value"] == "Barcelona"


async def test_keep_leaves_old_value_but_logs_history():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")
    llm = MultiReplyFakeLLM(["city: Barcelona", "KEEP"])
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "estuve de visita en Barcelona")
    assert (await mem.get_fact("u1", "city")).value == "Madrid"
    history = await mem.get_fact_history("u1", "city")
    assert len(history) == 1
    assert history[0]["resolution"] == "keep"


async def test_merge_stores_merged_value():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")
    merged_value = "Madrid y Barcelona"
    llm = MultiReplyFakeLLM(["city: Barcelona", f"MERGE\n{merged_value}"])
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "también vivo en Barcelona")
    assert (await mem.get_fact("u1", "city")).value == merged_value
    history = await mem.get_fact_history("u1", "city")
    assert history[0]["resolution"] == "merge"


async def test_judge_exception_defaults_to_keep_and_still_logs():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")

    class BoomOnSecond(MultiReplyFakeLLM):
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            self.calls.append((system, list(messages)))
            if len(self.calls) == 1:
                return "city: Barcelona"
            raise RuntimeError("judge down")

    maint = MemoryMaintainer(mem, BoomOnSecond(["city: Barcelona"]))
    await maint.extract_facts("u1", "text")  # must not raise
    assert (await mem.get_fact("u1", "city")).value == "Madrid"
    history = await mem.get_fact_history("u1", "city")
    assert len(history) == 1
    assert history[0]["resolution"] == "keep"


async def test_unrecognized_judge_verdict_defaults_to_keep():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")
    llm = MultiReplyFakeLLM(["city: Barcelona", "DUNNO"])
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "text")
    assert (await mem.get_fact("u1", "city")).value == "Madrid"
    history = await mem.get_fact_history("u1", "city")
    assert history[0]["resolution"] == "keep"


async def test_same_value_does_nothing():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")
    llm = FakeLLM(reply="city: Madrid")
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "sigo en Madrid")
    history = await mem.get_fact_history("u1", "city")
    assert history == []


async def test_existing_keys_included_in_extraction_prompt():
    mem = FakeMemory()
    await mem.upsert_fact("u1", "city", "Madrid")
    await mem.upsert_fact("u1", "name", "Gabo")
    llm = FakeLLM(reply="")
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "nothing new")
    system_used, _msgs = llm.calls[0]
    assert "city" in system_used
    assert "name" in system_used


async def test_key_normalization_in_extraction():
    """Keys from the LLM are normalized before storage."""
    mem = FakeMemory()
    llm = FakeLLM(reply="Ciudad Actual: Sevilla")
    maint = MemoryMaintainer(mem, llm)
    await maint.extract_facts("u1", "vivo en Sevilla")
    # The key should be normalized to "ciudad_actual"
    assert (await mem.get_fact("u1", "ciudad_actual")).value == "Sevilla"
