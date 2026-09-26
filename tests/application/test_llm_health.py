import pytest

from ari.application.schedule.llm_health import MonitoredLLM


class FlakyLLM:
    def __init__(self, script):
        self.script = list(script)

    async def complete(self, system, messages, max_tokens=1024):
        if self.script.pop(0):
            return "ok"
        raise RuntimeError("Not logged in")


class Listener:
    def __init__(self):
        self.events = []

    async def cli_failed(self, reason):
        self.events.append(("down", reason))

    async def cli_recovered(self):
        self.events.append(("up",))


async def _call(llm):
    try:
        return await llm.complete("s", [])
    except RuntimeError:
        return None


async def test_down_after_threshold_and_recovery_checked_on_success():
    llm = MonitoredLLM(FlakyLLM([False, False, False, False, True]), threshold=3)
    llm.listener = Listener()
    for _ in range(5):
        await _call(llm)
    downs = [e for e in llm.listener.events if e[0] == "down"]
    assert downs == [("down", "Not logged in")]
    assert llm.listener.events[-1] == ("up",)


async def test_errors_still_propagate_without_listener():
    llm = MonitoredLLM(FlakyLLM([False]))
    with pytest.raises(RuntimeError):
        await llm.complete("s", [])


async def test_toolset_is_forwarded():
    seen = []

    class Inner:
        async def complete(self, system, messages, max_tokens=1024, toolset=None):
            seen.append(toolset)
            return "ok"

    llm = MonitoredLLM(Inner())
    await llm.complete("s", [], toolset="T")
    await llm.complete("s", [])
    assert seen == ["T", None]
