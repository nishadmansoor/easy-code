"""Question analysis, ranking, deduplication and context assembly."""

import pytest

from backend.app.retrieval.question_analysis import (
    QuestionType,
    StructuralIntent,
    analyze_question,
    extract_identifiers,
)
from backend.app.retrieval.ranking import (
    TIER_CONTEXT,
    TIER_DIRECT,
    TIER_RELATED,
    RetrievalContext,
    RetrievedItem,
    Source,
    assemble_context_text,
    deduplicate,
    rank,
    remove_contained,
)


def item(path="a.py", name="f", start=1, end=10, **overrides) -> RetrievedItem:
    defaults = dict(
        file_path=path,
        entity_type="function",
        entity_name=name,
        start_line=start,
        end_line=end,
        content="def f(): pass",
        score=0.5,
    )
    return RetrievedItem(**{**defaults, **overrides})


class TestQuestionClassification:
    @pytest.mark.parametrize(
        "question",
        [
            "What does this repository do?",
            "Give me a summary of this project",
            "What is this codebase for?",
        ],
    )
    def test_semantic_questions(self, question):
        assert analyze_question(question).question_type is QuestionType.SEMANTIC

    @pytest.mark.parametrize(
        ("question", "intent"),
        [
            ("What calls AuthService.login()?", StructuralIntent.CALLERS),
            ("Which functions call `find_by_email`?", StructuralIntent.CALLERS),
            ("What imports app/core/session.py?", StructuralIntent.IMPORTERS),
            ("Which classes inherit from BaseRepository?", StructuralIntent.INHERITANCE),
            ("Where is UserRepository defined?", StructuralIntent.DEFINITION),
        ],
    )
    def test_structural_questions(self, question, intent):
        analysis = analyze_question(question)
        assert analysis.question_type is QuestionType.STRUCTURAL
        assert intent in analysis.intents

    @pytest.mark.parametrize(
        "question",
        [
            "How does a login request move through the application?",
            "What happens when a user submits a request?",
            "Explain how AuthService interacts with UserRepository",
            "Explain the architecture of this project",
        ],
    )
    def test_mixed_questions(self, question):
        assert analyze_question(question).question_type is QuestionType.MIXED

    def test_structural_question_without_a_target_is_not_structural(self):
        # Nothing concrete to look up, so it must not skip semantic search.
        analysis = analyze_question("What calls what around here?")
        assert analysis.question_type is not QuestionType.STRUCTURAL
        assert analysis.wants_vector or not analysis.identifiers

    def test_routing_flags(self):
        assert analyze_question("What calls `login`?").wants_graph
        assert analyze_question("What does this project do?").wants_vector
        mixed = analyze_question("What happens when a request arrives?")
        assert mixed.wants_graph and mixed.wants_vector


class TestIdentifierExtraction:
    def test_extracts_camel_case_snake_case_and_calls(self):
        identifiers, _ = extract_identifiers(
            "How does AuthService use find_by_email and create_session()?"
        )
        assert {"AuthService", "find_by_email", "create_session"} <= set(identifiers)

    def test_extracts_backticked_and_dotted_names(self):
        identifiers, _ = extract_identifiers("What calls `AuthService.login`?")
        assert "AuthService" in identifiers
        assert "login" in identifiers

    def test_extracts_file_paths_and_keeps_them_separate(self):
        identifiers, files = extract_identifiers("What imports app/core/session.py?")
        assert files == ["app/core/session.py"]
        assert "app/core/session.py" not in identifiers

    def test_ignores_ordinary_prose(self):
        identifiers, files = extract_identifiers("what does this repository do")
        assert identifiers == []
        assert files == []


class TestDeduplication:
    def test_merges_identical_regions_from_both_retrievers(self):
        merged = deduplicate(
            [
                item(source=Source.VECTOR, tier=TIER_CONTEXT, score=0.4),
                item(source=Source.GRAPH, tier=TIER_DIRECT, score=0.9, relationship="callers"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].source is Source.BOTH
        assert merged[0].tier == TIER_DIRECT
        assert merged[0].score == 0.9
        assert merged[0].relationship == "callers"

    def test_keeps_distinct_regions(self):
        assert len(deduplicate([item(start=1, end=10), item(start=20, end=30)])) == 2

    def test_removes_contained_ranges(self):
        outer = item(name="Class", start=1, end=100)
        inner = item(name="method", start=10, end=20)
        assert remove_contained([outer, inner]) == [outer]

    def test_keeps_overlapping_but_not_contained_ranges(self):
        first = item(start=1, end=20)
        second = item(name="g", start=15, end=40)
        assert len(remove_contained([first, second])) == 2


class TestRanking:
    def test_higher_tier_wins_over_raw_score(self):
        weak_direct = item(name="direct", tier=TIER_DIRECT, score=0.5)
        strong_context = item(path="b.py", name="ctx", tier=TIER_CONTEXT, score=0.9)
        assert rank([strong_context, weak_direct], limit=2)[0].entity_name == "direct"

    def test_items_found_by_both_retrievers_are_boosted(self):
        both = item(path="a.py", name="both", tier=TIER_RELATED, score=0.6, source=Source.BOTH)
        single = item(path="b.py", name="single", tier=TIER_RELATED, score=0.65)
        assert rank([single, both], limit=2)[0].entity_name == "both"

    def test_respects_the_limit(self):
        items = [item(path=f"f{i}.py", name=f"f{i}") for i in range(30)]
        assert len(rank(items, limit=7)) == 7

    def test_per_file_cap_preserves_diversity(self):
        """One high-scoring file must not crowd out the rest of the repository."""
        crowded = [
            item(path="big.py", name=f"f{i}", start=i * 100, end=i * 100 + 50, score=0.99)
            for i in range(10)
        ]
        others = [item(path=f"other{i}.py", name=f"g{i}", score=0.4) for i in range(4)]
        selected = rank(crowded + others, limit=7, per_file_cap=3)

        assert sum(1 for s in selected if s.file_path == "big.py") == 3
        # Every lower-scoring file still made it in.
        assert {s.file_path for s in selected} == {"big.py", *[f"other{i}.py" for i in range(4)]}

    def test_file_level_items_cite_the_path_alone(self):
        """A ``path:0-0`` citation is useless for navigation."""
        assert item(path="app/core.py", start=0, end=0).citation == "app/core.py"
        assert item(path="app/core.py", start=5, end=9).citation == "app/core.py:5-9"

    def test_cap_is_soft_so_the_budget_is_never_wasted(self):
        """With nothing else to show, the cap yields rather than return less."""
        crowded = [
            item(path="big.py", name=f"f{i}", start=i * 100, end=i * 100 + 50, score=0.9)
            for i in range(10)
        ]
        selected = rank(crowded, limit=6, per_file_cap=3)
        assert len(selected) == 6


class TestContextAssembly:
    def test_includes_graph_facts_and_citations(self):
        context = RetrievalContext(
            question="q",
            question_type="mixed",
            items=[item(path="app/auth/service.py", name="login", start=42, end=78)],
            graph_facts=["login calls find_by_email (app/users/repository.py)"],
        )
        text = assemble_context_text(context)
        assert "Structural facts" in text
        assert "app/auth/service.py:42-78" in text
        assert "find_by_email" in text

    def test_respects_the_character_budget(self):
        items = [
            item(path=f"f{i}.py", name=f"f{i}", content="x" * 2000, start=1, end=50)
            for i in range(30)
        ]
        text = assemble_context_text(
            RetrievalContext(question="q", question_type="semantic", items=items),
            max_chars=5000,
        )
        assert len(text) < 6000

    def test_reports_when_nothing_was_retrieved(self):
        text = assemble_context_text(RetrievalContext(question="q", question_type="semantic"))
        assert "No relevant context" in text

    def test_cited_files_are_deduplicated_in_order(self):
        context = RetrievalContext(
            question="q",
            question_type="mixed",
            items=[
                item(path="a.py"),
                item(path="b.py", start=30, end=40),
                item(path="a.py", start=50, end=60),
            ],
        )
        assert context.cited_files == ["a.py", "b.py"]
