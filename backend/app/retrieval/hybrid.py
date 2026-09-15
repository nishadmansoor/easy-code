"""Hybrid retrieval: semantic search plus structural graph traversal.

Pipeline:

    question -> analysis -> {Qdrant, Neo4j} -> ranking -> dedup -> context

The two retrievers are complementary. Qdrant finds code that is conceptually
related to the question; Neo4j explains how that code connects to everything
else. Which one leads is decided by the question's classification.
"""

import logging
from pathlib import Path

from backend.app.config.settings import settings
from backend.app.graph.store import GraphStore
from backend.app.retrieval.graph_retrieval import expand_from_vector_hits, retrieve_graph_context
from backend.app.retrieval.question_analysis import QuestionType, analyze_question
from backend.app.retrieval.ranking import RetrievalContext, RetrievedItem, rank
from backend.app.retrieval.vector_retrieval import retrieve_relevant_chunks
from backend.app.vector.store import VectorStore

logger = logging.getLogger(__name__)

#: How many vector hits to fetch per question type before ranking.
VECTOR_LIMITS = {
    QuestionType.SEMANTIC: 1.0,
    QuestionType.MIXED: 0.8,
    QuestionType.STRUCTURAL: 0.4,
}


def retrieve_hybrid(
    question: str,
    repository_id: str,
    vector_store: VectorStore | None = None,
    graph_store: GraphStore | None = None,
    repo_path: Path | None = None,
    context_limit: int | None = None,
) -> RetrievalContext:
    """Retrieve and rank evidence for one question."""
    analysis = analyze_question(question)
    limit = context_limit or settings.retrieval_context_limit

    vector_items: list[RetrievedItem] = []
    graph_items: list[RetrievedItem] = []
    graph_facts: list[str] = []

    # A structural question with no resolvable target has nothing to look up in
    # the graph, so fall back to semantic search rather than returning nothing.
    use_vector = analysis.wants_vector or not analysis.identifiers
    if vector_store is not None and use_vector:
        vector_limit = max(
            4, int(settings.retrieval_vector_limit * VECTOR_LIMITS[analysis.question_type])
        )
        try:
            vector_items = retrieve_relevant_chunks(
                query=question,
                vector_store=vector_store,
                repository_id=repository_id,
                limit=vector_limit,
            )
        except Exception:
            logger.exception("Vector retrieval failed for repository %s", repository_id)

    if graph_store is not None and analysis.wants_graph:
        try:
            graph_items, graph_facts = retrieve_graph_context(
                analysis, graph_store, repository_id
            )
        except Exception:
            logger.exception("Graph retrieval failed for repository %s", repository_id)

        # Structural expansion around semantic hits: the step that makes the
        # hybrid different from running both retrievers side by side.
        if vector_items:
            try:
                expanded_items, expanded_facts = expand_from_vector_hits(
                    graph_store, repository_id, vector_items
                )
                graph_items.extend(expanded_items)
                graph_facts.extend(expanded_facts)
            except Exception:
                logger.exception("Graph expansion failed for repository %s", repository_id)

    # Structural results have no source text of their own; read it from disk so
    # the LLM sees real code rather than only a node name.
    if repo_path is not None:
        _attach_source(graph_items, repo_path)

    ranked = rank(vector_items + graph_items, limit=limit)

    return RetrievalContext(
        question=question,
        question_type=analysis.question_type.value,
        items=ranked,
        graph_facts=_dedupe_preserving_order(graph_facts)[:25],
        identifiers=analysis.identifiers,
        vector_hit_count=len(vector_items),
        graph_hit_count=len(graph_items),
    )


def retrieve_vector_only(
    question: str,
    repository_id: str,
    vector_store: VectorStore,
    context_limit: int | None = None,
) -> RetrievalContext:
    """The baseline the hybrid system is measured against."""
    limit = context_limit or settings.retrieval_context_limit
    items = retrieve_relevant_chunks(
        query=question,
        vector_store=vector_store,
        repository_id=repository_id,
        limit=max(limit, settings.retrieval_vector_limit),
    )
    return RetrievalContext(
        question=question,
        question_type="vector_only",
        items=rank(items, limit=limit),
        vector_hit_count=len(items),
    )


MAX_ATTACHED_LINES = 120


def _attach_source(items: list[RetrievedItem], repo_path: Path) -> None:
    """Fill in ``content`` for graph items by reading the cited line range."""
    cache: dict[str, list[str]] = {}
    for item in items:
        if item.content or not item.file_path or item.start_line <= 0:
            continue
        lines = cache.get(item.file_path)
        if lines is None:
            path = (repo_path / item.file_path).resolve()
            try:
                # Guard against a crafted path escaping the workspace.
                path.relative_to(repo_path.resolve())
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except (OSError, ValueError):
                lines = []
            cache[item.file_path] = lines
        if not lines:
            continue
        end = min(item.end_line, item.start_line + MAX_ATTACHED_LINES - 1, len(lines))
        item.content = "\n".join(lines[item.start_line - 1 : end])
        if end < item.end_line:
            item.content += "\n... (truncated)"


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
