from typing import Protocol

from ari.domain.agent.message import Message
from ari.domain.tools.toolset import Toolset


class LLMTimeoutError(RuntimeError):
    """The model call exceeded its time budget (e.g. a slow tool)."""


class LLMPort(Protocol):
    async def complete(self, system: str, messages: list[Message],
                       max_tokens: int = 1024, toolset: Toolset | None = None) -> str: ...
