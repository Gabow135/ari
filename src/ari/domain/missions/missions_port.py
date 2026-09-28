from abc import ABC, abstractmethod

from .entities import Mission


class MissionsPort(ABC):
    @abstractmethod
    async def add(self, user_id: str, chat_id: str, instruction: str) -> int: ...

    @abstractmethod
    async def get(self, mission_id: int) -> Mission | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[Mission]: ...

    @abstractmethod
    async def claim_pending(self) -> list[Mission]: ...

    @abstractmethod
    async def finish(self, mission_id: int, status: str,
                     result: str | None = None) -> None: ...

    @abstractmethod
    async def set_status(self, mission_id: int, status: str) -> None: ...

    @abstractmethod
    async def increment_failures(self, mission_id: int) -> int:
        """Increment failure count and return the new total."""
        ...
