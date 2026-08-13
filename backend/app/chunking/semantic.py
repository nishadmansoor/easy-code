from dataclasses import dataclass
from pathlib import Path

from backend.app.models.entities import CodeEntity, EntityType


@dataclass
class Chunk:
    repository_id: str
    file_path: str
    language: str
    entity_type: str
    entity_name: str
    start_line: int
    end_line: int
    content: str
    metadata: dict | None = None


def create_chunks_from_entities(entities: list[CodeEntity]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for entity in entities:
        if entity.source_code and entity.entity_type in {
            EntityType.CLASS,
            EntityType.FUNCTION,
            EntityType.METHOD,
        }:
            chunks.append(
                Chunk(
                    repository_id=entity.repository_id,
                    file_path=entity.file_path,
                    language=entity.language,
                    entity_type=entity.entity_type.value,
                    entity_name=entity.entity_name,
                    start_line=entity.start_line,
                    end_line=entity.end_line,
                    content=entity.source_code,
                    metadata={"parent_name": entity.parent_name} if entity.parent_name else None,
                )
            )
    return chunks


def create_file_chunks(repo_path: Path, entities: list[CodeEntity]) -> list[Chunk]:

    file_entities: dict[str, list[CodeEntity]] = {}
    for e in entities:
        file_entities.setdefault(e.file_path, []).append(e)

    chunks: list[Chunk] = []
    for file_path_str, file_ents in file_entities.items():
        p = Path(file_path_str)
        if not p.exists():
            continue
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if len(source.strip()) == 0:
            continue

        covered_lines = set()
        for e in file_ents:
            for line in range(e.start_line, e.end_line + 1):
                covered_lines.add(line)

        uncovered_ranges: list[tuple[int, int]] = []
        start = None
        for line_num in range(1, len(source.splitlines()) + 1):
            if line_num not in covered_lines:
                if start is None:
                    start = line_num
            else:
                if start is not None:
                    uncovered_ranges.append((start, line_num - 1))
                    start = None
        if start is not None:
            uncovered_ranges.append((start, len(source.splitlines())))

        for s, e_line in uncovered_ranges:
            lines = source.splitlines()[s - 1 : e_line]
            content = "\n".join(lines).strip()
            if len(content) > 20:
                chunks.append(
                    Chunk(
                        repository_id=file_ents[0].repository_id,
                        file_path=file_path_str,
                        language=file_ents[0].language,
                        entity_type="module_context",
                        entity_name=p.name,
                        start_line=s,
                        end_line=e_line,
                        content=content,
                    )
                )

    return chunks
