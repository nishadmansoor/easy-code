"""Shared, lazily-created service singletons.

Qdrant, Neo4j and the embedding model are expensive to construct, so they are
built once per process and reused. Construction is deferred until first use so
the API can start (and report its health) even when a backing service is down.
"""

import logging
import threading

from backend.app.graph.store import GraphStore
from backend.app.storage.database import RepositoryStore, get_repository_store
from backend.app.vector.store import VectorStore

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_vector_store: VectorStore | None = None
_graph_store: GraphStore | None = None


def get_vector_store() -> VectorStore:
    global _vector_store
    with _lock:
        if _vector_store is None:
            _vector_store = VectorStore()
        return _vector_store


def get_graph_store() -> GraphStore:
    global _graph_store
    with _lock:
        if _graph_store is None:
            _graph_store = GraphStore()
        return _graph_store


def get_store() -> RepositoryStore:
    return get_repository_store()


def reset_services() -> None:
    """Drop cached services (used by tests)."""
    global _vector_store, _graph_store
    with _lock:
        if _graph_store is not None:
            try:
                _graph_store.close()
            except Exception:
                pass
        _vector_store = None
        _graph_store = None


def service_health() -> tuple[bool, bool]:
    """(qdrant_ok, neo4j_ok) — never raises."""
    qdrant_ok = False
    neo4j_ok = False
    try:
        get_vector_store().client.get_collections()
        qdrant_ok = True
    except Exception:
        logger.debug("Qdrant health check failed", exc_info=True)
    try:
        get_graph_store().run_cypher("RETURN 1 AS ok")
        neo4j_ok = True
    except Exception:
        logger.debug("Neo4j health check failed", exc_info=True)
    return qdrant_ok, neo4j_ok
