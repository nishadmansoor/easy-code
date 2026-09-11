"""Grounded answer generation.

Prompting alone does not guarantee groundedness, so every citation the model
produces is checked against the evidence that was actually retrieved. Citations
that point at files or line ranges which were never retrieved are removed from
the answer and reported, rather than being shown to the user as fact.
"""

import logging
import re
import time
from dataclasses import dataclass, field

from backend.app.generation.prompts import ANSWER_SYSTEM_PROMPT, build_answer_prompt
from backend.app.generation.provider import LLMProvider, LLMUnavailableError, get_provider
from backend.app.retrieval.ranking import RetrievalContext, assemble_context_text

logger = logging.getLogger(__name__)

CITATION = re.compile(r"`?([\w./\-]+\.[A-Za-z0-9]+):(\d+)(?:\s*-\s*(\d+))?`?")

INSUFFICIENT_EVIDENCE = (
    "I couldn't find enough evidence in the indexed repository to determine this."
)

#: A cited range is accepted when it overlaps retrieved evidence for that file,
#: allowing this much slack in lines for off-by-a-few paraphrasing.
LINE_TOLERANCE = 5


@dataclass
class Citation:
    file_path: str
    start_line: int
    end_line: int
    verified: bool = True

    def __str__(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


@dataclass
class GeneratedAnswer:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    question_type: str = ""
    provider: str = ""
    model: str = ""
    context_items: int = 0
    vector_hits: int = 0
    graph_hits: int = 0
    latency_seconds: float = 0.0
    grounded: bool = True


def extract_citations(text: str) -> list[tuple[str, int, int]]:
    """Every `path:start-end` (or `path:line`) reference in the text."""
    found: list[tuple[str, int, int]] = []
    for match in CITATION.finditer(text):
        path, start, end = match.group(1), int(match.group(2)), match.group(3)
        found.append((path, start, int(end) if end else start))
    return found


def verify_citations(
    text: str, context: RetrievalContext
) -> tuple[list[Citation], list[str]]:
    """Split cited locations into verified and unsupported.

    A citation is verified when its file was retrieved and its line range
    overlaps a retrieved range for that file.
    """
    ranges: dict[str, list[tuple[int, int]]] = {}
    for item in context.items:
        ranges.setdefault(item.file_path, []).append((item.start_line, item.end_line))

    verified: list[Citation] = []
    unverified: list[str] = []
    seen: set[str] = set()

    for path, start, end in extract_citations(text):
        label = f"{path}:{start}-{end}"
        if label in seen:
            continue
        seen.add(label)

        known = ranges.get(path)
        if known is None:
            unverified.append(label)
            continue

        overlaps = any(
            start <= known_end + LINE_TOLERANCE and known_start - LINE_TOLERANCE <= end
            for known_start, known_end in known
        )
        if overlaps:
            verified.append(Citation(path, start, end))
        else:
            unverified.append(label)

    return verified, unverified


def strip_unverified(text: str, unverified: list[str]) -> str:
    """Replace unsupported citations with a marker instead of silently keeping them."""
    cleaned = text
    for label in unverified:
        path, _, lines = label.rpartition(":")
        start = lines.split("-")[0]
        # Match both the full range and a bare `path:start` form.
        for pattern in (
            rf"`?{re.escape(path)}:{re.escape(lines)}`?",
            rf"`?{re.escape(path)}:{re.escape(start)}`?",
        ):
            cleaned = re.sub(pattern, f"{path} (line reference unverified)", cleaned)
    return cleaned


MAX_APPENDED_SOURCES = 6


def _append_retrieved_sources(
    text: str, context: RetrievalContext
) -> tuple[str, list[Citation]]:
    """Attach the top retrieved locations when the model cited nothing."""
    citations = [
        Citation(item.file_path, item.start_line, item.end_line)
        for item in context.items
        if item.file_path and item.start_line > 0
    ][:MAX_APPENDED_SOURCES]

    if not citations:
        return text, []

    listed = "\n".join(f"- {citation}" for citation in citations)
    return f"{text.rstrip()}\n\nSources (retrieved context for this answer):\n{listed}", citations


def generate_answer(
    context: RetrievalContext,
    repository_name: str = "",
    provider: LLMProvider | None = None,
) -> GeneratedAnswer:
    """Produce a grounded answer for an assembled retrieval context."""
    started = time.perf_counter()
    provider = provider or get_provider()

    if not context.items and not context.graph_facts:
        return GeneratedAnswer(
            answer=INSUFFICIENT_EVIDENCE,
            question_type=context.question_type,
            provider=provider.name,
            model=provider.model_name,
            latency_seconds=time.perf_counter() - started,
            vector_hits=context.vector_hit_count,
            graph_hits=context.graph_hit_count,
        )

    context_text = assemble_context_text(context)
    prompt = build_answer_prompt(
        question=context.question,
        context=context_text,
        repository=repository_name or "the indexed repository",
        question_type=context.question_type,
    )

    try:
        response = provider.generate(prompt, system=ANSWER_SYSTEM_PROMPT)
        text = response.text
        provider_name, model_name = response.provider, response.model
    except LLMUnavailableError as exc:
        logger.warning("Generation failed (%s); using extractive fallback", exc)
        from backend.app.generation.provider import ExtractiveProvider

        fallback = ExtractiveProvider()
        response = fallback.generate(prompt)
        text, provider_name, model_name = response.text, response.provider, response.model

    if not text.strip():
        text = INSUFFICIENT_EVIDENCE

    verified, unverified = verify_citations(text, context)
    if unverified:
        logger.info("Removed %d unverified citation(s): %s", len(unverified), unverified)
        text = strip_unverified(text, unverified)

    if not verified and INSUFFICIENT_EVIDENCE not in text:
        # Smaller local models sometimes ignore the citation format. Rather than
        # return an uncited answer, append the locations that were actually
        # retrieved, labelled as such so nothing is attributed to the model.
        text, verified = _append_retrieved_sources(text, context)

    return GeneratedAnswer(
        answer=text,
        citations=verified,
        unverified_citations=unverified,
        question_type=context.question_type,
        provider=provider_name,
        model=model_name,
        context_items=len(context.items),
        vector_hits=context.vector_hit_count,
        graph_hits=context.graph_hit_count,
        latency_seconds=time.perf_counter() - started,
        grounded=not unverified,
    )
