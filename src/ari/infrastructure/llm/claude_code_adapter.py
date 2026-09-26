import json
import logging

from ari.domain.agent.message import Message
from ari.domain.tools.toolset import Toolset
from ari.infrastructure.claude_bin import resolve_claude_bin
from ari.infrastructure.llm.stream_json import STREAM_ARGS, run_streaming

log = logging.getLogger("ari.claude_code")

# Ari's chat must see only Ari's prompt. Without these the CLI injects the host
# user's own setup into every reply: claude.ai connectors (Gmail, Drive…), their
# hooks/plugins (e.g. SessionStart instructions), skills, and tool/agent lists.
ISOLATION_ARGS = [
    "--tools", "",  # no built-in tools at all (not merely disallowed)
    "--strict-mcp-config",  # no MCP servers / claude.ai connectors
    "--setting-sources", "project",  # skip user-level hooks and plugins
    "--disable-slash-commands",  # no skills
]

_ISOLATION_KEEP = ["--strict-mcp-config", "--setting-sources", "project",
                   "--disable-slash-commands"]


def cli_tool_args(toolset: Toolset | None) -> list[str]:
    """CLI flags for a call's tools. No toolset = today's zero-tool isolation.
    With a toolset the host's setup stays excluded; only Ari's MCP config loads."""
    if toolset is None:
        return list(ISOLATION_ARGS)
    args = ["--tools", ",".join(toolset.builtin_tools),
            "--allowed-tools", ",".join(toolset.allowed_tools)]
    if toolset.mcp_config_path:
        args += ["--mcp-config", toolset.mcp_config_path]
    return args + _ISOLATION_KEEP


class ClaudeCodeCliAdapter:
    """LLMPort backed by the Claude Code CLI in headless mode.

    Uses the CLI's own authentication (e.g. a Claude Code subscription) — it
    does NOT require ANTHROPIC_API_KEY. The `claude` binary must be on PATH.

    Isolation: without a toolset the runner passes ``ISOLATION_ARGS`` (no
    tools at all); with one, only the toolset's built-ins and Ari's own MCP
    config are enabled — never the host user's connectors, hooks, plugins or
    skills.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        claude_bin: str = "claude",
        runner=None,
        cli_env: dict | None = None,
        timeout: float | None = None,
    ):
        self._model = model
        self._bin = resolve_claude_bin(claude_bin)
        self._runner = runner or self._default_runner
        self._cli_env = cli_env  # see infrastructure/claude_env.py
        self._timeout = timeout

    async def complete(
        self, system: str, messages: list[Message], max_tokens: int = 1024,
        toolset: Toolset | None = None
    ) -> str:
        prompt = self._render(messages)
        if toolset is None:
            raw = await self._runner(system, prompt, self._model)
        else:
            raw = await self._runner(system, prompt, self._model, toolset)
        data = json.loads(raw)
        text = data.get("result", "") or ""
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI returned an error: {text[:300]}")
        return text.strip()

    async def _default_runner(self, system: str, prompt: str, model: str,
                              toolset: Toolset | None = None) -> str:
        # stream-json: progress (thinking, tool steps, partial text) as it arrives.
        return await run_streaming(
            [self._bin, "-p",
             "--model", model,
             "--system-prompt", system,
             *STREAM_ARGS,
             *cli_tool_args(toolset)],
            stdin=prompt.encode(),
            env=self._cli_env,
            timeout=self._timeout,
        )

    @staticmethod
    def _render(messages: list[Message]) -> str:
        lines = []
        for m in messages:
            speaker = "User" if m.role == "user" else "Assistant"
            lines.append(f"{speaker}: {m.content}")
        return "\n".join(lines)
