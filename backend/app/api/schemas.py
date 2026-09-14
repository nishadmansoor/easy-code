from datetime import datetime

from pydantic import BaseModel, Field


class RepositoryRequest(BaseModel):
    url: str = Field(..., description="Public HTTP(S) URL of a git repository")


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    mode: str = Field(
        "hybrid",
        description="'hybrid' (semantic + structural) or 'vector' (semantic only baseline)",
    )
    generate: bool = Field(True, description="Run the LLM; false returns retrieval only")
    limit: int | None = Field(None, ge=1, le=50)


class RepositoryResponse(BaseModel):
    id: str
    url: str
    name: str
    status: str
    languages: list[str] = []
    language_counts: dict[str, int] = {}
    frameworks: list[str] = []
    file_count: int = 0
    entity_count: int = 0
    chunk_count: int = 0
    graph_node_count: int = 0
    graph_relationship_count: int = 0
    indexing_seconds: float = 0.0
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StatusResponse(BaseModel):
    id: str
    status: str
    error: str | None = None
    file_count: int = 0
    entity_count: int = 0
    chunk_count: int = 0
    graph_node_count: int = 0
    indexing_seconds: float = 0.0


class OverviewResponse(BaseModel):
    id: str
    name: str
    overview: str
    languages: list[str] = []
    frameworks: list[str] = []
    file_count: int = 0
    entity_count: int = 0


class SourceReference(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    entity_type: str = ""
    entity_name: str = ""
    source: str = ""
    relationship: str = ""
    score: float = 0.0
    content: str = ""


class CitationModel(BaseModel):
    file_path: str
    start_line: int
    end_line: int


class QueryResponse(BaseModel):
    query: str
    question_type: str
    answer: str = ""
    citations: list[CitationModel] = []
    unverified_citations: list[str] = []
    grounded: bool = True
    sources: list[SourceReference] = []
    graph_facts: list[str] = []
    vector_hits: int = 0
    graph_hits: int = 0
    provider: str = ""
    model: str = ""
    latency_seconds: float = 0.0


class FileEntry(BaseModel):
    file_path: str
    language: str = ""


class FileListResponse(BaseModel):
    files: list[FileEntry]


class FileContentResponse(BaseModel):
    file_path: str
    language: str = ""
    content: str
    line_count: int


class GraphNode(BaseModel):
    name: str
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    signature: str | None = None
    class_name: str | None = None


class GraphFileContents(BaseModel):
    file_path: str
    language: str = ""
    classes: list[GraphNode] = []
    functions: list[GraphNode] = []
    methods: list[GraphNode] = []


class GraphClassInfo(BaseModel):
    class_name: str
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    methods: list[GraphNode] = []
    bases: list[GraphNode] = []
    subclasses: list[GraphNode] = []


class GraphCallerInfo(BaseModel):
    caller_type: str
    caller_name: str
    caller_file: str
    caller_start: int = 0
    caller_end: int = 0


class GraphCalleeInfo(BaseModel):
    callee_type: str
    callee_name: str
    callee_file: str
    callee_start: int = 0
    callee_end: int = 0


class GraphImporterInfo(BaseModel):
    file_path: str


class GraphOverviewFile(BaseModel):
    file_path: str
    language: str = ""
    classes: list[str] = []
    functions: list[str] = []


class GraphOverview(BaseModel):
    files: list[GraphOverviewFile] = []
    statistics: dict = {}
    central_files: list[dict] = []
    entry_points: list[dict] = []


class NetworkNode(BaseModel):
    id: str
    label: str = ""
    language: str | None = None
    file_path: str | None = None
    entity_type: str | None = None
    start_line: int = 0
    end_line: int = 0
    imports: int = 0
    importers: int = 0
    definitions: int = 0
    focus: bool = False


class NetworkEdge(BaseModel):
    source: str
    target: str


class NetworkResponse(BaseModel):
    """Nodes and edges for the graph visualisation."""

    scope: str
    focus: str | None = None
    nodes: list[NetworkNode] = []
    edges: list[NetworkEdge] = []
    total_nodes: int = 0
    truncated: bool = False


class HealthResponse(BaseModel):
    status: str
    qdrant: bool
    neo4j: bool
    llm_provider: str
    llm_model: str
    llm_available: bool
