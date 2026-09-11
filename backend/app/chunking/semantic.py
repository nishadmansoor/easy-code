"""Semantic chunking.

Chunks follow structural boundaries — a function, a method, a class, a
documentation section — rather than a fixed character window. Oversized units
are split on line boundaries so that a very long function still fits the
embedding model's context, and every part keeps accurate line numbers.
"""

from dataclasses import dataclass, field

from backend.app.models.entities import CodeEntity, EntityType

MAX_CHUNK_CHARS = 4000
MIN_CHUNK_CHARS = 30

CHUNKABLE_TYPES = {
    EntityType.CLASS,
    EntityType.FUNCTION,
    EntityType.METHOD,
    EntityType.DOC_SECTION,
}


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
    metadata: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"

    def embedding_text(self) -> str:
        """Text sent to the embedding model.

        A natural-language header is prepended so that questions phrased in
        prose ("where is authentication handled") can match code whose tokens
        are mostly identifiers.
        """
        header = f"{self.entity_type} {self.entity_name} in {self.file_path}"
        parent = self.metadata.get("parent_name")
        if parent:
            header += f" (defined in {parent})"
        signature = self.metadata.get("signature")
        docstring = self.metadata.get("docstring")

        parts = [header]
        if signature:
            parts.append(signature)
        if docstring:
            parts.append(docstring.strip())
        parts.append(self.content)
        return "\n\n".join(part for part in parts if part)


def _split_oversized(chunk: Chunk) -> list[Chunk]:
    """Split a chunk that exceeds the size limit, preserving line numbers."""
    if len(chunk.content) <= MAX_CHUNK_CHARS:
        return [chunk]

    lines = chunk.content.splitlines()
    parts: list[Chunk] = []
    buffer: list[str] = []
    buffer_start = chunk.start_line
    size = 0

    for offset, line in enumerate(lines):
        line_number = chunk.start_line + offset
        if size + len(line) + 1 > MAX_CHUNK_CHARS and buffer:
            parts.append(
                Chunk(
                    repository_id=chunk.repository_id,
                    file_path=chunk.file_path,
                    language=chunk.language,
                    entity_type=chunk.entity_type,
                    entity_name=f"{chunk.entity_name} (part {len(parts) + 1})",
                    start_line=buffer_start,
                    end_line=line_number - 1,
                    content="\n".join(buffer),
                    metadata={**chunk.metadata, "split_of": chunk.entity_name},
                )
            )
            buffer, size, buffer_start = [], 0, line_number
        buffer.append(line)
        size += len(line) + 1

    if buffer:
        parts.append(
            Chunk(
                repository_id=chunk.repository_id,
                file_path=chunk.file_path,
                language=chunk.language,
                entity_type=chunk.entity_type,
                entity_name=(
                    f"{chunk.entity_name} (part {len(parts) + 1})" if parts else chunk.entity_name
                ),
                start_line=buffer_start,
                end_line=chunk.end_line,
                content="\n".join(buffer),
                metadata=(
                    {**chunk.metadata, "split_of": chunk.entity_name} if parts else chunk.metadata
                ),
            )
        )
    return parts


def create_chunks_from_entities(entities: list[CodeEntity]) -> list[Chunk]:
    """One chunk per meaningful code or documentation unit.

    A class that contains methods is represented by its signature, docstring
    and attribute lines rather than its full body, because each method is
    already indexed separately.
    """
    methods_by_class: dict[tuple[str, str], list[CodeEntity]] = {}
    for entity in entities:
        if entity.entity_type == EntityType.METHOD and entity.parent_name:
            methods_by_class.setdefault((entity.file_path, entity.parent_name), []).append(entity)

    chunks: list[Chunk] = []
    for entity in entities:
        if entity.entity_type not in CHUNKABLE_TYPES:
            continue

        content = entity.source_code
        metadata: dict = {}
        if entity.parent_name:
            metadata["parent_name"] = entity.parent_name
        if entity.signature:
            metadata["signature"] = entity.signature
        if entity.docstring:
            metadata["docstring"] = entity.docstring
        if entity.decorators:
            metadata["decorators"] = entity.decorators
        if entity.base_classes:
            metadata["base_classes"] = entity.base_classes

        if entity.entity_type == EntityType.CLASS:
            members = methods_by_class.get((entity.file_path, entity.entity_name), [])
            if members:
                content = _class_summary(entity, members)
                metadata["method_names"] = [m.entity_name for m in members]

        if not content or len(content.strip()) < MIN_CHUNK_CHARS:
            continue

        chunks.append(
            Chunk(
                repository_id=entity.repository_id,
                file_path=entity.file_path,
                language=entity.language,
                entity_type=entity.entity_type.value,
                entity_name=entity.entity_name,
                start_line=entity.start_line,
                end_line=entity.end_line,
                content=content,
                metadata=metadata,
            )
        )

    expanded: list[Chunk] = []
    for chunk in chunks:
        expanded.extend(_split_oversized(chunk))
    return expanded


def _class_summary(entity: CodeEntity, methods: list[CodeEntity]) -> str:
    """A class outline: declaration, docstring and method signatures."""
    lines = [entity.signature or f"class {entity.entity_name}:"]
    if entity.docstring:
        lines.append(f'    """{entity.docstring.strip()}"""')
    for method in sorted(methods, key=lambda m: m.start_line):
        lines.append(f"    {method.signature or f'def {method.entity_name}(...)'}")
    return "\n".join(lines)


def create_module_chunks(entities: list[CodeEntity]) -> list[Chunk]:
    """Module-level chunks that give each file a searchable identity.

    Without these, a question like "what does the config module do" can only
    match individual functions.
    """
    modules = [e for e in entities if e.entity_type == EntityType.MODULE]
    by_file: dict[str, list[CodeEntity]] = {}
    for entity in entities:
        by_file.setdefault(entity.file_path, []).append(entity)

    chunks: list[Chunk] = []
    for module in modules:
        members = by_file.get(module.file_path, [])
        classes = [e.entity_name for e in members if e.entity_type == EntityType.CLASS]
        functions = [e.entity_name for e in members if e.entity_type == EntityType.FUNCTION]
        if not classes and not functions and not module.docstring:
            continue

        lines = [f"Module {module.file_path}"]
        if module.docstring:
            lines.append(module.docstring.strip())
        if classes:
            lines.append("Classes: " + ", ".join(sorted(classes)))
        if functions:
            lines.append("Functions: " + ", ".join(sorted(functions)))
        content = "\n".join(lines)
        if len(content) < MIN_CHUNK_CHARS:
            continue

        chunks.append(
            Chunk(
                repository_id=module.repository_id,
                file_path=module.file_path,
                language=module.language,
                entity_type=EntityType.MODULE.value,
                entity_name=module.entity_name,
                start_line=module.start_line,
                end_line=module.end_line,
                content=content,
                metadata={"class_count": len(classes), "function_count": len(functions)},
            )
        )
    return chunks


def build_chunks(entities: list[CodeEntity]) -> list[Chunk]:
    """All chunks for a repository."""
    return create_chunks_from_entities(entities) + create_module_chunks(entities)
