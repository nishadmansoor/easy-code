from enum import StrEnum

from pydantic import BaseModel


class RepositoryStatus(StrEnum):
    QUEUED = "queued"
    CLONING = "cloning"
    PARSING = "parsing"
    INDEXING = "indexing"
    GENERATING_EMBEDDINGS = "generating_embeddings"
    READY = "ready"
    FAILED = "failed"


class EntityType(StrEnum):
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"


class CodeEntity(BaseModel):
    repository_id: str
    file_path: str
    language: str
    entity_type: EntityType
    entity_name: str
    start_line: int
    end_line: int
    source_code: str = ""
    parent_name: str | None = None


class RepositoryMetadata(BaseModel):
    id: str
    url: str
    status: RepositoryStatus
    languages: list[str] = []
    frameworks: list[str] = []
    file_count: int = 0
    entity_count: int = 0
