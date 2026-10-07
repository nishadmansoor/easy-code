"""Graph backend selection.

Two interchangeable implementations of the same interface:

- :class:`~backend.app.graph.store.GraphStore` talks to a Neo4j server.
- :class:`~backend.app.graph.embedded_store.EmbeddedGraphStore` needs no server
  and keeps one JSON file per repository.

``STORAGE_MODE=embedded`` selects the second. Nothing above this layer knows
which is in use.
"""

import logging
import threading

from backend.app.config.settings import settings

logger = logging.getLogger(__name__)

_embedded_store = None
_lock = threading.Lock()


def build_graph_store():
    """Construct the configured graph backend.

    The embedded store is a process-wide singleton because it caches each
    repository's graph in memory. Two instances would each hold their own
    cache, so a write through one could go unseen by the other.
    """
    if not settings.embedded:
        from backend.app.graph.store import GraphStore

        return GraphStore()

    global _embedded_store
    with _lock:
        if _embedded_store is None:
            from backend.app.graph.embedded_store import EmbeddedGraphStore

            logger.info("Using embedded graph store at %s", settings.graph_dir)
            _embedded_store = EmbeddedGraphStore()
        return _embedded_store


def reset_graph_store() -> None:
    """Drop the cached embedded store (used by tests)."""
    global _embedded_store
    with _lock:
        _embedded_store = None


__all__ = ["build_graph_store", "reset_graph_store"]
