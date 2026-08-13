from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from backend.app.chunking.semantic import Chunk
from backend.app.config.settings import settings
from backend.app.embeddings.model import EmbeddingModel, get_embedding_model

COLLECTION_NAME = "easycode_chunks"


class VectorStore:
    def __init__(self, embedding_model: EmbeddingModel | None = None):
        self.client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        self.embedding_model = embedding_model or get_embedding_model()
        self._ensure_collection()

    def _ensure_collection(self):
        collections = [c.name for c in self.client.get_collections().collections]
        if COLLECTION_NAME not in collections:
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=self.embedding_model.dimension(),
                    distance=Distance.COSINE,
                ),
            )

    def store_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0

        texts = [self._chunk_text(c) for c in chunks]
        embeddings = self.embedding_model.embed(texts)

        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            payload = {
                "repository_id": chunk.repository_id,
                "file_path": chunk.file_path,
                "language": chunk.language,
                "entity_type": chunk.entity_type,
                "entity_name": chunk.entity_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content": chunk.content,
            }
            if chunk.metadata:
                payload.update(chunk.metadata)
            points.append(PointStruct(id=i, vector=embedding, payload=payload))

        self.client.upsert(collection_name=COLLECTION_NAME, points=points)
        return len(points)

    def search(
        self,
        query: str,
        repository_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        query_embedding = self.embedding_model.embed([query])[0]

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        must_conditions = []
        if repository_id:
            must_conditions.append(
                FieldCondition(key="repository_id", match=MatchValue(value=repository_id))
            )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        results = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_embedding,
            query_filter=query_filter,
            limit=limit,
        )

        return [
            {
                "score": hit.score,
                "file_path": hit.payload["file_path"],
                "entity_type": hit.payload["entity_type"],
                "entity_name": hit.payload["entity_name"],
                "start_line": hit.payload["start_line"],
                "end_line": hit.payload["end_line"],
                "content": hit.payload.get("content", ""),
                "language": hit.payload.get("language", ""),
            }
            for hit in results.points
        ]

    def _chunk_text(self, chunk: Chunk) -> str:
        prefix = f"{chunk.entity_type} {chunk.entity_name} in {chunk.file_path}"
        return f"{prefix}\n\n{chunk.content}"
