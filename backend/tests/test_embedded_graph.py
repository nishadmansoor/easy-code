"""The embedded graph backend.

These run in the default suite because the embedded store needs no services —
that is the whole point of it. The assertions deliberately mirror the Neo4j
integration tests, since the two backends must be interchangeable.
"""

import json

import pytest

from backend.app.graph.builder import build_code_graph
from backend.app.graph.embedded_store import EmbeddedGraphStore

REPO_ID = "embedded-test"


@pytest.fixture
def store(tmp_path, parsed_sample) -> EmbeddedGraphStore:
    graph_store = EmbeddedGraphStore(directory=tmp_path / "graph")
    build_code_graph(
        repo_id=REPO_ID,
        repo_url="https://example.invalid/miniapp",
        parsed=parsed_sample,
        graph_store=graph_store,
        repo_name="miniapp",
    )
    return graph_store


class TestWrites:
    def test_writes_all_node_types(self, store):
        nodes = store.get_statistics(REPO_ID)["nodes"]
        assert nodes["Repository"] == 1
        assert nodes["Class"] >= 5
        assert nodes["Function"] >= 5
        assert nodes["Method"] >= 8

    def test_writes_all_relationship_types(self, store):
        relationships = store.get_statistics(REPO_ID)["relationships"]
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

    def test_persists_across_instances(self, tmp_path, parsed_sample):
        directory = tmp_path / "graph"
        first = EmbeddedGraphStore(directory=directory)
        build_code_graph(REPO_ID, "https://example.invalid/x", parsed_sample, first)
        expected = first.get_statistics(REPO_ID)

        # A fresh instance reads the same graph back from disk.
        second = EmbeddedGraphStore(directory=directory)
        assert second.get_statistics(REPO_ID) == expected

    def test_writes_valid_json(self, store, tmp_path):
        path = tmp_path / "graph" / f"{REPO_ID}.json"
        data = json.loads(path.read_text())
        assert data["repository_id"] == REPO_ID
        assert data["files"]

    def test_rejects_path_traversal_in_repository_id(self, tmp_path):
        graph_store = EmbeddedGraphStore(directory=tmp_path / "graph")
        graph_store.create_repository_node("../../escape", "https://example.invalid/x")
        assert not (tmp_path.parent / "escape.json").exists()
        assert list((tmp_path / "graph").glob("*.json"))

    def test_clear_removes_everything(self, store):
        assert store.get_statistics(REPO_ID)["node_count"] > 0
        store.clear_repository(REPO_ID)
        assert store.get_statistics(REPO_ID)["node_count"] == 0

    def test_unknown_repository_is_empty_not_an_error(self, store):
        assert store.get_statistics("never-indexed")["node_count"] == 0
        assert store.get_files("never-indexed") == []


class TestQueries:
    def test_finds_callers(self, store):
        callers = store.get_function_callers(REPO_ID, "find_by_email")
        assert {row["caller_name"] for row in callers} == {"login"}
        assert callers[0]["caller_file"] == "app/auth/service.py"
        assert callers[0]["caller_type"] in ("Function", "Method")

    def test_finds_callees(self, store):
        callees = {row["callee_name"] for row in store.get_function_callees(REPO_ID, "login")}
        assert {"find_by_email", "create", "hash_password"} <= callees

    def test_finds_importers(self, store):
        importers = {
            row["file_path"] for row in store.get_importers(REPO_ID, "app/core/session.py")
        }
        assert {"app/auth/service.py", "app/main.py"} <= importers

    def test_finds_imported_files(self, store):
        imported = {row["file_path"] for row in store.get_imported_files(REPO_ID, "app/main.py")}
        assert "app/auth/routes.py" in imported

    def test_class_info_includes_methods_and_inheritance(self, store):
        (info,) = store.get_class_info(REPO_ID, "UserRepository")
        assert {m["name"] for m in info["methods"]} >= {"find_by_email", "find_by_id", "add"}
        assert [b["name"] for b in info["bases"]] == ["BaseRepository"]

        (base,) = store.get_class_info(REPO_ID, "BaseRepository")
        assert [s["name"] for s in base["subclasses"]] == ["UserRepository"]

    def test_inheritance_results_carry_line_numbers(self, store):
        (info,) = store.get_class_info(REPO_ID, "UserRepository")
        (base,) = info["bases"]
        assert base["start_line"] > 0
        assert base["end_line"] >= base["start_line"]

    def test_file_contents(self, store):
        record = store.get_file_contents(REPO_ID, "app/auth/service.py")
        assert {c["name"] for c in record["classes"]} == {"AuthService"}
        assert {f["name"] for f in record["functions"]} == {"hash_password"}

    def test_file_contents_unknown_file_is_none(self, store):
        assert store.get_file_contents(REPO_ID, "app/ghost.py") is None

    def test_find_entities(self, store):
        (entity,) = store.find_entities(REPO_ID, "AuthService")
        assert entity["entity_type"] == "Class"
        assert entity["file_path"] == "app/auth/service.py"

    def test_dependency_chain(self, store):
        chains = store.get_dependency_chain(REPO_ID, "app/main.py", "app/core/base.py")
        assert chains
        assert chains[0]["chain"][0] == "app/main.py"
        assert chains[0]["chain"][-1] == "app/core/base.py"

    def test_dependency_chain_without_a_path_is_empty(self, store):
        assert store.get_dependency_chain(REPO_ID, "app/core/base.py", "app/main.py") == []

    def test_central_files(self, store):
        central = store.get_central_files(REPO_ID, limit=5)
        assert central and central[0]["importers"] >= 1

    def test_entry_points(self, store):
        entries = {row["file_path"] for row in store.get_entry_points(REPO_ID)}
        assert "app/main.py" in entries

    def test_repository_overview(self, store):
        files = store.get_repository_overview(REPO_ID)["files"]
        service = next(f for f in files if f["file_path"] == "app/auth/service.py")
        assert service["classes"] == ["AuthService"]
        assert service["functions"] == ["hash_password"]

    def test_entity_neighborhood(self, store):
        rows = store.get_entity_neighborhood(REPO_ID, "login")
        assert rows
        assert {"find_by_email", "login_route"} & {row["other_name"] for row in rows}


class TestNetworks:
    def test_file_network(self, store):
        network = store.get_file_network(REPO_ID, limit=150)
        assert network["scope"] == "files"
        assert network["nodes"] and network["edges"]
        assert not network["truncated"]
        ids = {n["id"] for n in network["nodes"]}
        for edge in network["edges"]:
            assert edge["source"] in ids and edge["target"] in ids

    def test_file_network_truncates_to_most_connected(self, store):
        network = store.get_file_network(REPO_ID, limit=2)
        assert len(network["nodes"]) == 2
        assert network["truncated"]
        assert network["total_nodes"] > 2

    def test_call_network(self, store):
        network = store.get_call_network(REPO_ID, "login", depth=2)
        assert network["scope"] == "calls"
        assert network["focus"] == "login"
        focused = [n for n in network["nodes"] if n["focus"]]
        assert focused and focused[0]["label"] == "login"
        assert {n["label"] for n in network["nodes"]} >= {"login", "find_by_email"}

    def test_call_network_for_unknown_name_is_empty(self, store):
        network = store.get_call_network(REPO_ID, "no_such_function")
        assert network["nodes"] == []


def test_cypher_is_refused_with_a_useful_message(store):
    with pytest.raises(NotImplementedError, match="GRAPH_BACKEND|Cypher"):
        store.run_cypher("MATCH (n) RETURN n")


def test_ping(store):
    assert store.ping() is True


class TestGraphRetrievalLabels:
    """Relationship labels must name their target.

    A label like "imports this file" is a dangling reference: the model sees
    each item on its own and cannot tell which file "this" is, nor which way
    the edge runs. That ambiguity made it abstain on questions the graph had
    already answered.
    """

    def test_dependency_labels_name_the_file(self, store):
        from backend.app.retrieval.graph_retrieval import find_file_dependencies

        items, facts = find_file_dependencies(store, REPO_ID, ["app/core/session.py"])
        assert items

        labels = {item.relationship for item in items}
        assert not any("this file" in label for label in labels), labels
        assert "imports app/core/session.py" in labels
        assert any(fact.startswith("app/core/session.py is imported by:") for fact in facts)

    def test_both_directions_are_distinguishable(self, store):
        from backend.app.retrieval.graph_retrieval import find_file_dependencies

        items, _ = find_file_dependencies(store, REPO_ID, ["app/auth/service.py"])
        by_path = {item.file_path: item.relationship for item in items}

        # main.py imports service.py; service.py imports repository.py.
        assert by_path.get("app/main.py") == "imports app/auth/service.py"
        assert by_path.get("app/users/repository.py") == "imported by app/auth/service.py"
