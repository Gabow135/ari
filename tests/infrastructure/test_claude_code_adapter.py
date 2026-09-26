from datetime import datetime, timezone

import pytest

from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.domain.agent.message import Message
from ari.domain.tools.toolset import WEB_TOOLS, Toolset
from ari.infrastructure.llm.claude_code_adapter import ISOLATION_ARGS, cli_tool_args


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


async def test_default_runner_passes_cli_env(monkeypatch):
    import ari.infrastructure.llm.claude_code_adapter as mod
    captured = {}

    async def fake_run_streaming(argv, stdin=None, env=None, **_kw):
        captured["env"] = env
        return '{"type": "result", "is_error": false, "result": "ok"}'

    monkeypatch.setattr(mod, "run_streaming", fake_run_streaming)
    adapter = ClaudeCodeCliAdapter(claude_bin="claude", cli_env={"CLAUDE_CONFIG_DIR": "x"})
    await adapter.complete("sys", [Message("u", "user", "hi", datetime.now(timezone.utc))])
    assert captured["env"] == {"CLAUDE_CONFIG_DIR": "x"}


def test_no_toolset_keeps_full_isolation():
    assert cli_tool_args(None) == ISOLATION_ARGS


def test_web_only_toolset_args():
    args = cli_tool_args(Toolset(WEB_TOOLS, WEB_TOOLS))
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--allowed-tools") + 1] == "WebSearch,WebFetch"
    assert "--mcp-config" not in args
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in args
    assert args[args.index("--setting-sources") + 1] == "project"


def test_mcp_toolset_args():
    t = Toolset((*WEB_TOOLS, "ToolSearch"), (*WEB_TOOLS, "ToolSearch", "mcp__google"),
                "/cfg/owner.json")
    args = cli_tool_args(t)
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch,ToolSearch"
    assert args[args.index("--allowed-tools") + 1] == "WebSearch,WebFetch,ToolSearch,mcp__google"
    assert args[args.index("--mcp-config") + 1] == "/cfg/owner.json"
    assert "--strict-mcp-config" in args


async def test_default_runner_passes_toolset_and_timeout(monkeypatch):
    import ari.infrastructure.llm.claude_code_adapter as mod
    captured = {}

    async def fake_run_streaming(argv, stdin=None, env=None, timeout=None, **_kw):
        captured.update(argv=argv, timeout=timeout)
        return '{"type": "result", "is_error": false, "result": "ok"}'

    monkeypatch.setattr(mod, "run_streaming", fake_run_streaming)
    adapter = ClaudeCodeCliAdapter(claude_bin="claude", timeout=180)
    t = Toolset(WEB_TOOLS, WEB_TOOLS)
    assert await adapter.complete("s", [_msg("user", "hi")], toolset=t) == "ok"
    assert captured["timeout"] == 180
    assert captured["argv"][captured["argv"].index("--tools") + 1] == "WebSearch,WebFetch"
    await adapter.complete("s", [_msg("user", "hi")])
    assert captured["argv"][captured["argv"].index("--tools") + 1] == ""


async def test_injected_three_arg_runner_still_works():
    async def runner(system, prompt, model):
        return '{"result": "ok", "is_error": false}'

    assert await ClaudeCodeCliAdapter(runner=runner).complete("s", [_msg("user", "hi")]) == "ok"
