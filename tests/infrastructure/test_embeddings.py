import pytest

from ari.infrastructure.memory.embeddings import FastEmbedEmbeddings


@pytest.mark.slow
async def test_embed_returns_vectors():
    emb = FastEmbedEmbeddings("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    vectors = await emb.embed(["hola mundo", "how are you"])
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0
