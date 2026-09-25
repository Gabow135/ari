import asyncio
import json
import logging

from ari.domain.agent.message import Message
from ari.infrastructure.claude_bin import resolve_claude_bin

log = logging.getLogger("ari.claude_code")


class ClaudeCodeCliAdapter:
    """LLMPort backed by the Claude Code CLI in headless mode.

    Uses the CLI's own authentication (e.g. a Claude Code subscription) — it
    does NOT require ANTHROPIC_API_KEY. The `claude` binary must be on PATH.

    Tool isolation: the default runner passes ``--allowed-tools ""`` (empty
    whitelist), so Claude has no tools available and acts as a pure text
    responder.  This prevents any filesystem, Bash, or web actions.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        claude_bin: str = "claude",
        runner=None,
    ):
        self._model = model
        self._bin = resolve_claude_bin(claude_bin)
        self._runner = runner or self._default_runner

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
        proc = await asyncio.create_subprocess_exec(
            self._bin, "-p",
            "--model", model,
            "--system-prompt", system,
            "--output-format", "json",
            "--allowed-tools", "",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(prompt.encode())
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): "
                f"{err.decode(errors='replace')[:500]}"
            )
        return out.decode(errors="replace")

    @staticmethod
    def _render(messages: list[Message]) -> str:
        lines = []
        for m in messages:
            speaker = "User" if m.role == "user" else "Assistant"
            lines.append(f"{speaker}: {m.content}")
        return "\n".join(lines)
