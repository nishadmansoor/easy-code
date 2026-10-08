"""Structural retrieval from the code graph.

Every function here answers a question the vector index cannot: what calls
this, what imports that, what inherits from this. Results come back as
:class:`RetrievedItem` objects plus plain-language facts that the LLM can cite.
"""

import logging

from backend.app.graph.store import GraphStore
from backend.app.retrieval.question_analysis import QuestionAnalysis, StructuralIntent
from backend.app.retrieval.ranking import (
    TIER_DEPENDENCY,
    TIER_DIRECT,
    TIER_FILE,
    TIER_RELATED,
    RetrievedItem,
    Source,
)

logger = logging.getLogger(__name__)

MAX_PER_QUERY = 12


def _item(
    file_path: str,
    entity_type: str,
    entity_name: str,
    start_line: int | None,
    end_line: int | None,
    tier: int,
    relationship: str = "",
    score: float = 0.9,
) -> RetrievedItem:
    return RetrievedItem(
        file_path=file_path or "",
        entity_type=(entity_type or "entity").lower(),
        entity_name=entity_name or "",
        start_line=int(start_line or 0),
        end_line=int(end_line or 0),
        source=Source.GRAPH,
        tier=tier,
        relationship=relationship,
        score=score,
    )


def find_definitions(
    graph_store: GraphStore, repository_id: str, names: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for name in names:
        for row in graph_store.find_entities(repository_id, name, limit=5):
            location = f"{row['file_path']}:{row['start_line']}-{row['end_line']}"
            qualified = (
                f"{row['class_name']}.{row['name']}" if row.get("class_name") else row["name"]
            )
            facts.append(f"{row['entity_type']} {qualified} is defined at {location}")
            items.append(
                _item(
                    row["file_path"],
                    row["entity_type"],
                    row["name"],
                    row["start_line"],
                    row["end_line"],
                    TIER_DIRECT,
                    relationship="definition",
                    score=1.0,
                )
            )
    return items, facts


def find_callers(
    graph_store: GraphStore, repository_id: str, names: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for name in names:
        rows = graph_store.get_function_callers(repository_id, name)[:MAX_PER_QUERY]
        if not rows:
            continue
        for row in rows:
            location = f"{row['caller_file']}:{row['caller_start']}-{row['caller_end']}"
            facts.append(
                f"{row['caller_type']} {row['caller_name']} ({location}) calls {name}"
            )
            items.append(
                _item(
                    row["caller_file"],
                    row["caller_type"],
                    row["caller_name"],
                    row["caller_start"],
                    row["caller_end"],
                    TIER_RELATED,
                    relationship=f"calls {name}",
                )
            )
    return items, facts


def find_callees(
    graph_store: GraphStore, repository_id: str, names: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for name in names:
        for row in graph_store.get_function_callees(repository_id, name)[:MAX_PER_QUERY]:
            location = f"{row['callee_file']}:{row['callee_start']}-{row['callee_end']}"
            facts.append(f"{name} calls {row['callee_name']} ({location})")
            items.append(
                _item(
                    row["callee_file"],
                    row["callee_type"],
                    row["callee_name"],
                    row["callee_start"],
                    row["callee_end"],
                    TIER_RELATED,
                    relationship=f"called by {name}",
                )
            )
    return items, facts


def find_inheritance(
    graph_store: GraphStore, repository_id: str, names: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for name in names:
        for row in graph_store.get_class_info(repository_id, name):
            bases = [b for b in row.get("bases", []) if b.get("name")]
            subclasses = [s for s in row.get("subclasses", []) if s.get("name")]
            if bases:
                facts.append(
                    f"class {name} inherits from "
                    + ", ".join(f"{b['name']} ({b['file_path']})" for b in bases)
                )
            if subclasses:
                facts.append(
                    f"classes inheriting from {name}: "
                    + ", ".join(f"{s['name']} ({s['file_path']})" for s in subclasses)
                )
            for related, relationship in (
                *[(b, f"base class of {name}") for b in bases],
                *[(s, f"subclass of {name}") for s in subclasses],
            ):
                items.append(
                    _item(
                        related["file_path"],
                        "class",
                        related["name"],
                        related.get("start_line"),
                        related.get("end_line"),
                        TIER_RELATED,
                        relationship=relationship,
                    )
                )
    return items, facts


def find_class_structure(
    graph_store: GraphStore, repository_id: str, names: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for name in names:
        for row in graph_store.get_class_info(repository_id, name):
            methods = [m for m in row.get("methods", []) if m.get("name")]
            if not methods:
                continue
            facts.append(
                f"class {name} ({row['file_path']}:{row['start_line']}-{row['end_line']}) "
                f"defines: " + ", ".join(sorted(m["name"] for m in methods))
            )
            for method in methods[:MAX_PER_QUERY]:
                items.append(
                    _item(
                        method["file_path"],
                        "method",
                        method["name"],
                        method["start_line"],
                        method["end_line"],
                        TIER_FILE,
                        relationship=f"method of {name}",
                        score=0.7,
                    )
                )
    return items, facts


def find_file_dependencies(
    graph_store: GraphStore, repository_id: str, file_paths: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for file_path in file_paths:
        importers = [
            row["file_path"] for row in graph_store.get_importers(repository_id, file_path)
        ]
        imported = [
            row["file_path"] for row in graph_store.get_imported_files(repository_id, file_path)
        ]
        if importers:
            facts.append(f"{file_path} is imported by: " + ", ".join(importers[:MAX_PER_QUERY]))
        if imported:
            facts.append(f"{file_path} imports: " + ", ".join(imported[:MAX_PER_QUERY]))
        # Name the target explicitly. "imports this file" is a dangling
        # reference: the model sees the item out of context and cannot tell
        # which file "this" is, nor which direction the edge runs.
        labelled = [(path, f"imports {file_path}") for path in importers] + [
            (path, f"imported by {file_path}") for path in imported
        ]
        for path, relationship in labelled[:MAX_PER_QUERY]:
            items.append(_item(path, "file", path, 0, 0, TIER_DEPENDENCY, relationship, score=0.6))
    return items, facts


def find_file_contents(
    graph_store: GraphStore, repository_id: str, file_paths: list[str]
) -> tuple[list[RetrievedItem], list[str]]:
    items: list[RetrievedItem] = []
    facts: list[str] = []
    for file_path in file_paths:
        record = graph_store.get_file_contents(repository_id, file_path)
        if not record:
            continue
        classes = record.get("classes", [])
        functions = record.get("functions", [])
        summary = []
        if classes:
            summary.append("classes " + ", ".join(sorted(c["name"] for c in classes)))
        if functions:
            summary.append("functions " + ", ".join(sorted(f["name"] for f in functions)))
        if summary:
            facts.append(f"{file_path} defines " + "; ".join(summary))

        entries = [(c, "class") for c in classes] + [(f, "function") for f in functions]
        for entry, kind in entries:
            items.append(
                _item(
                    file_path,
                    kind,
                    entry["name"],
                    entry.get("start_line"),
                    entry.get("end_line"),
                    TIER_FILE,
                    relationship=f"defined in {file_path}",
                    score=0.7,
                )
            )
    return items, facts


def find_architecture(
    graph_store: GraphStore, repository_id: str
) -> tuple[list[RetrievedItem], list[str]]:
    facts: list[str] = []
    items: list[RetrievedItem] = []

    central = graph_store.get_central_files(repository_id, limit=8)
    if central:
        facts.append(
            "Most depended-on files: "
            + ", ".join(f"{row['file_path']} ({row['importers']} importers)" for row in central)
        )
        for row in central[:5]:
            items.append(
                _item(row["file_path"], "file", row["file_path"], 0, 0, TIER_DEPENDENCY,
                      relationship=f"imported by {row['importers']} files", score=0.6)
            )

    entries = graph_store.get_entry_points(repository_id, limit=6)
    if entries:
        facts.append(
            "Files that nothing else imports (likely entry points): "
            + ", ".join(row["file_path"] for row in entries)
        )
        for row in entries[:4]:
            items.append(
                _item(row["file_path"], "file", row["file_path"], 0, 0, TIER_DEPENDENCY,
                      relationship="likely entry point", score=0.6)
            )
    return items, facts


def retrieve_graph_context(
    analysis: QuestionAnalysis, graph_store: GraphStore, repository_id: str
) -> tuple[list[RetrievedItem], list[str]]:
    """Run the graph queries implied by the question's intents."""
    items: list[RetrievedItem] = []
    facts: list[str] = []
    names = analysis.identifiers
    intents = set(analysis.intents)

    def collect(result: tuple[list[RetrievedItem], list[str]]) -> None:
        items.extend(result[0])
        facts.extend(result[1])

    if names:
        collect(find_definitions(graph_store, repository_id, names))
        collect(find_class_structure(graph_store, repository_id, names))

    if StructuralIntent.CALLERS in intents and names:
        collect(find_callers(graph_store, repository_id, names))
    if StructuralIntent.CALLEES in intents and names:
        collect(find_callees(graph_store, repository_id, names))
    if StructuralIntent.INHERITANCE in intents and names:
        collect(find_inheritance(graph_store, repository_id, names))
    if analysis.file_paths and (
        intents & {StructuralIntent.IMPORTERS, StructuralIntent.DEPENDENCIES}
    ):
        collect(find_file_dependencies(graph_store, repository_id, analysis.file_paths))
    if analysis.file_paths and StructuralIntent.FILE_CONTENTS in intents:
        collect(find_file_contents(graph_store, repository_id, analysis.file_paths))
    if intents & {StructuralIntent.ARCHITECTURE, StructuralIntent.ENTRY_POINT}:
        collect(find_architecture(graph_store, repository_id))

    # A flow question benefits from the call graph around every named entity
    # even when the question never says the word "calls".
    if analysis.is_flow_question and names:
        collect(find_callers(graph_store, repository_id, names))
        collect(find_callees(graph_store, repository_id, names))

    return items, facts


def expand_from_vector_hits(
    graph_store: GraphStore,
    repository_id: str,
    hits: list[RetrievedItem],
    max_seeds: int = 3,
) -> tuple[list[RetrievedItem], list[str]]:
    """Grow structural context around the strongest semantic matches.

    This is the core of the hybrid idea: the vector index finds *where* to look,
    and the graph explains how that code connects to the rest of the system.
    """
    items: list[RetrievedItem] = []
    facts: list[str] = []

    seeds = [hit for hit in hits if hit.entity_type in {"function", "method", "class"}][:max_seeds]
    for seed in seeds:
        for row in graph_store.get_entity_neighborhood(repository_id, seed.entity_name, limit=8):
            direction = "->" if row["outgoing"] else "<-"
            facts.append(
                f"{row['source_name']} {direction} {row['relationship']} {direction} "
                f"{row['other_name']}"
                + (f" ({row['other_file']})" if row.get("other_file") else "")
            )
            if row.get("other_file") and row.get("other_name"):
                items.append(
                    _item(
                        row["other_file"],
                        row["other_type"],
                        row["other_name"],
                        row.get("other_start"),
                        row.get("other_end"),
                        TIER_RELATED,
                        relationship=f"{row['relationship']} {seed.entity_name}",
                        score=0.65,
                    )
                )
    return items, facts
