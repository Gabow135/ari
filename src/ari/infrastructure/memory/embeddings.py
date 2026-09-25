import asyncio

from fastembed import TextEmbedding


class FastEmbedEmbeddings:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-large"):
        self._model = TextEmbedding(model_name=model_name)
        self.dim: int | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await asyncio.to_thread(lambda: list(self._model.embed(texts)))
        result = [v.tolist() for v in vectors]
        if result:
            self.dim = len(result[0])
        return result
