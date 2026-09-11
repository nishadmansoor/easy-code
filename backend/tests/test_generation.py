"""Answer generation, citation verification and hallucination prevention."""

from backend.app.generation.answer import (
    INSUFFICIENT_EVIDENCE,
    extract_citations,
    generate_answer,
    strip_unverified,
    verify_citations,
)
from backend.app.generation.provider import (
    ExtractiveProvider,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    build_provider,
)
from backend.app.retrieval.ranking import RetrievalContext, RetrievedItem


class StubProvider(LLMProvider):
    """Returns a scripted answer so generation can be tested without a model."""

    name = "stub"

    def __init__(self, text: str = "", fail: bool = False):
        self.text = text
        self.fail = fail
        self.prompts: list[str] = []

    def is_available(self) -> bool:
        return not self.fail

    @property
    def model_name(self) -> str:
        return "stub-model"

    def generate(self, prompt: str, system: str | None = None) -> LLMResponse:
        self.prompts.append(prompt)
        if self.fail:
            raise LLMUnavailableError("stub is offline")
        return LLMResponse(text=self.text, model="stub-model", provider="stub")


def context_with(*ranges) -> RetrievalContext:
    return RetrievalContext(
        question="Where is authentication handled?",
        question_type="mixed",
        items=[
            RetrievedItem(
                file_path=path,
                entity_type="function",
                entity_name="login",
                start_line=start,
                end_line=end,
                content="def login(): ...",
                language="python",
            )
            for path, start, end in ranges
        ],
    )


class TestCitationExtraction:
    def test_extracts_ranges_and_single_lines(self):
        text = "See `app/auth/service.py:42-78` and app/main.py:10."
        assert extract_citations(text) == [
            ("app/auth/service.py", 42, 78),
            ("app/main.py", 10, 10),
        ]

    def test_ignores_prose_without_line_numbers(self):
        assert extract_citations("The file app/auth/service.py handles login.") == []


class TestCitationVerification:
    def test_accepts_citations_backed_by_retrieved_evidence(self):
        context = context_with(("app/auth/service.py", 42, 78))
        verified, unverified = verify_citations("See app/auth/service.py:42-78.", context)
        assert [str(c) for c in verified] == ["app/auth/service.py:42-78"]
        assert unverified == []

    def test_rejects_a_file_that_was_never_retrieved(self):
        context = context_with(("app/auth/service.py", 42, 78))
        verified, unverified = verify_citations("See app/invented/module.py:1-9.", context)
        assert verified == []
        assert unverified == ["app/invented/module.py:1-9"]

    def test_rejects_a_line_range_far_from_the_evidence(self):
        context = context_with(("app/auth/service.py", 42, 78))
        _, unverified = verify_citations("See app/auth/service.py:900-950.", context)
        assert unverified == ["app/auth/service.py:900-950"]

    def test_allows_small_line_drift(self):
        context = context_with(("app/auth/service.py", 42, 78))
        verified, unverified = verify_citations("See app/auth/service.py:44-76.", context)
        assert verified and not unverified

    def test_deduplicates_repeated_citations(self):
        context = context_with(("a.py", 1, 10))
        verified, _ = verify_citations("a.py:1-10 and again a.py:1-10", context)
        assert len(verified) == 1

    def test_strip_unverified_marks_the_reference(self):
        cleaned = strip_unverified("See `fake.py:1-9` now.", ["fake.py:1-9"])
        assert "fake.py:1-9" not in cleaned
        assert "line reference unverified" in cleaned


class TestGenerateAnswer:
    def test_abstains_when_nothing_was_retrieved(self):
        empty = RetrievalContext(question="q", question_type="semantic")
        result = generate_answer(empty, provider=StubProvider("anything"))
        assert result.answer == INSUFFICIENT_EVIDENCE
        assert result.citations == []

    def test_keeps_verified_citations(self):
        context = context_with(("app/auth/service.py", 42, 78))
        provider = StubProvider(
            "Authentication happens in `app/auth/service.py:42-78`.\n\n"
            "Sources:\n- app/auth/service.py:42-78"
        )
        result = generate_answer(context, provider=provider)
        assert result.grounded
        assert [str(c) for c in result.citations] == ["app/auth/service.py:42-78"]

    def test_removes_hallucinated_citations_and_flags_the_answer(self):
        context = context_with(("app/auth/service.py", 42, 78))
        provider = StubProvider("It lives in `app/ghost/service.py:1-20`.")
        result = generate_answer(context, provider=provider)

        assert not result.grounded
        assert result.unverified_citations == ["app/ghost/service.py:1-20"]
        assert "app/ghost/service.py:1-20" not in result.answer

    def test_appends_retrieved_sources_when_the_model_cites_nothing(self):
        context = context_with(("app/auth/service.py", 42, 78))
        result = generate_answer(context, provider=StubProvider("Authentication is handled."))
        assert "Sources (retrieved context for this answer)" in result.answer
        assert [str(c) for c in result.citations] == ["app/auth/service.py:42-78"]

    def test_falls_back_when_the_provider_is_unreachable(self):
        context = context_with(("app/auth/service.py", 42, 78))
        result = generate_answer(context, provider=StubProvider(fail=True))
        assert result.provider == "extractive"
        assert "app/auth/service.py:42-78" in result.answer

    def test_prompt_contains_the_retrieved_context(self):
        context = context_with(("app/auth/service.py", 42, 78))
        provider = StubProvider("ok")
        generate_answer(context, provider=provider)
        assert "app/auth/service.py:42-78" in provider.prompts[0]
        assert "Where is authentication handled?" in provider.prompts[0]

    def test_records_retrieval_provenance(self):
        context = context_with(("a.py", 1, 10))
        context.vector_hit_count, context.graph_hit_count = 5, 3
        result = generate_answer(context, provider=StubProvider("answer"))
        assert (result.vector_hits, result.graph_hits) == (5, 3)


class TestProviders:
    def test_extractive_provider_lists_retrieved_locations(self):
        response = ExtractiveProvider().generate("Location: app/x.py:1-10\nLocation: app/y.py:5-9")
        assert "app/x.py:1-10" in response.text
        assert "app/y.py:5-9" in response.text

    def test_extractive_provider_abstains_without_locations(self):
        assert INSUFFICIENT_EVIDENCE in ExtractiveProvider().generate("nothing here").text

    def test_extractive_provider_is_always_available(self):
        assert ExtractiveProvider().is_available()

    def test_build_provider_falls_back_when_unreachable(self, monkeypatch):
        from backend.app.generation import provider as provider_module

        monkeypatch.setattr(
            provider_module.OllamaProvider, "is_available", lambda self: False
        )
        assert build_provider("ollama").name == "extractive"

    def test_openai_provider_requires_a_key_from_the_environment(self):
        from backend.app.generation.provider import OpenAICompatibleProvider

        assert not OpenAICompatibleProvider(api_key="").is_available()
        assert OpenAICompatibleProvider(api_key="from-env").is_available()
