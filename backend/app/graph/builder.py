from pathlib import Path

from backend.app.graph.store import GraphStore
from backend.app.ingestion.repository import LANGUAGE_EXTENSIONS, collect_source_files
from backend.app.models.entities import CodeEntity, EntityType


def build_code_graph(
    repo_id: str,
    repo_url: str,
    repo_path: Path,
    entities: list[CodeEntity],
    graph_store: GraphStore,
):
    graph_store.clear_repository(repo_id)
    graph_store.create_repository_node(repository_id=repo_id, url=repo_url)

    source_files = collect_source_files(repo_path)
    for f in source_files:
        lang = LANGUAGE_EXTENSIONS.get(f.suffix, "unknown")
        rel_path = str(f)
        graph_store.create_file_node(repository_id=repo_id, file_path=rel_path, language=lang)
        graph_store.create_repository_contains_file(repository_id=repo_id, file_path=rel_path)

    entity_map: dict[tuple[str, str, str], CodeEntity] = {}
    for e in entities:
        key = (e.file_path, e.entity_type.value, e.entity_name)
        entity_map[key] = e

    for e in entities:
        if e.entity_type == EntityType.CLASS:
            graph_store.create_class_node(
                repository_id=repo_id,
                file_path=e.file_path,
                class_name=e.entity_name,
                start_line=e.start_line,
                end_line=e.end_line,
            )
            graph_store.create_file_defines_class(
                repository_id=repo_id,
                file_path=e.file_path,
                class_name=e.entity_name,
            )

        elif e.entity_type == EntityType.FUNCTION:
            graph_store.create_function_node(
                repository_id=repo_id,
                file_path=e.file_path,
                function_name=e.entity_name,
                start_line=e.start_line,
                end_line=e.end_line,
            )
            graph_store.create_file_defines_function(
                repository_id=repo_id,
                file_path=e.file_path,
                function_name=e.entity_name,
            )

        elif e.entity_type == EntityType.METHOD:
            graph_store.create_method_node(
                repository_id=repo_id,
                file_path=e.file_path,
                class_name=e.parent_name or "",
                method_name=e.entity_name,
                start_line=e.start_line,
                end_line=e.end_line,
            )
            if e.parent_name:
                graph_store.create_class_contains_method(
                    repository_id=repo_id,
                    file_path=e.file_path,
                    class_name=e.parent_name,
                    method_name=e.entity_name,
                )

        elif e.entity_type == EntityType.IMPORT:
            graph_store.create_file_imports_file(
                repository_id=repo_id,
                source_file=e.file_path,
                target_module=e.entity_name,
            )
