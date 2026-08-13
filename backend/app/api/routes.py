from fastapi import APIRouter, HTTPException

from backend.app.api.schemas import (
    GraphCallerInfo,
    GraphClassInfo,
    GraphClassNode,
    GraphFileContents,
    GraphFileNode,
    GraphFunctionNode,
    GraphImporterInfo,
    GraphMethodNode,
    GraphOverview,
    QueryRequest,
    QueryResponse,
    QueryResult,
    RepositoryRequest,
    RepositoryResponse,
)
from backend.app.chunking.semantic import create_chunks_from_entities, create_file_chunks
from backend.app.embeddings.model import get_embedding_model
from backend.app.graph.builder import build_code_graph
from backend.app.graph.store import GraphStore
from backend.app.ingestion.repository import clone_repository, create_repository_metadata
from backend.app.models.entities import RepositoryMetadata, RepositoryStatus
from backend.app.parsing import parse_repository
from backend.app.retrieval.graph_retrieval import (
    get_caller_graph_context,
    get_class_graph_context,
    get_file_graph_context,
    get_importer_graph_context,
)
from backend.app.retrieval.vector_retrieval import retrieve_relevant_chunks
from backend.app.vector.store import VectorStore

router = APIRouter()

_in_memory_repos: dict[str, RepositoryMetadata] = {}
_vector_stores: dict[str, VectorStore] = {}
_graph_stores: dict[str, GraphStore] = {}


@router.post("/repositories", response_model=RepositoryResponse)
async def create_repository(req: RepositoryRequest):
    try:
        repo_id, repo_path = clone_repository(req.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to clone repository: {e}")

    metadata = create_repository_metadata(req.url, repo_id, repo_path)
    metadata.status = RepositoryStatus.PARSING
    _in_memory_repos[repo_id] = metadata

    entities = parse_repository(repo_path, repo_id)
    metadata.entity_count = len(entities)

    chunks = create_chunks_from_entities(entities)
    file_chunks = create_file_chunks(repo_path, entities)
    all_chunks = chunks + file_chunks

    embedding_model = get_embedding_model()
    vector_store = VectorStore(embedding_model=embedding_model)
    vector_store.store_chunks(all_chunks)
    _vector_stores[repo_id] = vector_store

    graph_store = GraphStore()
    build_code_graph(
        repo_id=repo_id,
        repo_url=req.url,
        repo_path=repo_path,
        entities=entities,
        graph_store=graph_store,
    )
    _graph_stores[repo_id] = graph_store

    metadata.status = RepositoryStatus.READY
    return RepositoryResponse(
        id=metadata.id,
        url=metadata.url,
        status=metadata.status.value,
        languages=metadata.languages,
        file_count=metadata.file_count,
        entity_count=metadata.entity_count,
    )


@router.get("/repositories/{repo_id}", response_model=RepositoryResponse)
async def get_repository(repo_id: str):
    metadata = _in_memory_repos.get(repo_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Repository not found")
    return RepositoryResponse(
        id=metadata.id,
        url=metadata.url,
        status=metadata.status.value,
        languages=metadata.languages,
        file_count=metadata.file_count,
        entity_count=metadata.entity_count,
    )


@router.post("/repositories/{repo_id}/query", response_model=QueryResponse)
async def query_repository(repo_id: str, req: QueryRequest):
    metadata = _in_memory_repos.get(repo_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Repository not found")
    if metadata.status != RepositoryStatus.READY:
        raise HTTPException(status_code=400, detail=f"Repository is {metadata.status.value}")

    vector_store = _vector_stores.get(repo_id)
    if not vector_store:
        raise HTTPException(status_code=500, detail="Vector store not available")

    results = retrieve_relevant_chunks(
        query=req.query, vector_store=vector_store, repository_id=repo_id
    )

    return QueryResponse(
        results=[
            QueryResult(
                score=r["score"],
                file_path=r["file_path"],
                entity_type=r["entity_type"],
                entity_name=r["entity_name"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                language=r["language"],
            )
            for r in results
        ],
        query=req.query,
    )


@router.get("/repositories/{repo_id}/graph/overview", response_model=GraphOverview)
async def graph_overview(repo_id: str):
    graph_store = _graph_stores.get(repo_id)
    if not graph_store:
        raise HTTPException(status_code=404, detail="Repository graph not found")

    overview = graph_store.get_repository_overview(repo_id)
    return GraphOverview(
        files=[GraphFileNode(**f) for f in overview["files"]],
        classes=[GraphClassNode(**c) for c in overview["classes"]],
        functions=[GraphFunctionNode(**fn) for fn in overview["functions"]],
    )


@router.get("/repositories/{repo_id}/graph/file", response_model=list[GraphFileContents])
async def graph_file(repo_id: str, file_path: str):
    graph_store = _graph_stores.get(repo_id)
    if not graph_store:
        raise HTTPException(status_code=404, detail="Repository graph not found")

    results = get_file_graph_context(repo_id, file_path, graph_store)
    if not results:
        return []

    record = results[0]
    f = record["f"]
    return [
        GraphFileContents(
            file_path=f.get("file_path", ""),
            classes=[
                GraphClassNode(
                    name=c.get("name", ""),
                    file_path=c.get("file_path", ""),
                    start_line=c.get("start_line", 0),
                    end_line=c.get("end_line", 0),
                )
                for c in record.get("classes", [])
            ],
            functions=[
                GraphFunctionNode(
                    name=fn.get("name", ""),
                    file_path=fn.get("file_path", ""),
                    start_line=fn.get("start_line", 0),
                    end_line=fn.get("end_line", 0),
                )
                for fn in record.get("functions", [])
            ],
            methods=[
                GraphMethodNode(
                    name=m.get("name", ""),
                    class_name=m.get("class_name", ""),
                    file_path=m.get("file_path", ""),
                    start_line=m.get("start_line", 0),
                    end_line=m.get("end_line", 0),
                )
                for m in record.get("methods", [])
            ],
        )
    ]


@router.get("/repositories/{repo_id}/graph/class", response_model=list[GraphClassInfo])
async def graph_class(repo_id: str, class_name: str):
    graph_store = _graph_stores.get(repo_id)
    if not graph_store:
        raise HTTPException(status_code=404, detail="Repository graph not found")

    results = get_class_graph_context(repo_id, class_name, graph_store)
    return [
        GraphClassInfo(
            class_name=r["class"].get("name", class_name),
            methods=[
                GraphMethodNode(
                    name=m.get("name", ""),
                    class_name=m.get("class_name", class_name),
                    file_path=m.get("file_path", ""),
                    start_line=m.get("start_line", 0),
                    end_line=m.get("end_line", 0),
                )
                for m in r.get("methods", [])
            ],
        )
        for r in results
    ]


@router.get(
    "/repositories/{repo_id}/graph/callers",
    response_model=list[GraphCallerInfo],
)
async def graph_callers(repo_id: str, function_name: str):
    graph_store = _graph_stores.get(repo_id)
    if not graph_store:
        raise HTTPException(status_code=404, detail="Repository graph not found")

    results = get_caller_graph_context(repo_id, function_name, graph_store)
    return [GraphCallerInfo(**r) for r in results]


@router.get(
    "/repositories/{repo_id}/graph/importers",
    response_model=list[GraphImporterInfo],
)
async def graph_importers(repo_id: str, file_path: str):
    graph_store = _graph_stores.get(repo_id)
    if not graph_store:
        raise HTTPException(status_code=404, detail="Repository graph not found")

    results = get_importer_graph_context(repo_id, file_path, graph_store)
    return [GraphImporterInfo(**r) for r in results]
