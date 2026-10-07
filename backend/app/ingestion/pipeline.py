"""End-to-end indexing pipeline.

Runs clone, parse, embed, graph and summarise in order, recording the status
at each stage so a caller can poll progress. Any failure marks the repository ``failed`` with
the reason instead of leaving it stuck mid-flight.
"""

import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from backend.app.chunking.semantic import build_chunks
from backend.app.config.settings import settings
from backend.app.generation.overview import generate_overview
from backend.app.graph import build_graph_store
from backend.app.graph.builder import build_code_graph
from backend.app.graph.store import GraphStore
from backend.app.ingestion.repository import (
    IngestionError,
    clone_repository,
    delete_repository_workspace,
    detect_frameworks,
    detect_languages,
    iter_repository_files,
    normalize_repository_url,
    relative_path,
    repository_name,
)
from backend.app.models.entities import RepositoryMetadata, RepositoryStatus
from backend.app.parsing import parse_repository
from backend.app.storage.database import RepositoryStore
from backend.app.vector.store import VectorStore

logger = logging.getLogger(__name__)

StoreFactory = Callable[[], VectorStore | GraphStore]


def repository_path(repository_id: str) -> Path:
    return settings.repos_dir / repository_id


def register_repository(url: str, store: RepositoryStore) -> RepositoryMetadata:
    """Create the queued record. Raises IngestionError on an invalid URL."""
    normalized = normalize_repository_url(url)
    metadata = RepositoryMetadata(
        id=uuid.uuid4().hex[:12],
        url=normalized,
        name=repository_name(normalized),
        status=RepositoryStatus.QUEUED,
    )
    return store.create(metadata)


def index_repository(
    repository_id: str,
    store: RepositoryStore,
    vector_store: VectorStore | None = None,
    graph_store: GraphStore | None = None,
    generate_repository_overview: bool = True,
) -> RepositoryMetadata | None:
    """Clone, parse, embed, graph and summarise one registered repository."""
    started = time.perf_counter()
    metadata = store.get(repository_id)
    if metadata is None:
        logger.error("Cannot index unknown repository %s", repository_id)
        return None

    owns_graph_store = graph_store is None

    try:
        store.set_status(repository_id, RepositoryStatus.CLONING)
        _, repo_path = clone_repository(metadata.url, repo_id=repository_id)

        language_counts = detect_languages(repo_path)
        frameworks = detect_frameworks(repo_path)
        all_files = iter_repository_files(repo_path)
        store.update(
            repository_id,
            languages=list(language_counts.keys()),
            language_counts=language_counts,
            frameworks=frameworks,
            file_count=len(all_files),
        )

        store.set_status(repository_id, RepositoryStatus.PARSING)
        parsed = parse_repository(repo_path, repository_id)
        logger.info(
            "Parsed %s: %d entities, %d imports, %d calls, %d inheritance edges",
            repository_id,
            len(parsed.entities),
            len(parsed.imports),
            len(parsed.calls),
            len(parsed.inheritance),
        )
        store.update(repository_id, entity_count=len(parsed.entities))

        store.set_status(repository_id, RepositoryStatus.GENERATING_EMBEDDINGS)
        chunks = build_chunks(parsed.entities)
        vector_store = vector_store or VectorStore()
        vector_store.delete_repository(repository_id)
        chunk_count = vector_store.store_chunks(chunks)
        store.update(repository_id, chunk_count=chunk_count)

        store.set_status(repository_id, RepositoryStatus.BUILDING_GRAPH)
        graph_store = graph_store or build_graph_store()
        file_rows = [
            {
                "file_path": relative_path(path, repo_path),
                "language": _language_of(path),
            }
            for path in all_files
        ]
        stats = build_code_graph(
            repo_id=repository_id,
            repo_url=metadata.url,
            parsed=parsed,
            graph_store=graph_store,
            repo_name=metadata.name,
            files=file_rows,
        )
        store.update(
            repository_id,
            graph_node_count=stats["node_count"],
            graph_relationship_count=stats["relationship_count"],
        )

        store.set_status(repository_id, RepositoryStatus.INDEXING)
        if generate_repository_overview:
            current = store.get(repository_id)
            try:
                overview = generate_overview(current, vector_store, graph_store)
                store.update(repository_id, overview=overview)
            except Exception:
                logger.exception("Overview generation failed for %s", repository_id)

        elapsed = time.perf_counter() - started
        store.update(
            repository_id,
            status=RepositoryStatus.READY,
            error=None,
            indexing_seconds=round(elapsed, 2),
        )
        logger.info("Indexed repository %s in %.1fs", repository_id, elapsed)
        return store.get(repository_id)

    except IngestionError as exc:
        logger.error("Ingestion failed for %s: %s", repository_id, exc)
        store.set_status(repository_id, RepositoryStatus.FAILED, error=str(exc))
        return store.get(repository_id)
    except Exception as exc:  # noqa: BLE001 - the status must always be updated
        logger.exception("Indexing failed for %s", repository_id)
        store.set_status(
            repository_id, RepositoryStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
        )
        return store.get(repository_id)
    finally:
        if owns_graph_store and graph_store is not None:
            graph_store.close()


def _language_of(path: Path) -> str:
    from backend.app.ingestion.repository import LANGUAGE_EXTENSIONS

    return LANGUAGE_EXTENSIONS.get(path.suffix.lower(), "unknown")


def delete_repository(
    repository_id: str,
    store: RepositoryStore,
    vector_store: VectorStore | None = None,
    graph_store: GraphStore | None = None,
) -> bool:
    """Remove a repository from every store and delete its workspace."""
    if store.get(repository_id) is None:
        return False

    try:
        (vector_store or VectorStore()).delete_repository(repository_id)
    except Exception:
        logger.exception("Failed to clear vectors for %s", repository_id)

    own_graph = graph_store is None
    try:
        graph_store = graph_store or build_graph_store()
        graph_store.clear_repository(repository_id)
    except Exception:
        logger.exception("Failed to clear graph for %s", repository_id)
    finally:
        if own_graph and graph_store is not None:
            graph_store.close()

    delete_repository_workspace(repository_id)
    return store.delete(repository_id)
