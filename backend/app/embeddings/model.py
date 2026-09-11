"""Embedding abstraction.

Nothing outside this module knows which model is in use. The default is a
local sentence-transformers model so the project never requires a paid API key.
"""

import logging
import os
from abc import ABC, abstractmethod

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.app.config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)


class EmbeddingModel(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def dimension(self) -> int: ...


class LocalEmbeddingModel(EmbeddingModel):
    """sentence-transformers running locally on CPU or GPU."""

    def __init__(self, model_name: str | None = None, batch_size: int | None = None):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or settings.embedding_model_name
        self.batch_size = batch_size or settings.embedding_batch_size
        logger.info("Loading embedding model %s", self.model_name)
        self._model = SentenceTransformer(self.model_name)
        self._dimension = _embedding_dimension(self._model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return [vector.tolist() for vector in vectors]

    def dimension(self) -> int:
        return self._dimension


def _embedding_dimension(model) -> int:
    """Read the output dimension across sentence-transformers versions.

    The method was renamed from ``get_sentence_embedding_dimension`` to
    ``get_embedding_dimension`` in v5.
    """
    for attribute in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        getter = getattr(model, attribute, None)
        if callable(getter):
            return int(getter())
    raise RuntimeError("Could not determine embedding dimension")


class HashingEmbeddingModel(EmbeddingModel):
    """Deterministic dependency-free embeddings for tests.

    Character n-gram hashing into a fixed-size vector. Not competitive for
    retrieval quality, but it needs no model download and gives stable results.
    """

    def __init__(self, dimension: int = 256):
        self._dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        import math

        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dimension
            lowered = text.lower()
            for size in (3, 4):
                for index in range(max(len(lowered) - size + 1, 0)):
                    gram = lowered[index : index + size]
                    vector[hash(gram) % self._dimension] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    def dimension(self) -> int:
        return self._dimension


_embedding_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = LocalEmbeddingModel()
    return _embedding_model


def set_embedding_model(model: EmbeddingModel | None) -> None:
    """Override the process-wide model (used by tests and benchmarks)."""
    global _embedding_model
    _embedding_model = model
