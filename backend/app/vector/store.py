"""Qdrant-backed semantic index."""

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from backend.app.chunking.semantic import Chunk
from backend.app.config.settings import settings
from backend.app.embeddings.model import EmbeddingModel, get_embedding_model

logger = logging.getLogger(__name__)

#: Payload fields that queries can filter on.
INDEXED_PAYLOAD_FIELDS = ("repository_id", "language", "entity_type", "file_path")

UPSERT_BATCH_SIZE = 256


def _point_id(chunk: Chunk) -> str:
    """A stable id derived from the chunk's identity.

    Re-indexing the same repository overwrites the same points instead of
    duplicating them, and two repositories can never collide.
    """
    key = (
        f"{chunk.repository_id}|{chunk.file_path}|{chunk.entity_type}|"
        f"{chunk.entity_name}|{chunk.start_line}-{chunk.end_line}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class VectorStore:
    def __init__(
        self,
        embedding_model: EmbeddingModel | None = None,
        collection_name: str | None = None,
        client: QdrantClient | None = None,
    ):
        self.collection_name = collection_name or settings.qdrant_collection
        self.client = client or QdrantClient(
            host=settings.qdrant_host, port=settings.qdrant_port, timeout=60
        )
        self.embedding_model = embedding_model or get_embedding_model()
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        existing = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in existing:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.embedding_model.dimension(), distance=Distance.COSINE
                ),
            )
            logger.info("Created Qdrant collection %s", self.collection_name)

        for field in INDEXED_PAYLOAD_FIELDS:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass  # index already exists

    def store_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0

        stored = 0
        for start in range(0, len(chunks), UPSERT_BATCH_SIZE):
            batch = chunks[start : start + UPSERT_BATCH_SIZE]
            embeddings = self.embedding_model.embed([c.embedding_text() for c in batch])
            points = [
                PointStruct(
                    id=_point_id(chunk),
                    vector=embedding,
                    payload={
                        "repository_id": chunk.repository_id,
                        "file_path": chunk.file_path,
                        "language": chunk.language,
                        "entity_type": chunk.entity_type,
                        "entity_name": chunk.entity_name,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "content": chunk.content,
                        **{f"meta_{k}": v for k, v in (chunk.metadata or {}).items()},
                    },
                )
                for chunk, embedding in zip(batch, embeddings, strict=True)
            ]
            self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
            stored += len(points)

        logger.info("Stored %d chunks in Qdrant", stored)
        return stored

    def search(
        self,
        query: str,
        repository_id: str | None = None,
        limit: int = 10,
        language: str | None = None,
        entity_types: list[str] | None = None,
        file_path: str | None = None,
    ) -> list[dict]:
        query_vector = self.embedding_model.embed([query])
        if not query_vector:
            return []

        conditions = []
        if repository_id:
            conditions.append(
                FieldCondition(key="repository_id", match=MatchValue(value=repository_id))
            )
        if language:
            conditions.append(FieldCondition(key="language", match=MatchValue(value=language)))
        if entity_types:
            conditions.append(FieldCondition(key="entity_type", match=MatchAny(any=entity_types)))
        if file_path:
            conditions.append(FieldCondition(key="file_path", match=MatchValue(value=file_path)))

        response = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector[0],
            query_filter=Filter(must=conditions) if conditions else None,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "score": float(hit.score),
                "file_path": hit.payload.get("file_path", ""),
                "entity_type": hit.payload.get("entity_type", ""),
                "entity_name": hit.payload.get("entity_name", ""),
                "start_line": int(hit.payload.get("start_line", 0)),
                "end_line": int(hit.payload.get("end_line", 0)),
                "content": hit.payload.get("content", ""),
                "language": hit.payload.get("language", ""),
            }
            for hit in response.points
        ]

    def delete_repository(self, repository_id: str) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="repository_id", match=MatchValue(value=repository_id))]
            ),
            wait=True,
        )

    def count(self, repository_id: str | None = None) -> int:
        query_filter = (
            Filter(
                must=[FieldCondition(key="repository_id", match=MatchValue(value=repository_id))]
            )
            if repository_id
            else None
        )
        return int(
            self.client.count(
                collection_name=self.collection_name, count_filter=query_filter, exact=True
            ).count
        )
