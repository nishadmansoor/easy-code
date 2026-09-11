"""HTTP API.

Indexing runs in the background so ``POST /repositories`` returns immediately
with a ``queued`` record; clients poll ``GET /repositories/{id}/status``.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from backend.app.api.dependencies import (
    get_graph_store,
    get_store,
    get_vector_store,
    service_health,
)
from backend.app.api.schemas import (
    CitationModel,
    FileContentResponse,
    FileEntry,
    FileListResponse,
    GraphCalleeInfo,
    GraphCallerInfo,
    GraphClassInfo,
    GraphFileContents,
    GraphImporterInfo,
    GraphNode,
    GraphOverview,
    GraphOverviewFile,
    HealthResponse,
    OverviewResponse,
    QueryRequest,
    QueryResponse,
    RepositoryRequest,
    RepositoryResponse,
    SourceReference,
    StatusResponse,
)
from backend.app.config.settings import settings
from backend.app.generation.answer import generate_answer
from backend.app.generation.provider import get_provider
from backend.app.ingestion.pipeline import (
    delete_repository,
    index_repository,
    register_repository,
    repository_path,
)
from backend.app.ingestion.repository import IngestionError
from backend.app.models.entities import RepositoryMetadata, RepositoryStatus
from backend.app.retrieval.hybrid import retrieve_hybrid, retrieve_vector_only

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_PREVIEW_BYTES = 400_000


def _to_response(metadata: RepositoryMetadata) -> RepositoryResponse:
    return RepositoryResponse(**metadata.model_dump(exclude={"overview"}))


def _require_repository(repo_id: str) -> RepositoryMetadata:
    metadata = get_store().get(repo_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return metadata


def _require_ready(repo_id: str) -> RepositoryMetadata:
    metadata = _require_repository(repo_id)
    if metadata.status != RepositoryStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=f"Repository is not ready (status: {metadata.status.value})",
        )
    return metadata


def _run_indexing(repository_id: str) -> None:
    """Background entry point. Never raises into the event loop."""
    try:
        index_repository(repository_id, get_store())
    except Exception:
        logger.exception("Background indexing crashed for %s", repository_id)


# --------------------------------------------------------------------------- #
# Repositories
# --------------------------------------------------------------------------- #


@router.post("/repositories", response_model=RepositoryResponse, status_code=202)
async def create_repository(req: RepositoryRequest, background: BackgroundTasks):
    try:
        metadata = register_repository(req.url, get_store())
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background.add_task(_run_indexing, metadata.id)
    return _to_response(metadata)


@router.get("/repositories", response_model=list[RepositoryResponse])
async def list_repositories():
    return [_to_response(metadata) for metadata in get_store().list()]


@router.get("/repositories/{repo_id}", response_model=RepositoryResponse)
async def get_repository(repo_id: str):
    return _to_response(_require_repository(repo_id))


@router.get("/repositories/{repo_id}/status", response_model=StatusResponse)
async def get_status(repo_id: str):
    metadata = _require_repository(repo_id)
    return StatusResponse(
        id=metadata.id,
        status=metadata.status.value,
        error=metadata.error,
        file_count=metadata.file_count,
        entity_count=metadata.entity_count,
        chunk_count=metadata.chunk_count,
        graph_node_count=metadata.graph_node_count,
        indexing_seconds=metadata.indexing_seconds,
    )


@router.get("/repositories/{repo_id}/overview", response_model=OverviewResponse)
async def get_overview(repo_id: str):
    metadata = _require_repository(repo_id)
    return OverviewResponse(
        id=metadata.id,
        name=metadata.name,
        overview=metadata.overview,
        languages=metadata.languages,
        frameworks=metadata.frameworks,
        file_count=metadata.file_count,
        entity_count=metadata.entity_count,
    )


@router.delete("/repositories/{repo_id}", status_code=204)
async def remove_repository(repo_id: str):
    _require_repository(repo_id)
    delete_repository(repo_id, get_store())
    return None


# --------------------------------------------------------------------------- #
# Query
# --------------------------------------------------------------------------- #


@router.post("/repositories/{repo_id}/query", response_model=QueryResponse)
async def query_repository(repo_id: str, req: QueryRequest):
    metadata = _require_ready(repo_id)

    if req.mode == "vector":
        context = retrieve_vector_only(
            question=req.query,
            repository_id=repo_id,
            vector_store=get_vector_store(),
            context_limit=req.limit,
        )
    elif req.mode == "hybrid":
        context = retrieve_hybrid(
            question=req.query,
            repository_id=repo_id,
            vector_store=get_vector_store(),
            graph_store=get_graph_store(),
            repo_path=repository_path(repo_id),
            context_limit=req.limit,
        )
    else:
        raise HTTPException(status_code=400, detail="mode must be 'hybrid' or 'vector'")

    sources = [
        SourceReference(
            file_path=item.file_path,
            start_line=item.start_line,
            end_line=item.end_line,
            entity_type=item.entity_type,
            entity_name=item.entity_name,
            source=item.source.value,
            relationship=item.relationship,
            score=round(item.score, 4),
            content=item.content,
        )
        for item in context.items
    ]

    if not req.generate:
        return QueryResponse(
            query=req.query,
            question_type=context.question_type,
            sources=sources,
            graph_facts=context.graph_facts,
            vector_hits=context.vector_hit_count,
            graph_hits=context.graph_hit_count,
        )

    result = generate_answer(context, repository_name=metadata.name)
    return QueryResponse(
        query=req.query,
        question_type=context.question_type,
        answer=result.answer,
        citations=[
            CitationModel(
                file_path=c.file_path, start_line=c.start_line, end_line=c.end_line
            )
            for c in result.citations
        ],
        unverified_citations=result.unverified_citations,
        grounded=result.grounded,
        sources=sources,
        graph_facts=context.graph_facts,
        vector_hits=context.vector_hit_count,
        graph_hits=context.graph_hit_count,
        provider=result.provider,
        model=result.model,
        latency_seconds=round(result.latency_seconds, 3),
    )


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #


@router.get("/repositories/{repo_id}/files", response_model=FileListResponse)
async def list_files(repo_id: str):
    _require_ready(repo_id)
    files = get_graph_store().get_files(repo_id)
    return FileListResponse(
        files=[FileEntry(file_path=f["file_path"], language=f["language"] or "") for f in files]
    )


@router.get("/repositories/{repo_id}/file", response_model=FileContentResponse)
async def get_file_content(repo_id: str, file_path: str = Query(..., min_length=1)):
    """Read one file from the cloned workspace, for source navigation."""
    _require_ready(repo_id)
    root = repository_path(repo_id).resolve()
    target = (root / file_path).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path is outside the repository") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > MAX_FILE_PREVIEW_BYTES:
        raise HTTPException(status_code=413, detail="File is too large to display")

    content = target.read_text(encoding="utf-8", errors="replace")
    from backend.app.ingestion.repository import LANGUAGE_EXTENSIONS

    return FileContentResponse(
        file_path=file_path,
        language=LANGUAGE_EXTENSIONS.get(target.suffix.lower(), ""),
        content=content,
        line_count=len(content.splitlines()),
    )


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #


@router.get("/repositories/{repo_id}/graph", response_model=GraphOverview)
async def graph_overview(repo_id: str):
    _require_ready(repo_id)
    graph_store = get_graph_store()
    overview = graph_store.get_repository_overview(repo_id)
    return GraphOverview(
        files=[
            GraphOverviewFile(
                file_path=row["file_path"],
                language=row["language"] or "",
                classes=[name for name in row["classes"] if name],
                functions=[name for name in row["functions"] if name],
            )
            for row in overview["files"]
        ],
        statistics=graph_store.get_statistics(repo_id),
        central_files=graph_store.get_central_files(repo_id, limit=10),
        entry_points=graph_store.get_entry_points(repo_id, limit=10),
    )


@router.get("/repositories/{repo_id}/graph/file", response_model=GraphFileContents)
async def graph_file(repo_id: str, file_path: str = Query(..., min_length=1)):
    _require_ready(repo_id)
    record = get_graph_store().get_file_contents(repo_id, file_path)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found in the graph")
    return GraphFileContents(
        file_path=record["file_path"],
        language=record["language"] or "",
        classes=[GraphNode(**_node(c)) for c in record["classes"]],
        functions=[GraphNode(**_node(f)) for f in record["functions"]],
        methods=[GraphNode(**_node(m)) for m in record["methods"]],
    )


@router.get("/repositories/{repo_id}/graph/class", response_model=list[GraphClassInfo])
async def graph_class(repo_id: str, class_name: str = Query(..., min_length=1)):
    _require_ready(repo_id)
    rows = get_graph_store().get_class_info(repo_id, class_name)
    return [
        GraphClassInfo(
            class_name=row["class_name"],
            file_path=row["file_path"] or "",
            start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0,
            methods=[GraphNode(**_node(m)) for m in row["methods"] if m.get("name")],
            bases=[GraphNode(**_node(b)) for b in row["bases"] if b.get("name")],
            subclasses=[GraphNode(**_node(s)) for s in row["subclasses"] if s.get("name")],
        )
        for row in rows
    ]


@router.get("/repositories/{repo_id}/graph/callers", response_model=list[GraphCallerInfo])
async def graph_callers(repo_id: str, function_name: str = Query(..., min_length=1)):
    _require_ready(repo_id)
    return [
        GraphCallerInfo(
            caller_type=row["caller_type"],
            caller_name=row["caller_name"],
            caller_file=row["caller_file"],
            caller_start=row["caller_start"] or 0,
            caller_end=row["caller_end"] or 0,
        )
        for row in get_graph_store().get_function_callers(repo_id, function_name)
    ]


@router.get("/repositories/{repo_id}/graph/callees", response_model=list[GraphCalleeInfo])
async def graph_callees(repo_id: str, function_name: str = Query(..., min_length=1)):
    _require_ready(repo_id)
    return [
        GraphCalleeInfo(
            callee_type=row["callee_type"],
            callee_name=row["callee_name"],
            callee_file=row["callee_file"],
            callee_start=row["callee_start"] or 0,
            callee_end=row["callee_end"] or 0,
        )
        for row in get_graph_store().get_function_callees(repo_id, function_name)
    ]


@router.get("/repositories/{repo_id}/graph/importers", response_model=list[GraphImporterInfo])
async def graph_importers(repo_id: str, file_path: str = Query(..., min_length=1)):
    _require_ready(repo_id)
    return [
        GraphImporterInfo(file_path=row["file_path"])
        for row in get_graph_store().get_importers(repo_id, file_path)
    ]


@router.get("/repositories/{repo_id}/graph/imports", response_model=list[GraphImporterInfo])
async def graph_imports(repo_id: str, file_path: str = Query(..., min_length=1)):
    _require_ready(repo_id)
    return [
        GraphImporterInfo(file_path=row["file_path"])
        for row in get_graph_store().get_imported_files(repo_id, file_path)
    ]


@router.get("/repositories/{repo_id}/graph/path")
async def graph_dependency_path(
    repo_id: str,
    source: str = Query(..., min_length=1),
    target: str = Query(..., min_length=1),
):
    _require_ready(repo_id)
    rows = get_graph_store().get_dependency_chain(repo_id, source, target)
    return {"chains": [row["chain"] for row in rows]}


def _node(record: dict) -> dict:
    return {
        "name": record.get("name") or "",
        "file_path": record.get("file_path") or "",
        "start_line": record.get("start_line") or 0,
        "end_line": record.get("end_line") or 0,
        "signature": record.get("signature"),
        "class_name": record.get("class_name"),
    }


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@router.get("/health", response_model=HealthResponse)
async def health():
    qdrant_ok, neo4j_ok = service_health()
    provider = get_provider()
    return HealthResponse(
        status="ok" if qdrant_ok and neo4j_ok else "degraded",
        qdrant=qdrant_ok,
        neo4j=neo4j_ok,
        llm_provider=provider.name,
        llm_model=provider.model_name,
        llm_available=provider.name != "extractive",
    )


@router.get("/config")
async def config():
    """Non-sensitive runtime configuration, for the UI and for debugging."""
    return {
        "embedding_model": settings.embedding_model_name,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.ollama_model,
        "retrieval_context_limit": settings.retrieval_context_limit,
        "repos_dir": str(Path(settings.repos_dir)),
    }
