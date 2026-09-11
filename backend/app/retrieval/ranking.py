"""Result ranking, deduplication and context assembly.

Retrieved items from Qdrant and Neo4j are merged into one ranked list. The
budget is deliberately small: sending everything to the LLM degrades both
answer quality and latency.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from backend.app.config.settings import settings


class Source(StrEnum):
    VECTOR = "vector"
    GRAPH = "graph"
    BOTH = "both"


#: Priority tiers from AGENT.md §19, highest first.
TIER_DIRECT = 0  # the entity the question names, or a top semantic match
TIER_RELATED = 1  # callers, callees, subclasses of a direct hit
TIER_DEPENDENCY = 2  # imports and importers
TIER_FILE = 3  # sibling definitions in a relevant file
TIER_DOC = 4  # documentation
TIER_CONTEXT = 5  # broad repository context

TIER_WEIGHTS = {
    TIER_DIRECT: 1.0,
    TIER_RELATED: 0.8,
    TIER_DEPENDENCY: 0.65,
    TIER_FILE: 0.5,
    TIER_DOC: 0.45,
    TIER_CONTEXT: 0.3,
}


@dataclass
class RetrievedItem:
    """One piece of evidence, from either retriever."""

    file_path: str
    entity_type: str
    entity_name: str
    start_line: int
    end_line: int
    content: str = ""
    language: str = ""
    score: float = 0.0
    source: Source = Source.VECTOR
    tier: int = TIER_CONTEXT
    relationship: str = ""
    reason: str = ""

    @property
    def citation(self) -> str:
        # File-level results have no line range; citing ``path:0-0`` would be
        # worse than useless, so they cite the path alone.
        if self.start_line <= 0:
            return self.file_path
        return f"{self.file_path}:{self.start_line}-{self.end_line}"

    @property
    def key(self) -> tuple[str, str, int, int]:
        return (self.file_path, self.entity_name, self.start_line, self.end_line)


@dataclass
class RetrievalContext:
    """Everything assembled for one question."""

    question: str
    question_type: str
    items: list[RetrievedItem] = field(default_factory=list)
    graph_facts: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    vector_hit_count: int = 0
    graph_hit_count: int = 0

    @property
    def cited_files(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            if item.file_path not in seen:
                seen.append(item.file_path)
        return seen


def deduplicate(items: list[RetrievedItem]) -> list[RetrievedItem]:
    """Merge items describing the same code region.

    An item found by both retrievers is stronger evidence than one found by
    either alone, so merging records ``Source.BOTH`` and keeps the better tier.
    """
    merged: dict[tuple[str, str, int, int], RetrievedItem] = {}
    for item in items:
        existing = merged.get(item.key)
        if existing is None:
            merged[item.key] = item
            continue

        if existing.source != item.source:
            existing.source = Source.BOTH
        existing.tier = min(existing.tier, item.tier)
        existing.score = max(existing.score, item.score)
        if not existing.content and item.content:
            existing.content = item.content
        if item.relationship and item.relationship not in existing.relationship:
            existing.relationship = (
                f"{existing.relationship}, {item.relationship}".strip(", ")
                if existing.relationship
                else item.relationship
            )
    return list(merged.values())


def _overlaps(a: RetrievedItem, b: RetrievedItem) -> bool:
    return (
        a.file_path == b.file_path
        and a.start_line <= b.end_line
        and b.start_line <= a.end_line
    )


def remove_contained(items: list[RetrievedItem]) -> list[RetrievedItem]:
    """Drop items fully contained in a higher-ranked item from the same file."""
    kept: list[RetrievedItem] = []
    for item in items:
        contained = any(
            _overlaps(item, other)
            and other.start_line <= item.start_line
            and other.end_line >= item.end_line
            and other.key != item.key
            for other in kept
        )
        if not contained:
            kept.append(item)
    return kept


def rank(items: list[RetrievedItem], limit: int, per_file_cap: int = 4) -> list[RetrievedItem]:
    """Rank by tier-weighted score, then enforce diversity across files.

    ``per_file_cap`` stops one large file from crowding out the rest of the
    repository, which matters for coverage on architecture questions. It is a
    soft cap: once every other file has been offered, capped items backfill any
    remaining budget rather than returning fewer results than asked for.
    """
    unique = deduplicate(items)

    def sort_key(item: RetrievedItem) -> tuple:
        weight = TIER_WEIGHTS.get(item.tier, 0.3)
        boost = 1.15 if item.source == Source.BOTH else 1.0
        return (-(weight * boost * max(item.score, 0.01)), item.tier, item.file_path)

    ordered = sorted(unique, key=sort_key)
    ordered = remove_contained(ordered)

    selected: list[RetrievedItem] = []
    per_file: dict[str, int] = {}
    overflow: list[RetrievedItem] = []

    for item in ordered:
        if len(selected) >= limit:
            break
        if per_file.get(item.file_path, 0) >= per_file_cap:
            overflow.append(item)
            continue
        selected.append(item)
        per_file[item.file_path] = per_file.get(item.file_path, 0) + 1

    # Backfill from overflow only if the cap left the budget unspent.
    for item in overflow:
        if len(selected) >= limit:
            break
        selected.append(item)

    return selected


def assemble_context_text(context: RetrievalContext, max_chars: int | None = None) -> str:
    """Render retrieved evidence as the text block given to the LLM.

    Every snippet carries its exact citation so the model can only cite
    locations that were actually retrieved.
    """
    budget = max_chars or settings.retrieval_max_context_chars
    sections: list[str] = []
    used = 0

    if context.graph_facts:
        facts = "\n".join(f"- {fact}" for fact in context.graph_facts)
        block = f"## Structural facts from the code graph\n{facts}"
        sections.append(block)
        used += len(block)

    snippets: list[str] = []
    for index, item in enumerate(context.items, start=1):
        header = (
            f"### [{index}] {item.entity_type} {item.entity_name}\n"
            f"Location: {item.citation}"
        )
        if item.relationship:
            header += f"\nRelationship: {item.relationship}"

        body = item.content.strip()
        block = f"{header}\n```{item.language or ''}\n{body}\n```" if body else header

        if used + len(block) > budget:
            remaining = budget - used
            if remaining < 400:
                break
            block = block[:remaining] + "\n... (truncated)\n```"
        snippets.append(block)
        used += len(block)

    if snippets:
        sections.append("## Retrieved code\n\n" + "\n\n".join(snippets))

    return "\n\n".join(sections) if sections else "No relevant context was retrieved."
