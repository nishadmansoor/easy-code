"""Turns static-analysis output into graph nodes and relationships.

Only resolved relationships are written. An import of a third-party package or
a call to an unknown name produces no edge rather than a guessed one.
"""

import logging

from backend.app.graph.store import GraphStore
from backend.app.models.entities import EntityType, ParsedRepository

logger = logging.getLogger(__name__)


def build_code_graph(
    repo_id: str,
    repo_url: str,
    parsed: ParsedRepository,
    graph_store: GraphStore,
    repo_name: str = "",
    files: list[dict] | None = None,
) -> dict:
    """Write the repository graph and return its statistics."""
    graph_store.clear_repository(repo_id)
    graph_store.create_repository_node(repository_id=repo_id, url=repo_url, name=repo_name)

    file_rows = files if files is not None else _file_rows_from_entities(parsed)
    graph_store.create_files(repo_id, file_rows)

    graph_store.create_classes(
        repo_id,
        [
            {
                "file_path": e.file_path,
                "name": e.entity_name,
                "start_line": e.start_line,
                "end_line": e.end_line,
                "docstring": e.docstring,
                "base_classes": e.base_classes,
            }
            for e in parsed.entities
            if e.entity_type == EntityType.CLASS
        ],
    )

    graph_store.create_functions(
        repo_id,
        [
            {
                "file_path": e.file_path,
                "name": e.entity_name,
                "start_line": e.start_line,
                "end_line": e.end_line,
                "signature": e.signature,
                "docstring": e.docstring,
            }
            for e in parsed.entities
            if e.entity_type == EntityType.FUNCTION
        ],
    )

    graph_store.create_methods(
        repo_id,
        [
            {
                "file_path": e.file_path,
                "class_name": e.parent_name or "",
                "name": e.entity_name,
                "start_line": e.start_line,
                "end_line": e.end_line,
                "signature": e.signature,
                "docstring": e.docstring,
            }
            for e in parsed.entities
            if e.entity_type == EntityType.METHOD and e.parent_name
        ],
    )

    graph_store.create_imports(
        repo_id,
        [
            {
                "source_file": edge.source_file,
                "target_file": edge.resolved_file,
                "module": edge.module,
                "line": edge.line,
            }
            for edge in parsed.imports
            if edge.resolved_file
        ],
    )

    graph_store.create_calls(
        repo_id,
        [
            {
                "caller_file": call.caller_file,
                "caller_name": call.caller_name,
                "caller_type": call.caller_type.value,
                "callee_file": call.callee_file,
                "callee_name": call.callee_name,
                "callee_type": call.callee_type.value if call.callee_type else None,
                "line": call.line,
            }
            for call in parsed.calls
            if call.callee_file
        ],
    )

    graph_store.create_inheritance(
        repo_id,
        [
            {
                "child_file": edge.child_file,
                "child_class": edge.child_class,
                "parent_file": edge.parent_file,
                "parent_class": edge.parent_class,
            }
            for edge in parsed.inheritance
            if edge.parent_file
        ],
    )

    stats = graph_store.get_statistics(repo_id)
    logger.info(
        "Built graph for %s: %d nodes, %d relationships",
        repo_id,
        stats["node_count"],
        stats["relationship_count"],
    )
    return stats


def _file_rows_from_entities(parsed: ParsedRepository) -> list[dict]:
    seen: dict[str, str] = {}
    for entity in parsed.entities:
        seen.setdefault(entity.file_path, entity.language)
    return [{"file_path": path, "language": language} for path, language in sorted(seen.items())]
