import os
from abc import ABC, abstractmethod

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")

from sentence_transformers import SentenceTransformer

from backend.app.config.settings import settings


class EmbeddingModel(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def dimension(self) -> int: ...


class LocalEmbeddingModel(EmbeddingModel):
    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or settings.embedding_model_name
        self._model = SentenceTransformer(self._model_name)
        self._dimension = self._model.get_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def dimension(self) -> int:
        return self._dimension


_embedding_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = LocalEmbeddingModel()
    return _embedding_model
