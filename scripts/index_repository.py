#!/usr/bin/env python
"""Index a repository from the command line, then optionally ask a question.

    python scripts/index_repository.py https://github.com/psf/requests
    python scripts/index_repository.py https://github.com/psf/requests \
        --ask "What happens when you call requests.get?"

Requires Qdrant and Neo4j (``docker compose up -d qdrant neo4j``).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.generation.answer import generate_answer  # noqa: E402
from backend.app.graph import build_graph_store  # noqa: E402
from backend.app.ingestion.pipeline import (  # noqa: E402
    index_repository,
    register_repository,
    repository_path,
)
from backend.app.ingestion.repository import IngestionError  # noqa: E402
from backend.app.retrieval.hybrid import retrieve_hybrid  # noqa: E402
from backend.app.storage.database import RepositoryStore  # noqa: E402
from backend.app.vector.store import VectorStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public HTTP(S) git repository URL")
    parser.add_argument("--ask", action="append", default=[], help="Ask a question afterwards")
    parser.add_argument(
        "--no-overview", action="store_true", help="Skip generating the repository overview"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    for noisy in ("httpx", "httpcore", "sentence_transformers", "neo4j", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    store = RepositoryStore()
    try:
        metadata = register_repository(args.url, store)
    except IngestionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = index_repository(
        metadata.id, store, generate_repository_overview=not args.no_overview
    )
    if result is None or result.status != "ready":
        print(f"Indexing failed: {result.error if result else 'unknown error'}", file=sys.stderr)
        return 1

    print()
    print(f"Repository {result.name} indexed as {result.id} in {result.indexing_seconds}s")
    print(
        f"  {result.file_count} files, {result.entity_count} entities, "
        f"{result.chunk_count} chunks"
    )
    print(
        f"  {result.graph_node_count} graph nodes, "
        f"{result.graph_relationship_count} relationships"
    )
    print(f"  languages: {', '.join(result.languages) or 'none detected'}")
    print(f"  frameworks: {', '.join(result.frameworks) or 'none detected'}")

    if result.overview:
        print()
        print("=" * 72)
        print(result.overview)

    if not args.ask:
        return 0

    vector_store = VectorStore()
    graph_store = build_graph_store()
    try:
        for question in args.ask:
            context = retrieve_hybrid(
                question,
                result.id,
                vector_store,
                graph_store,
                repo_path=repository_path(result.id),
            )
            answer = generate_answer(context, repository_name=result.name)
            print()
            print("=" * 72)
            print(f"Q: {question}")
            print(f"   [{context.question_type}] {context.vector_hit_count} vector hits, "
                  f"{context.graph_hit_count} graph hits, {answer.latency_seconds:.1f}s")
            print()
            print(answer.answer)
    finally:
        graph_store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
