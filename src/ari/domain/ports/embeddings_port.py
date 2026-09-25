from typing import Protocol


class EmbeddingsPort(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
