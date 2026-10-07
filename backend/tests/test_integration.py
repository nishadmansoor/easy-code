"""Integration tests against real Qdrant and Neo4j instances.

Skipped automatically when the services are not running, so the default
``pytest`` run works on a bare checkout. Start them with:

    docker compose up -d qdrant neo4j
"""

import pytest

from backend.app.chunking.semantic import build_chunks
from backend.app.embeddings.model import HashingEmbeddingModel
from backend.app.graph import build_graph_store
from backend.app.graph.builder import build_code_graph
from backend.app.retrieval.hybrid import retrieve_hybrid, retrieve_vector_only
from backend.app.vector.store import VectorStore

pytestmark = pytest.mark.integration

REPO_ID = "pytest-integration"
COLLECTION = "easycode_test_chunks"


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    from pathlib import Path

    from backend.app.parsing import parse_repository

    return parse_repository(Path(__file__).parent / "fixtures" / "sample_repo", REPO_ID)


@pytest.fixture(scope="module")
def vector_store(parsed):
    """A dedicated collection using deterministic embeddings, torn down after."""
    try:
        store = VectorStore(
            embedding_model=HashingEmbeddingModel(), collection_name=COLLECTION
        )
    except Exception as exc:
        pytest.skip(f"Qdrant is not available: {exc}")

    store.delete_repository(REPO_ID)
    store.store_chunks(build_chunks(parsed.entities))
    yield store
    try:
        store.client.delete_collection(COLLECTION)
    except Exception:
        pass


@pytest.fixture(scope="module")
def graph_store(parsed):
    # The Neo4j driver connects lazily, so constructing a GraphStore succeeds
    # even when the server is down. Run a trivial query to prove the connection
    # before yielding, otherwise every test errors instead of skipping.
    try:
        store = build_graph_store()
        store.ping()
    except Exception as exc:
        pytest.skip(f"Neo4j is not available: {exc}")

    build_code_graph(
        repo_id=REPO_ID,
        repo_url="https://example.invalid/miniapp",
        parsed=parsed,
        graph_store=store,
        repo_name="miniapp",
    )
    yield store
    store.clear_repository(REPO_ID)
    store.close()


class TestVectorStore:
    def test_stores_and_counts_chunks(self, vector_store, parsed):
        assert vector_store.count(REPO_ID) == len(build_chunks(parsed.entities))

    def test_reindexing_does_not_duplicate_points(self, vector_store, parsed):
        before = vector_store.count(REPO_ID)
        vector_store.store_chunks(build_chunks(parsed.entities))
        assert vector_store.count(REPO_ID) == before

    def test_search_returns_metadata_with_relative_paths(self, vector_store):
        hits = vector_store.search("authenticate a user", repository_id=REPO_ID, limit=5)
        assert hits
        for hit in hits:
            assert not hit["file_path"].startswith("/")
            assert hit["start_line"] >= 1

    def test_filters_by_repository(self, vector_store):
        assert vector_store.search("anything", repository_id="other-repo", limit=5) == []

    def test_filters_by_entity_type(self, vector_store):
        hits = vector_store.search(
            "authentication", repository_id=REPO_ID, limit=10, entity_types=["class"]
        )
        assert hits and all(hit["entity_type"] == "class" for hit in hits)


class TestGraphStore:
    def test_writes_all_node_types(self, graph_store):
        nodes = graph_store.get_statistics(REPO_ID)["nodes"]
        assert nodes["Repository"] == 1
        assert nodes["Class"] >= 5
        assert nodes["Function"] >= 5
        assert nodes["Method"] >= 8

    def test_writes_all_relationship_types(self, graph_store):
        relationships = graph_store.get_statistics(REPO_ID)["relationships"]
        for expected in (
            "REPOSITORY_CONTAINS_FILE",
            "FILE_DEFINES_CLASS",
            "FILE_DEFINES_FUNCTION",
            "CLASS_CONTAINS_METHOD",
            "FILE_IMPORTS_FILE",
            "CLASS_INHERITS_CLASS",
        ):
            assert relationships.get(expected, 0) > 0, expected
        assert (
            relationships.get("FUNCTION_CALLS_FUNCTION", 0)
            + relationships.get("METHOD_CALLS_METHOD", 0)
            > 0
        )

    def test_finds_callers(self, graph_store):
        callers = graph_store.get_function_callers(REPO_ID, "find_by_email")
        assert {row["caller_name"] for row in callers} == {"login"}
        assert callers[0]["caller_file"] == "app/auth/service.py"

    def test_finds_callees(self, graph_store):
        callees = {row["callee_name"] for row in graph_store.get_function_callees(REPO_ID, "login")}
        assert {"find_by_email", "create", "hash_password"} <= callees

    def test_finds_importers(self, graph_store):
        importers = {
            row["file_path"] for row in graph_store.get_importers(REPO_ID, "app/core/session.py")
        }
        assert {"app/auth/service.py", "app/main.py"} <= importers

    def test_class_info_includes_methods_and_inheritance(self, graph_store):
        (info,) = graph_store.get_class_info(REPO_ID, "UserRepository")
        assert {m["name"] for m in info["methods"]} >= {"find_by_email", "find_by_id", "add"}
        assert [b["name"] for b in info["bases"]] == ["BaseRepository"]

        (base,) = graph_store.get_class_info(REPO_ID, "BaseRepository")
        assert [s["name"] for s in base["subclasses"]] == ["UserRepository"]

    def test_inheritance_results_carry_line_numbers(self, graph_store):
        """Bases and subclasses must be citable, not just nameable."""
        (info,) = graph_store.get_class_info(REPO_ID, "UserRepository")
        (base,) = info["bases"]
        assert base["start_line"] > 0
        assert base["end_line"] >= base["start_line"]

    def test_file_contents(self, graph_store):
        record = graph_store.get_file_contents(REPO_ID, "app/auth/service.py")
        assert {c["name"] for c in record["classes"]} == {"AuthService"}
        assert {f["name"] for f in record["functions"]} == {"hash_password"}

    def test_dependency_chain(self, graph_store):
        chains = graph_store.get_dependency_chain(
            REPO_ID, "app/main.py", "app/core/base.py"
        )
        assert chains
        assert chains[0]["chain"][0] == "app/main.py"
        assert chains[0]["chain"][-1] == "app/core/base.py"

    def test_central_files(self, graph_store):
        central = graph_store.get_central_files(REPO_ID, limit=5)
        assert central and central[0]["importers"] >= 1

    def test_clear_repository_removes_everything(self, graph_store, parsed):
        # Uses a separate repository id so it cannot disturb the module fixture.
        temporary_id = "pytest-clear"
        build_code_graph(temporary_id, "https://example.invalid/x", parsed, graph_store)
        assert graph_store.get_statistics(temporary_id)["node_count"] > 0
        graph_store.clear_repository(temporary_id)
        assert graph_store.get_statistics(temporary_id)["node_count"] == 0


class TestHybridRetrieval:
    def test_structural_question_uses_the_graph(self, vector_store, graph_store):
        context = retrieve_hybrid(
            "What calls `find_by_email`?", REPO_ID, vector_store, graph_store
        )
        assert context.question_type == "structural"
        assert context.graph_hit_count > 0
        assert any("login" in fact for fact in context.graph_facts)

    def test_inheritance_question_reports_the_subclass(self, vector_store, graph_store):
        context = retrieve_hybrid(
            "Which classes inherit from BaseRepository?", REPO_ID, vector_store, graph_store
        )
        assert any("UserRepository" in fact for fact in context.graph_facts)

    def test_semantic_question_uses_the_vector_index(self, vector_store, graph_store):
        context = retrieve_hybrid(
            "What does this project do?", REPO_ID, vector_store, graph_store
        )
        assert context.question_type == "semantic"
        assert context.vector_hit_count > 0

    def test_hybrid_finds_relationships_the_baseline_cannot(self, vector_store, graph_store):
        """The experiment in miniature: same index, graph adds the callers."""
        question = "What calls `find_by_email`?"
        baseline = retrieve_vector_only(question, REPO_ID, vector_store)
        hybrid = retrieve_hybrid(question, REPO_ID, vector_store, graph_store)

        assert baseline.graph_facts == []
        assert any("calls find_by_email" in fact for fact in hybrid.graph_facts)

    def test_results_are_capped_by_the_context_limit(self, vector_store, graph_store):
        context = retrieve_hybrid(
            "How does login work?", REPO_ID, vector_store, graph_store, context_limit=4
        )
        assert len(context.items) <= 4

    def test_every_item_has_a_usable_citation(self, vector_store, graph_store):
        context = retrieve_hybrid(
            "How does a login request flow through the app?",
            REPO_ID,
            vector_store,
            graph_store,
        )
        for item in context.items:
            assert item.file_path and not item.file_path.startswith("/")
            assert item.end_line >= item.start_line
