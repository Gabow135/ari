from dataclasses import dataclass, field

from ari.domain.agent.message import Message


@dataclass(slots=True)
class Conversation:
    user_id: str
    messages: list[Message] = field(default_factory=list)

    def add(self, message: Message) -> None:
        self.messages.append(message)

    def last(self, n: int) -> list[Message]:
        return self.messages[-n:] if n > 0 else []
