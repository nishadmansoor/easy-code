from pydantic import BaseModel


class RepositoryRequest(BaseModel):
    url: str


class QueryRequest(BaseModel):
    query: str
    repository_id: str


class QueryResult(BaseModel):
    score: float
    file_path: str
    entity_type: str
    entity_name: str
    start_line: int
    end_line: int
    content: str
    language: str


class QueryResponse(BaseModel):
    results: list[QueryResult]
    query: str


class RepositoryResponse(BaseModel):
    id: str
    url: str
    status: str
    languages: list[str]
    file_count: int
    entity_count: int
    graph_node_count: int = 0


class GraphFileNode(BaseModel):
    file_path: str
    language: str


class GraphClassNode(BaseModel):
    name: str
    file_path: str
    start_line: int
    end_line: int


class GraphFunctionNode(BaseModel):
    name: str
    file_path: str
    start_line: int
    end_line: int


class GraphMethodNode(BaseModel):
    name: str
    class_name: str
    file_path: str
    start_line: int
    end_line: int


class GraphFileContents(BaseModel):
    file_path: str
    classes: list[GraphClassNode]
    functions: list[GraphFunctionNode]
    methods: list[GraphMethodNode]


class GraphClassInfo(BaseModel):
    class_name: str
    methods: list[GraphMethodNode]


class GraphCallerInfo(BaseModel):
    caller_type: str
    caller_name: str
    caller_file: str
    caller_start: int
    caller_end: int


class GraphImporterInfo(BaseModel):
    file_path: str


class GraphOverview(BaseModel):
    files: list[GraphFileNode]
    classes: list[GraphClassNode]
    functions: list[GraphFunctionNode]
