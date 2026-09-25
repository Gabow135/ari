from datetime import datetime, timezone

import pytest

from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.domain.agent.message import Message


def _msg(role, content):
    return Message("u1", role, content, datetime.now(timezone.utc))


async def test_complete_renders_prompt_and_parses_result():
    captured = {}

    async def fake_runner(system, prompt, model):
        captured["system"] = system
        captured["prompt"] = prompt
        captured["model"] = model
        return '{"result": "hola de vuelta", "is_error": false}'

    adapter = ClaudeCodeCliAdapter(model="claude-sonnet-4-6", runner=fake_runner)
    out = await adapter.complete("system text", [_msg("user", "hola"), _msg("assistant", "hi"), _msg("user", "cómo estás")])

    assert out == "hola de vuelta"
    assert captured["system"] == "system text"
    assert captured["model"] == "claude-sonnet-4-6"
    # history is serialized with speaker labels, in order
    assert "User: hola" in captured["prompt"]
    assert "Assistant: hi" in captured["prompt"]
    assert captured["prompt"].strip().endswith("User: cómo estás")


async def test_complete_raises_on_cli_error():
    async def err_runner(system, prompt, model):
        return '{"result": "boom", "is_error": true}'

    adapter = ClaudeCodeCliAdapter(runner=err_runner)
    with pytest.raises(RuntimeError):
        await adapter.complete("s", [_msg("user", "hi")])


@pytest.mark.slow
async def test_complete_real_cli():
    """Integration test: calls the real claude CLI once. Requires CLI auth."""
    now = datetime.now(timezone.utc)
    adapter = ClaudeCodeCliAdapter()
    result = await adapter.complete(
        "You are concise. Reply with exactly: pong",
        [Message("u1", "user", "ping", now)],
    )
    assert result  # non-empty response


async def test_default_runner_isolates_chat_from_host_setup(monkeypatch):
    import ari.infrastructure.llm.claude_code_adapter as mod
    captured = {}

    async def fake_run_streaming(argv, stdin=None, **_kw):
        captured["argv"] = argv
        return '{"type": "result", "is_error": false, "result": "ok"}'

    monkeypatch.setattr(mod, "run_streaming", fake_run_streaming)
    adapter = ClaudeCodeCliAdapter(claude_bin="claude")
    assert await adapter.complete("sys", [Message("u", "user", "hi",
                                                  datetime.now(timezone.utc))]) == "ok"
    argv = captured["argv"]
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
