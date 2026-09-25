from typing import Protocol

from ari.domain.agent.message import Message


class LLMPort(Protocol):
    async def complete(self, system: str, messages: list[Message],
                       max_tokens: int = 1024) -> str: ...
