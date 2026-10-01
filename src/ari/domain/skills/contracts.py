from typing import Protocol, runtime_checkable

from ari.domain.ports.gateway_port import OutgoingMessage
from ari.domain.skills.models import Delivery, InboundContext, RawInbound


class SkillContext(Protocol):
    def secret(self, name: str) -> str: ...
    def optional_secret(self, name: str) -> str | None: ...
    @property
    def config(self) -> dict: ...
    @property
    def log(self): ...


@runtime_checkable
class InboundTransform(Protocol):
    async def on_inbound(self, raw: RawInbound, ctx: SkillContext) -> str | None: ...


@runtime_checkable
class OutboundTransform(Protocol):
    async def on_outbound(
        self, reply: OutgoingMessage, origin: InboundContext, ctx: SkillContext
    ) -> list[Delivery] | None: ...
