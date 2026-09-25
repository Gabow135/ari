import json
import logging

from ari.domain.agent.message import Message
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


class ClaudeCodeCliAdapter:
    """LLMPort backed by the Claude Code CLI in headless mode.

    Uses the CLI's own authentication (e.g. a Claude Code subscription) — it
    does NOT require ANTHROPIC_API_KEY. The `claude` binary must be on PATH.

    Isolation: the default runner passes ``ISOLATION_ARGS`` so Claude has no
    tools (filesystem, Bash, web), no MCP connectors and none of the host
    user's hooks/plugins/skills — a pure text responder with Ari's prompt only.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        claude_bin: str = "claude",
        runner=None,
        cli_env: dict | None = None,
    ):
        self._model = model
        self._bin = resolve_claude_bin(claude_bin)
        self._runner = runner or self._default_runner
        self._cli_env = cli_env  # see infrastructure/claude_env.py

    async def complete(
        self, system: str, messages: list[Message], max_tokens: int = 1024
    ) -> str:
        prompt = self._render(messages)
        raw = await self._runner(system, prompt, self._model)
        data = json.loads(raw)
        text = data.get("result", "") or ""
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI returned an error: {text[:300]}")
        return text.strip()

    async def _default_runner(self, system: str, prompt: str, model: str) -> str:
        # --allowed-tools "" passes an empty whitelist so Claude has no tools
        # and cannot perform filesystem, Bash, or web actions.
        # stream-json: progress (thinking, partial text) is emitted as it arrives.
        return await run_streaming(
            [self._bin, "-p",
             "--model", model,
             "--system-prompt", system,
             *STREAM_ARGS,
             *ISOLATION_ARGS],
            stdin=prompt.encode(),
            env=self._cli_env,
        )

    @staticmethod
    def _render(messages: list[Message]) -> str:
        lines = []
        for m in messages:
            speaker = "User" if m.role == "user" else "Assistant"
            lines.append(f"{speaker}: {m.content}")
        return "\n".join(lines)
