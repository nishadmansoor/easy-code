"""Semantic retrieval from the vector index.

This module is also the vector-only baseline used by the evaluation harness,
so it deliberately contains no graph logic.
"""

from backend.app.retrieval.ranking import (
    TIER_DIRECT,
    TIER_DOC,
    TIER_FILE,
    RetrievedItem,
    Source,
)
from backend.app.vector.store import VectorStore

#: Ranks 1-3 are treated as directly relevant; later hits are supporting context.
DIRECT_HIT_CUTOFF = 3


def retrieve_relevant_chunks(
    query: str,
    vector_store: VectorStore,
    repository_id: str | None = None,
    limit: int = 10,
    entity_types: list[str] | None = None,
) -> list[RetrievedItem]:
    hits = vector_store.search(
        query=query,
        repository_id=repository_id,
        limit=limit,
        entity_types=entity_types,
    )

    items: list[RetrievedItem] = []
    for rank_index, hit in enumerate(hits):
        if hit["entity_type"] == "doc_section":
            tier = TIER_DOC
        elif rank_index < DIRECT_HIT_CUTOFF:
            tier = TIER_DIRECT
        else:
            tier = TIER_FILE

        items.append(
            RetrievedItem(
                file_path=hit["file_path"],
                entity_type=hit["entity_type"],
                entity_name=hit["entity_name"],
                start_line=hit["start_line"],
                end_line=hit["end_line"],
                content=hit["content"],
                language=hit["language"],
                score=hit["score"],
                source=Source.VECTOR,
                tier=tier,
                reason=f"semantic rank {rank_index + 1}",
            )
        )
    return items
