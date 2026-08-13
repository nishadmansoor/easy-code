from backend.app.vector.store import VectorStore


def retrieve_relevant_chunks(
    query: str,
    vector_store: VectorStore,
    repository_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    results = vector_store.search(query=query, repository_id=repository_id, limit=limit)
    return results
