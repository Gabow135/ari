from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    user_id: str
    chat_id: str
    text: str


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    chat_id: str
    text: str


Handler = Callable[[IncomingMessage], Awaitable[OutgoingMessage]]


class GatewayPort(Protocol):
    async def start(self, handler: Handler) -> None: ...
