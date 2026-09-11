from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RepositoryStatus(StrEnum):
    QUEUED = "queued"
    CLONING = "cloning"
    PARSING = "parsing"
    INDEXING = "indexing"
    BUILDING_GRAPH = "building_graph"
    GENERATING_EMBEDDINGS = "generating_embeddings"
    READY = "ready"
    FAILED = "failed"


class EntityType(StrEnum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"
    DOC_SECTION = "doc_section"


class RelationType(StrEnum):
    FILE_IMPORTS_FILE = "FILE_IMPORTS_FILE"
    FUNCTION_CALLS_FUNCTION = "FUNCTION_CALLS_FUNCTION"
    METHOD_CALLS_METHOD = "METHOD_CALLS_METHOD"
    CLASS_INHERITS_CLASS = "CLASS_INHERITS_CLASS"


class CodeEntity(BaseModel):
    """A structural unit extracted from a source file.

    ``file_path`` is always relative to the repository root so that citations
    are stable and never leak absolute paths from the indexing machine.
    """

    repository_id: str
    file_path: str
    language: str
    entity_type: EntityType
    entity_name: str
    start_line: int
    end_line: int
    source_code: str = ""
    parent_name: str | None = None
    signature: str | None = None
    docstring: str | None = None
    decorators: list[str] = Field(default_factory=list)
    base_classes: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        if self.parent_name:
            return f"{self.parent_name}.{self.entity_name}"
        return self.entity_name

    @property
    def citation(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


class ImportEdge(BaseModel):
    """An import statement, before and after resolution to a repository file."""

    repository_id: str
    source_file: str
    module: str
    imported_names: list[str] = Field(default_factory=list)
    line: int
    resolved_file: str | None = None
    is_relative: bool = False


class CallEdge(BaseModel):
    """A call site whose callee was resolved to an entity in this repository."""

    repository_id: str
    caller_file: str
    caller_name: str
    caller_type: EntityType
    callee_name: str
    callee_file: str | None = None
    callee_type: EntityType | None = None
    line: int


class InheritanceEdge(BaseModel):
    repository_id: str
    child_file: str
    child_class: str
    parent_class: str
    parent_file: str | None = None


class ParsedRepository(BaseModel):
    """Everything static analysis extracted from one repository."""

    entities: list[CodeEntity] = Field(default_factory=list)
    imports: list[ImportEdge] = Field(default_factory=list)
    calls: list[CallEdge] = Field(default_factory=list)
    inheritance: list[InheritanceEdge] = Field(default_factory=list)

    def extend(self, other: "ParsedRepository") -> None:
        self.entities.extend(other.entities)
        self.imports.extend(other.imports)
        self.calls.extend(other.calls)
        self.inheritance.extend(other.inheritance)


class RepositoryMetadata(BaseModel):
    id: str
    url: str
    status: RepositoryStatus
    name: str = ""
    languages: list[str] = Field(default_factory=list)
    language_counts: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    file_count: int = 0
    entity_count: int = 0
    chunk_count: int = 0
    graph_node_count: int = 0
    graph_relationship_count: int = 0
    overview: str = ""
    error: str | None = None
    indexing_seconds: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
