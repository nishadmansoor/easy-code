"""Evaluation metrics.

Retrieval metrics operate on the ranked list of retrieved files. Answer metrics
are coarse automatic proxies — they measure whether the expected components are
mentioned and whether citations are supported, not whether the prose is good.
Anything claiming to measure prose quality would need human raters, and this
module does not pretend otherwise.
"""

from dataclasses import dataclass, field


def _ranked_files(retrieved_files: list[str]) -> list[str]:
    """Ranked retrieved files with duplicates removed, best rank kept."""
    seen: list[str] = []
    for path in retrieved_files:
        if path not in seen:
            seen.append(path)
    return seen


def recall_at_k(retrieved_files: list[str], relevant_files: list[str], k: int) -> float:
    """Fraction of ground-truth files appearing in the top ``k`` retrieved files."""
    if not relevant_files:
        return 1.0
    top_k = set(_ranked_files(retrieved_files)[:k])
    hits = sum(1 for path in relevant_files if path in top_k)
    return hits / len(relevant_files)


def precision_at_k(retrieved_files: list[str], relevant_files: list[str], k: int) -> float:
    if not relevant_files:
        return 0.0
    top_k = _ranked_files(retrieved_files)[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for path in top_k if path in set(relevant_files))
    return hits / len(top_k)


def reciprocal_rank(retrieved_files: list[str], relevant_files: list[str]) -> float:
    """1/rank of the first relevant file, or 0 when none was retrieved."""
    relevant = set(relevant_files)
    for index, path in enumerate(_ranked_files(retrieved_files), start=1):
        if path in relevant:
            return 1.0 / index
    return 0.0


def relevant_file_rate(retrieved_files: list[str], relevant_files: list[str]) -> float:
    """1.0 when at least one ground-truth file was retrieved at all."""
    return 1.0 if set(retrieved_files) & set(relevant_files) else 0.0


def entity_coverage(retrieved_entities: list[str], relevant_entities: list[str]) -> float:
    if not relevant_entities:
        return 1.0
    found = {name.lower() for name in retrieved_entities}
    hits = sum(1 for name in relevant_entities if name.lower() in found)
    return hits / len(relevant_entities)


def mention_coverage(answer: str, expected_mentions: list[str]) -> float:
    """Fraction of expected components the answer actually names."""
    if not expected_mentions:
        return 1.0
    lowered = answer.lower()
    hits = sum(1 for mention in expected_mentions if mention.lower() in lowered)
    return hits / len(expected_mentions)


def citation_accuracy(verified: int, unverified: int) -> float:
    """Share of the answer's citations that point at retrieved evidence."""
    total = verified + unverified
    if total == 0:
        return 0.0
    return verified / total


def citation_coverage(citations: list[str], relevant_files: list[str]) -> float:
    """Share of ground-truth files the answer actually cited."""
    if not relevant_files:
        return 1.0
    cited_files = {citation.split(":")[0] for citation in citations}
    hits = sum(1 for path in relevant_files if path in cited_files)
    return hits / len(relevant_files)


@dataclass
class QuestionResult:
    """Per-question scores for one system."""

    question_id: str
    category: str
    question: str
    system: str
    question_type: str = ""
    retrieved_files: list[str] = field(default_factory=list)
    recall_at_1: float = 0.0
    recall_at_3: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    precision_at_5: float = 0.0
    mrr: float = 0.0
    relevant_file_rate: float = 0.0
    entity_coverage: float = 0.0
    mention_coverage: float = 0.0
    citation_accuracy: float = 0.0
    citation_coverage: float = 0.0
    hallucinated: bool = False
    abstained: bool = False
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    retrieval_seconds: float = 0.0
    generation_seconds: float = 0.0


@dataclass
class AggregateMetrics:
    system: str
    question_count: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    mrr: float
    relevant_file_rate: float
    entity_coverage: float
    mention_coverage: float
    citation_accuracy: float
    citation_coverage: float
    hallucination_rate: float
    abstention_rate: float
    mean_retrieval_seconds: float
    mean_generation_seconds: float
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def aggregate(system: str, results: list[QuestionResult]) -> AggregateMetrics:
    by_category: dict[str, dict[str, float]] = {}
    categories = {result.category for result in results}
    for category in sorted(categories):
        subset = [r for r in results if r.category == category]
        by_category[category] = {
            "count": len(subset),
            "recall_at_5": _mean([r.recall_at_5 for r in subset]),
            "mrr": _mean([r.mrr for r in subset]),
            "relevant_file_rate": _mean([r.relevant_file_rate for r in subset]),
            "mention_coverage": _mean([r.mention_coverage for r in subset]),
        }

    return AggregateMetrics(
        system=system,
        question_count=len(results),
        recall_at_1=_mean([r.recall_at_1 for r in results]),
        recall_at_3=_mean([r.recall_at_3 for r in results]),
        recall_at_5=_mean([r.recall_at_5 for r in results]),
        recall_at_10=_mean([r.recall_at_10 for r in results]),
        precision_at_5=_mean([r.precision_at_5 for r in results]),
        mrr=_mean([r.mrr for r in results]),
        relevant_file_rate=_mean([r.relevant_file_rate for r in results]),
        entity_coverage=_mean([r.entity_coverage for r in results]),
        mention_coverage=_mean([r.mention_coverage for r in results]),
        citation_accuracy=_mean([r.citation_accuracy for r in results]),
        citation_coverage=_mean([r.citation_coverage for r in results]),
        hallucination_rate=_mean([1.0 if r.hallucinated else 0.0 for r in results]),
        abstention_rate=_mean([1.0 if r.abstained else 0.0 for r in results]),
        mean_retrieval_seconds=_mean([r.retrieval_seconds for r in results]),
        mean_generation_seconds=_mean([r.generation_seconds for r in results]),
        by_category=by_category,
    )
