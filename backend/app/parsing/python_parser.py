import ast
from pathlib import Path

from backend.app.models.entities import CodeEntity, EntityType


def parse_python_file(file_path: Path, repo_id: str) -> list[CodeEntity]:
    source = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    rel_path = str(file_path)
    entities: list[CodeEntity] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            entities.append(
                CodeEntity(
                    repository_id=repo_id,
                    file_path=rel_path,
                    language="python",
                    entity_type=EntityType.CLASS,
                    entity_name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    source_code=ast.get_source_segment(source, node) or "",
                )
            )
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entities.append(
                        CodeEntity(
                            repository_id=repo_id,
                            file_path=rel_path,
                            language="python",
                            entity_type=EntityType.METHOD,
                            entity_name=item.name,
                            start_line=item.lineno,
                            end_line=getattr(item, "end_lineno", item.lineno),
                            source_code=ast.get_source_segment(source, item) or "",
                            parent_name=node.name,
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entities.append(
                CodeEntity(
                    repository_id=repo_id,
                    file_path=rel_path,
                    language="python",
                    entity_type=EntityType.FUNCTION,
                    entity_name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    source_code=ast.get_source_segment(source, node) or "",
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                entities.append(
                    CodeEntity(
                        repository_id=repo_id,
                        file_path=rel_path,
                        language="python",
                        entity_type=EntityType.IMPORT,
                        entity_name=alias.name,
                        start_line=node.lineno,
                        end_line=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                entities.append(
                    CodeEntity(
                        repository_id=repo_id,
                        file_path=rel_path,
                        language="python",
                        entity_type=EntityType.IMPORT,
                        entity_name=f"{module}.{alias.name}",
                        start_line=node.lineno,
                        end_line=node.lineno,
                    )
                )

    return entities
