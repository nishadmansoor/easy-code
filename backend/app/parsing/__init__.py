from pathlib import Path

from backend.app.models.entities import CodeEntity
from backend.app.parsing.python_parser import parse_python_file

PARSERS = {
    "python": parse_python_file,
}


def parse_repository(repo_path: Path, repo_id: str) -> list[CodeEntity]:
    from backend.app.ingestion.repository import LANGUAGE_EXTENSIONS, collect_source_files

    source_files = collect_source_files(repo_path)
    entities: list[CodeEntity] = []

    for file_path in source_files:
        lang = LANGUAGE_EXTENSIONS.get(file_path.suffix)
        if lang and lang in PARSERS:
            entities.extend(PARSERS[lang](file_path, repo_id))

    return entities
