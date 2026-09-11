"""Automatic repository overview.

Produced once at the end of indexing so the user has a mental model before
asking anything. It draws on the README, the directory layout and the graph's
view of which files matter most.
"""

import logging
from collections import Counter

from backend.app.generation.prompts import OVERVIEW_SYSTEM_PROMPT, build_overview_prompt
from backend.app.generation.provider import LLMProvider, LLMUnavailableError, get_provider
from backend.app.graph.store import GraphStore
from backend.app.models.entities import RepositoryMetadata
from backend.app.vector.store import VectorStore

logger = logging.getLogger(__name__)

MAX_OVERVIEW_CONTEXT_CHARS = 12000

#: Seed queries that pull in the material an overview needs.
SEED_QUERIES = [
    "what this project does, its purpose and main features",
    "application entry point, main function, server startup",
    "core data models and primary abstractions",
]


def summarize_structure(graph_store: GraphStore, repository_id: str) -> str:
    """Directory layout, central files and entry points, from the graph."""
    lines: list[str] = []

    files = graph_store.get_files(repository_id)
    if files:
        directories = Counter(
            path["file_path"].rsplit("/", 1)[0] if "/" in path["file_path"] else "(root)"
            for path in files
        )
        top = directories.most_common(12)
        lines.append(
            "Directories: "
            + ", ".join(f"{name} ({count} files)" for name, count in top)
        )

    central = graph_store.get_central_files(repository_id, limit=8)
    if central:
        lines.append(
            "Most imported files: "
            + ", ".join(f"{row['file_path']} ({row['importers']} importers)" for row in central)
        )

    entries = graph_store.get_entry_points(repository_id, limit=6)
    if entries:
        lines.append(
            "Files nothing else imports (likely entry points): "
            + ", ".join(row["file_path"] for row in entries)
        )

    stats = graph_store.get_statistics(repository_id)
    if stats["nodes"]:
        lines.append(
            "Graph contents: "
            + ", ".join(f"{count} {label.lower()}s" for label, count in stats["nodes"].items())
        )

    return "\n".join(f"- {line}" for line in lines) or "- No structural information available."


def collect_overview_context(
    vector_store: VectorStore, repository_id: str, limit_per_query: int = 5
) -> str:
    """README sections and representative code, gathered by seed queries."""
    blocks: list[str] = []
    seen: set[tuple[str, int]] = set()
    used = 0

    docs = vector_store.search(
        query="project overview readme introduction what this does",
        repository_id=repository_id,
        limit=4,
        entity_types=["doc_section"],
    )
    code = []
    for query in SEED_QUERIES:
        code.extend(
            vector_store.search(
                query=query,
                repository_id=repository_id,
                limit=limit_per_query,
                entity_types=["module", "class", "function"],
            )
        )

    for hit in docs + code:
        key = (hit["file_path"], hit["start_line"])
        if key in seen:
            continue
        seen.add(key)

        block = (
            f"### {hit['entity_type']} {hit['entity_name']} "
            f"({hit['file_path']}:{hit['start_line']}-{hit['end_line']})\n"
            f"{hit['content'][:1500]}"
        )
        if used + len(block) > MAX_OVERVIEW_CONTEXT_CHARS:
            break
        blocks.append(block)
        used += len(block)

    return "\n\n".join(blocks) or "No documentation or code context available."


def generate_overview(
    metadata: RepositoryMetadata,
    vector_store: VectorStore,
    graph_store: GraphStore,
    provider: LLMProvider | None = None,
) -> str:
    """Write the repository overview, or a structural digest if no LLM is up."""
    provider = provider or get_provider()
    structure = summarize_structure(graph_store, metadata.id)
    context = collect_overview_context(vector_store, metadata.id)

    prompt = build_overview_prompt(
        repository=metadata.name or metadata.url,
        languages=", ".join(metadata.languages),
        frameworks=", ".join(metadata.frameworks),
        file_count=metadata.file_count,
        structure=structure,
        context=context,
    )

    try:
        response = provider.generate(prompt, system=OVERVIEW_SYSTEM_PROMPT)
        if response.text.strip():
            return response.text.strip()
    except LLMUnavailableError as exc:
        logger.warning("Overview generation failed: %s", exc)

    return _structural_overview(metadata, structure)


def _structural_overview(metadata: RepositoryMetadata, structure: str) -> str:
    """Facts-only overview used when no language model is available."""
    lines = [
        f"# {metadata.name or metadata.url}",
        "",
        "No language model is configured, so this overview reports indexed facts "
        "rather than a written explanation.",
        "",
        f"- Languages: {', '.join(metadata.languages) or 'unknown'}",
        f"- Frameworks detected: {', '.join(metadata.frameworks) or 'none detected'}",
        f"- Files indexed: {metadata.file_count}",
        f"- Code entities extracted: {metadata.entity_count}",
        "",
        "## Structure",
        structure,
    ]
    return "\n".join(lines)
