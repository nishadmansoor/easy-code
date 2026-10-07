"""The critical experiment: vector-only RAG versus graph + vector hybrid.

Both systems are given exactly the same index, the same context budget and the
same LLM. The only difference is whether structural information from Neo4j is
available during retrieval, so any difference in the scores is attributable to
the graph.
"""

import json
import logging
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from backend.app.config.settings import settings
from backend.app.evaluation.benchmark import Benchmark, BenchmarkQuestion
from backend.app.evaluation.metrics import (
    QuestionResult,
    aggregate,
    citation_accuracy,
    citation_coverage,
    entity_coverage,
    mention_coverage,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    relevant_file_rate,
)
from backend.app.generation.answer import INSUFFICIENT_EVIDENCE, generate_answer
from backend.app.graph import build_graph_store
from backend.app.ingestion.pipeline import index_repository, register_repository, repository_path
from backend.app.retrieval.hybrid import retrieve_hybrid, retrieve_vector_only
from backend.app.storage.database import RepositoryStore
from backend.app.vector.store import VectorStore

logger = logging.getLogger(__name__)

SYSTEMS = ("vector", "hybrid")


def prepare_repository(
    benchmark: Benchmark, store: RepositoryStore, reuse_existing: bool = True
) -> str:
    """Index the benchmark repository and return its id.

    A local fixture is copied into the workspace rather than cloned, which
    keeps the deterministic benchmark independent of the network.
    """
    if reuse_existing:
        for metadata in store.list():
            if metadata.status == "ready" and (
                metadata.url == benchmark.url
                or metadata.name == benchmark.name
            ):
                logger.info("Reusing indexed repository %s", metadata.id)
                return metadata.id

    if benchmark.local_path is not None:
        return _index_local(benchmark, store)

    metadata = register_repository(benchmark.url, store)
    result = index_repository(metadata.id, store, generate_repository_overview=False)
    if result is None or result.status != "ready":
        raise RuntimeError(f"Indexing failed: {result.error if result else 'unknown error'}")
    return metadata.id


def _index_local(benchmark: Benchmark, store: RepositoryStore) -> str:
    """Index a fixture directory by staging it as if it had been cloned."""
    from backend.app.models.entities import RepositoryMetadata, RepositoryStatus

    repo_id = f"bench-{benchmark.name}"
    metadata = store.get(repo_id)
    if metadata is None:
        metadata = store.create(
            RepositoryMetadata(
                id=repo_id,
                url=f"file://{benchmark.local_path}",
                name=benchmark.name,
                status=RepositoryStatus.QUEUED,
            )
        )

    workspace = repository_path(repo_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(benchmark.local_path, workspace)

    _index_workspace(repo_id, store)
    return repo_id


def _index_workspace(repo_id: str, store: RepositoryStore) -> None:
    """Run the indexing steps against an already-staged workspace."""
    from backend.app.chunking.semantic import build_chunks
    from backend.app.graph.builder import build_code_graph
    from backend.app.ingestion.repository import (
        detect_frameworks,
        detect_languages,
        iter_repository_files,
        relative_path,
    )
    from backend.app.models.entities import RepositoryStatus
    from backend.app.parsing import parse_repository

    workspace = repository_path(repo_id)
    metadata = store.get(repo_id)

    language_counts = detect_languages(workspace)
    files = iter_repository_files(workspace)
    parsed = parse_repository(workspace, repo_id)
    chunks = build_chunks(parsed.entities)

    vector_store = VectorStore()
    vector_store.delete_repository(repo_id)
    chunk_count = vector_store.store_chunks(chunks)

    graph_store = build_graph_store()
    try:
        from backend.app.ingestion.repository import LANGUAGE_EXTENSIONS

        stats = build_code_graph(
            repo_id=repo_id,
            repo_url=metadata.url,
            parsed=parsed,
            graph_store=graph_store,
            repo_name=metadata.name,
            files=[
                {
                    "file_path": relative_path(path, workspace),
                    "language": LANGUAGE_EXTENSIONS.get(path.suffix.lower(), "unknown"),
                }
                for path in files
            ],
        )
    finally:
        graph_store.close()

    store.update(
        repo_id,
        status=RepositoryStatus.READY,
        languages=list(language_counts.keys()),
        language_counts=language_counts,
        frameworks=detect_frameworks(workspace),
        file_count=len(files),
        entity_count=len(parsed.entities),
        chunk_count=chunk_count,
        graph_node_count=stats["node_count"],
        graph_relationship_count=stats["relationship_count"],
        error=None,
    )


def evaluate_question(
    question: BenchmarkQuestion,
    system: str,
    repository_id: str,
    vector_store: VectorStore,
    graph_store,
    generate: bool = True,
) -> QuestionResult:
    """Score one question against one system."""
    started = time.perf_counter()
    if system == "vector":
        context = retrieve_vector_only(question.question, repository_id, vector_store)
    else:
        context = retrieve_hybrid(
            question.question,
            repository_id,
            vector_store,
            graph_store,
            repo_path=repository_path(repository_id),
        )
    retrieval_seconds = time.perf_counter() - started

    retrieved_files = [item.file_path for item in context.items]
    retrieved_entities = [item.entity_name for item in context.items]

    result = QuestionResult(
        question_id=question.id,
        category=question.category.value,
        question=question.question,
        system=system,
        question_type=context.question_type,
        retrieved_files=retrieved_files,
        recall_at_1=recall_at_k(retrieved_files, question.relevant_files, 1),
        recall_at_3=recall_at_k(retrieved_files, question.relevant_files, 3),
        recall_at_5=recall_at_k(retrieved_files, question.relevant_files, 5),
        recall_at_10=recall_at_k(retrieved_files, question.relevant_files, 10),
        precision_at_5=precision_at_k(retrieved_files, question.relevant_files, 5),
        mrr=reciprocal_rank(retrieved_files, question.relevant_files),
        relevant_file_rate=relevant_file_rate(retrieved_files, question.relevant_files),
        entity_coverage=entity_coverage(retrieved_entities, question.relevant_entities),
        retrieval_seconds=round(retrieval_seconds, 3),
    )

    if not generate:
        return result

    answer = generate_answer(context)
    citations = [str(citation) for citation in answer.citations]

    result.answer = answer.answer
    result.citations = citations
    result.unverified_citations = answer.unverified_citations
    result.mention_coverage = mention_coverage(answer.answer, question.expected_mentions)
    result.citation_accuracy = citation_accuracy(
        len(answer.citations), len(answer.unverified_citations)
    )
    result.citation_coverage = citation_coverage(citations, question.relevant_files)
    # An answer that cites a location which was never retrieved is the concrete,
    # measurable form of hallucination this system is designed to prevent.
    result.hallucinated = bool(answer.unverified_citations)
    result.abstained = INSUFFICIENT_EVIDENCE in answer.answer
    result.generation_seconds = round(answer.latency_seconds, 3)
    return result


def run_benchmark(
    benchmark: Benchmark,
    systems: tuple[str, ...] = SYSTEMS,
    generate: bool = True,
    store: RepositoryStore | None = None,
    reuse_existing: bool = True,
) -> dict:
    """Run every question against every system and aggregate the scores."""
    store = store or RepositoryStore()
    repository_id = prepare_repository(benchmark, store, reuse_existing=reuse_existing)
    metadata = store.get(repository_id)

    vector_store = VectorStore()
    graph_store = build_graph_store()

    results: list[QuestionResult] = []
    try:
        for system in systems:
            for question in benchmark.questions:
                logger.info("[%s] %s", system, question.question)
                results.append(
                    evaluate_question(
                        question, system, repository_id, vector_store, graph_store, generate
                    )
                )
    finally:
        graph_store.close()

    aggregates = {
        system: aggregate(system, [r for r in results if r.system == system])
        for system in systems
    }

    return {
        "benchmark": benchmark.name,
        "repository_id": repository_id,
        "repository": {
            "url": metadata.url,
            "files": metadata.file_count,
            "entities": metadata.entity_count,
            "chunks": metadata.chunk_count,
            "graph_nodes": metadata.graph_node_count,
            "graph_relationships": metadata.graph_relationship_count,
            "indexing_seconds": metadata.indexing_seconds,
        },
        "configuration": {
            "embedding_model": settings.embedding_model_name,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.ollama_model,
            "context_limit": settings.retrieval_context_limit,
            "generation_enabled": generate,
        },
        "aggregates": {system: asdict(metrics) for system, metrics in aggregates.items()},
        "results": [asdict(result) for result in results],
    }


def compare(report: dict) -> dict:
    """Hybrid minus baseline for the headline metrics."""
    aggregates = report["aggregates"]
    if "vector" not in aggregates or "hybrid" not in aggregates:
        return {}

    baseline, hybrid = aggregates["vector"], aggregates["hybrid"]
    metrics = [
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "precision_at_5",
        "mrr",
        "relevant_file_rate",
        "entity_coverage",
        "mean_retrieval_seconds",
    ]
    # Answer-quality metrics are meaningless when no answer was generated.
    if report.get("configuration", {}).get("generation_enabled", True):
        metrics += [
            "mention_coverage",
            "citation_accuracy",
            "citation_coverage",
            "hallucination_rate",
            "mean_generation_seconds",
        ]
    return {
        metric: {
            "vector": baseline[metric],
            "hybrid": hybrid[metric],
            "delta": round(hybrid[metric] - baseline[metric], 4),
        }
        for metric in metrics
    }


def failure_cases(report: dict, limit: int = 10) -> list[dict]:
    """Questions where the hybrid system missed every ground-truth file."""
    failures = [
        {
            "question_id": result["question_id"],
            "question": result["question"],
            "category": result["category"],
            "system": result["system"],
            "question_type": result["question_type"],
            "retrieved_files": result["retrieved_files"][:5],
            "hallucinated": result["hallucinated"],
        }
        for result in report["results"]
        if result["relevant_file_rate"] == 0.0 or result["hallucinated"]
    ]
    return failures[:limit]


def format_report(report: dict) -> str:
    """Human-readable summary, printed by the evaluation script."""
    lines: list[str] = []
    repo = report["repository"]
    lines.append(f"# EasyCode evaluation — {report['benchmark']}")
    lines.append("")
    lines.append(
        f"Repository: {repo['url']} — {repo['files']} files, {repo['entities']} entities, "
        f"{repo['chunks']} chunks, {repo['graph_nodes']} graph nodes, "
        f"{repo['graph_relationships']} relationships"
    )
    config = report["configuration"]
    lines.append(
        f"Embeddings: {config['embedding_model']} | "
        f"LLM: {config['llm_provider']}/{config['llm_model']} | "
        f"context limit: {config['context_limit']}"
    )
    lines.append("")

    comparison = compare(report)
    if comparison:
        lines.append("## Vector-only baseline vs graph + vector hybrid")
        lines.append("")
        lines.append("| Metric | Vector-only | Hybrid | Delta |")
        lines.append("| --- | ---: | ---: | ---: |")
        for metric, values in comparison.items():
            delta = values["delta"]
            arrow = "+" if delta > 0 else ""
            lines.append(
                f"| {metric} | {values['vector']:.3f} | {values['hybrid']:.3f} "
                f"| {arrow}{delta:.3f} |"
            )
        lines.append("")

    generated = config.get("generation_enabled", True)
    for system, metrics in report["aggregates"].items():
        lines.append(f"## {system} — by question category")
        lines.append("")
        header = "| Category | N | Recall@5 | MRR | Relevant file rate |"
        divider = "| --- | ---: | ---: | ---: | ---: |"
        if generated:
            header += " Mention coverage |"
            divider += " ---: |"
        lines.append(header)
        lines.append(divider)
        for category, values in metrics["by_category"].items():
            row = (
                f"| {category} | {int(values['count'])} | {values['recall_at_5']:.3f} "
                f"| {values['mrr']:.3f} | {values['relevant_file_rate']:.3f} |"
            )
            if generated:
                row += f" {values['mention_coverage']:.3f} |"
            lines.append(row)
        lines.append("")

    failures = failure_cases(report)
    if failures:
        lines.append("## Failure cases")
        lines.append("")
        for failure in failures:
            reason = "hallucinated citation" if failure["hallucinated"] else "no relevant file"
            lines.append(
                f"- [{failure['system']}] {failure['question']} ({failure['category']}, "
                f"classified {failure['question_type']}) — {reason}; "
                f"retrieved {failure['retrieved_files']}"
            )
        lines.append("")

    return "\n".join(lines)


def save_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"evaluation-{report['benchmark']}.json"
    markdown_path = output_dir / f"evaluation-{report['benchmark']}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(format_report(report), encoding="utf-8")
    return json_path, markdown_path
