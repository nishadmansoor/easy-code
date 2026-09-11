"""API endpoints.

The backing services are replaced with fakes so these tests exercise routing,
validation and serialisation without Qdrant, Neo4j or an LLM.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.api import dependencies, routes
from backend.app.api.main import app
from backend.app.models.entities import RepositoryMetadata, RepositoryStatus
from backend.app.retrieval.ranking import RetrievalContext, RetrievedItem, Source
from backend.app.storage.database import RepositoryStore


class FakeVectorStore:
    def search(self, **kwargs):
        return []

    def delete_repository(self, repository_id):
        pass


class FakeGraphStore:
    def get_files(self, repository_id):
        return [{"file_path": "app/main.py", "language": "python"}]

    def get_repository_overview(self, repository_id):
        return {
            "files": [
                {
                    "file_path": "app/main.py",
                    "language": "python",
                    "classes": ["App"],
                    "functions": ["main"],
                }
            ]
        }

    def get_statistics(self, repository_id):
        return {
            "nodes": {"File": 1},
            "node_count": 1,
            "relationships": {"FILE_DEFINES_FUNCTION": 1},
            "relationship_count": 1,
        }

    def get_central_files(self, repository_id, limit=10):
        return [{"file_path": "app/core.py", "importers": 3}]

    def get_entry_points(self, repository_id, limit=10):
        return [{"file_path": "app/main.py", "dependencies": 2}]

    def get_file_contents(self, repository_id, file_path):
        if file_path != "app/main.py":
            return None
        return {
            "file_path": "app/main.py",
            "language": "python",
            "classes": [],
            "functions": [
                {"name": "main", "start_line": 1, "end_line": 5, "file_path": "app/main.py"}
            ],
            "methods": [],
        }

    def get_class_info(self, repository_id, class_name):
        return [
            {
                "class_name": class_name,
                "file_path": "app/core.py",
                "start_line": 1,
                "end_line": 20,
                "methods": [
                    {
                        "name": "run",
                        "class_name": class_name,
                        "file_path": "app/core.py",
                        "start_line": 5,
                        "end_line": 9,
                        "signature": "def run(self)",
                    }
                ],
                "bases": [],
                "subclasses": [],
            }
        ]

    def get_function_callers(self, repository_id, function_name):
        return [
            {
                "caller_type": "Function",
                "caller_name": "main",
                "caller_file": "app/main.py",
                "caller_start": 1,
                "caller_end": 5,
                "target_file": "app/core.py",
                "target_start": 5,
                "target_end": 9,
            }
        ]

    def get_function_callees(self, repository_id, function_name):
        return [
            {
                "callee_type": "Method",
                "callee_name": "run",
                "callee_file": "app/core.py",
                "callee_start": 5,
                "callee_end": 9,
            }
        ]

    def get_importers(self, repository_id, file_path):
        return [{"file_path": "app/main.py"}]

    def get_imported_files(self, repository_id, file_path):
        return [{"file_path": "app/core.py"}]

    def get_dependency_chain(self, repository_id, source_file, target_file, max_depth=6):
        return [{"chain": ["app/main.py", "app/core.py"]}]

    def clear_repository(self, repository_id):
        pass

    def run_cypher(self, query, **params):
        return [{"ok": 1}]

    def close(self):
        pass


READY_REPO = "ready-repo"


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = RepositoryStore(f"sqlite:///{tmp_path}/api.db")
    store.create(
        RepositoryMetadata(
            id=READY_REPO,
            url="https://github.com/a/b",
            name="b",
            status=RepositoryStatus.READY,
            file_count=1,
            entity_count=2,
            overview="An example project.",
        )
    )
    store.create(
        RepositoryMetadata(
            id="pending-repo",
            url="https://github.com/a/c",
            name="c",
            status=RepositoryStatus.CLONING,
        )
    )

    monkeypatch.setattr(routes, "get_store", lambda: store)
    monkeypatch.setattr(routes, "get_vector_store", lambda: FakeVectorStore())
    monkeypatch.setattr(routes, "get_graph_store", lambda: FakeGraphStore())
    monkeypatch.setattr(dependencies, "service_health", lambda: (True, True))
    monkeypatch.setattr(routes, "service_health", lambda: (True, True))

    workspace = tmp_path / "repos" / READY_REPO
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "main.py").write_text("def main():\n    return 1\n")
    (workspace / "secret.txt").write_text("not a source file")
    monkeypatch.setattr(routes, "repository_path", lambda repo_id: tmp_path / "repos" / repo_id)

    with TestClient(app) as test_client:
        yield test_client


class TestRepositoryLifecycle:
    def test_create_validates_the_url(self, client):
        response = client.post("/api/repositories", json={"url": "git@github.com:a/b.git"})
        assert response.status_code == 400

    def test_create_returns_202_and_queues_indexing(self, client, monkeypatch):
        started: list[str] = []
        monkeypatch.setattr(routes, "_run_indexing", started.append)

        response = client.post(
            "/api/repositories", json={"url": "https://github.com/psf/requests"}
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["name"] == "requests"
        assert started == [body["id"]]

    def test_get_repository(self, client):
        body = client.get(f"/api/repositories/{READY_REPO}").json()
        assert body["name"] == "b"
        assert body["status"] == "ready"

    def test_get_unknown_repository_is_404(self, client):
        assert client.get("/api/repositories/nope").status_code == 404

    def test_list_repositories(self, client):
        ids = {repo["id"] for repo in client.get("/api/repositories").json()}
        assert ids == {READY_REPO, "pending-repo"}

    def test_status_endpoint(self, client):
        body = client.get(f"/api/repositories/{READY_REPO}/status").json()
        assert body["status"] == "ready"
        assert body["entity_count"] == 2

    def test_overview_endpoint(self, client):
        body = client.get(f"/api/repositories/{READY_REPO}/overview").json()
        assert body["overview"] == "An example project."

    def test_delete_repository(self, client, monkeypatch):
        monkeypatch.setattr(routes, "delete_repository", lambda *a, **k: True)
        assert client.delete(f"/api/repositories/{READY_REPO}").status_code == 204


class TestQuery:
    def test_rejects_a_repository_that_is_not_ready(self, client):
        response = client.post(
            "/api/repositories/pending-repo/query", json={"query": "what is this?"}
        )
        assert response.status_code == 409
        assert "cloning" in response.json()["detail"]

    def test_rejects_an_unknown_mode(self, client):
        response = client.post(
            f"/api/repositories/{READY_REPO}/query",
            json={"query": "what is this?", "mode": "telepathy"},
        )
        assert response.status_code == 400

    def test_rejects_an_empty_question(self, client):
        response = client.post(f"/api/repositories/{READY_REPO}/query", json={"query": ""})
        assert response.status_code == 422

    def test_retrieval_only_skips_generation(self, client, monkeypatch):
        monkeypatch.setattr(
            routes,
            "retrieve_hybrid",
            lambda **kwargs: RetrievalContext(
                question=kwargs["question"],
                question_type="mixed",
                items=[
                    RetrievedItem(
                        file_path="app/main.py",
                        entity_type="function",
                        entity_name="main",
                        start_line=1,
                        end_line=5,
                        content="def main(): ...",
                        source=Source.GRAPH,
                        relationship="definition",
                    )
                ],
                graph_facts=["main is defined at app/main.py:1-5"],
                vector_hit_count=0,
                graph_hit_count=1,
            ),
        )
        body = client.post(
            f"/api/repositories/{READY_REPO}/query",
            json={"query": "where is main?", "generate": False},
        ).json()

        assert body["answer"] == ""
        assert body["question_type"] == "mixed"
        assert body["sources"][0]["file_path"] == "app/main.py"
        assert body["sources"][0]["source"] == "graph"
        assert body["graph_facts"] == ["main is defined at app/main.py:1-5"]

    def test_vector_mode_uses_the_baseline_retriever(self, client, monkeypatch):
        called: list[str] = []

        def fake_vector_only(**kwargs):
            called.append("vector")
            return RetrievalContext(question=kwargs["question"], question_type="vector_only")

        monkeypatch.setattr(routes, "retrieve_vector_only", fake_vector_only)
        body = client.post(
            f"/api/repositories/{READY_REPO}/query",
            json={"query": "what is this?", "mode": "vector", "generate": False},
        ).json()

        assert called == ["vector"]
        assert body["question_type"] == "vector_only"


class TestFiles:
    def test_lists_files(self, client):
        body = client.get(f"/api/repositories/{READY_REPO}/files").json()
        assert body["files"] == [{"file_path": "app/main.py", "language": "python"}]

    def test_reads_a_file(self, client):
        body = client.get(
            f"/api/repositories/{READY_REPO}/file", params={"file_path": "app/main.py"}
        ).json()
        assert "def main()" in body["content"]
        assert body["line_count"] == 2
        assert body["language"] == "python"

    def test_missing_file_is_404(self, client):
        response = client.get(
            f"/api/repositories/{READY_REPO}/file", params={"file_path": "app/ghost.py"}
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "path", ["../../../etc/passwd", "app/../../outside.txt", "/etc/passwd"]
    )
    def test_rejects_path_traversal(self, client, path):
        response = client.get(
            f"/api/repositories/{READY_REPO}/file", params={"file_path": path}
        )
        assert response.status_code in (400, 404)
        assert "root:" not in response.text


class TestGraph:
    def test_overview(self, client):
        body = client.get(f"/api/repositories/{READY_REPO}/graph").json()
        assert body["files"][0]["classes"] == ["App"]
        assert body["statistics"]["node_count"] == 1
        assert body["central_files"][0]["importers"] == 3

    def test_file_contents(self, client):
        body = client.get(
            f"/api/repositories/{READY_REPO}/graph/file", params={"file_path": "app/main.py"}
        ).json()
        assert body["functions"][0]["name"] == "main"

    def test_unknown_file_in_graph_is_404(self, client):
        response = client.get(
            f"/api/repositories/{READY_REPO}/graph/file", params={"file_path": "ghost.py"}
        )
        assert response.status_code == 404

    def test_class_info(self, client):
        body = client.get(
            f"/api/repositories/{READY_REPO}/graph/class", params={"class_name": "App"}
        ).json()
        assert body[0]["methods"][0]["name"] == "run"

    def test_callers_and_callees(self, client):
        callers = client.get(
            f"/api/repositories/{READY_REPO}/graph/callers", params={"function_name": "run"}
        ).json()
        callees = client.get(
            f"/api/repositories/{READY_REPO}/graph/callees", params={"function_name": "main"}
        ).json()
        assert callers[0]["caller_name"] == "main"
        assert callees[0]["callee_name"] == "run"

    def test_importers_and_imports(self, client):
        importers = client.get(
            f"/api/repositories/{READY_REPO}/graph/importers",
            params={"file_path": "app/core.py"},
        ).json()
        imports = client.get(
            f"/api/repositories/{READY_REPO}/graph/imports",
            params={"file_path": "app/main.py"},
        ).json()
        assert importers[0]["file_path"] == "app/main.py"
        assert imports[0]["file_path"] == "app/core.py"

    def test_dependency_path(self, client):
        body = client.get(
            f"/api/repositories/{READY_REPO}/graph/path",
            params={"source": "app/main.py", "target": "app/core.py"},
        ).json()
        assert body["chains"] == [["app/main.py", "app/core.py"]]

    def test_graph_endpoints_require_a_ready_repository(self, client):
        assert client.get("/api/repositories/pending-repo/graph").status_code == 409


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["qdrant"] and body["neo4j"]


def test_config_exposes_no_secrets(client):
    body = client.get("/api/config").json()
    assert "embedding_model" in body
    assert not any("key" in key.lower() for key in body)
