"""Full integration test with Docker services (Qdrant + Neo4j)."""

import shutil
import tempfile
from pathlib import Path

from backend.app.ingestion.repository import clone_repository, create_repository_metadata
from backend.app.parsing import parse_repository
from backend.app.chunking.semantic import create_chunks_from_entities, create_file_chunks
from backend.app.embeddings.model import get_embedding_model
from backend.app.vector.store import VectorStore
from backend.app.graph.store import GraphStore
from backend.app.graph.builder import build_code_graph
from backend.app.retrieval.vector_retrieval import retrieve_relevant_chunks
from backend.app.retrieval.graph_retrieval import (
    get_file_graph_context,
    get_class_graph_context,
    get_caller_graph_context,
)


def test_vector_store():
    print("=== Testing Qdrant vector store ===")
    model = get_embedding_model()
    store = VectorStore(embedding_model=model)
    print(f"  Connected to Qdrant. Dimension: {model.dimension()}")

    chunks_data = [
        ("test_repo", "src/auth.py", "python", "function", "login", 10, 25,
         "def login(username, password):\n    user = db.find(username)\n    if user and check_password(user, password):\n        return create_session(user)\n    return None"),
        ("test_repo", "src/auth.py", "python", "function", "logout", 27, 30,
         "def logout(session_id):\n    sessions.delete(session_id)"),
        ("test_repo", "src/db.py", "python", "class", "Database", 1, 40,
         "class Database:\n    def __init__(self, connection):\n        self.connection = connection\n    def find(self, username):\n        return self.connection.query(username)"),
    ]

    from backend.app.chunking.semantic import Chunk

    chunks = [Chunk(repository_id=r[0], file_path=r[1], language=r[2],
                     entity_type=r[3], entity_name=r[4], start_line=r[5],
                     end_line=r[6], content=r[7]) for r in chunks_data]

    stored = store.store_chunks(chunks)
    print(f"  Stored {stored} chunks")

    results = store.search(query="user authentication", repository_id="test_repo", limit=5)
    print(f"  Search 'user authentication': {len(results)} results")
    for r in results:
        print(f"    [{r['score']:.3f}] {r['entity_type']} {r['entity_name']} ({r['file_path']}:{r['start_line']}-{r['end_line']})")

    assert len(results) > 0
    assert results[0]["score"] > 0
    print("PASS: vector store works")


def test_graph_store():
    print("\n=== Testing Neo4j graph store ===")
    store = GraphStore()

    store.clear_repository("test_graph")
    store.create_repository_node("test_graph", "https://github.com/test/repo")
    store.create_file_node("test_graph", "src/auth.py", "python")
    store.create_file_node("test_graph", "src/db.py", "python")
    store.create_repository_contains_file("test_graph", "src/auth.py")
    store.create_repository_contains_file("test_graph", "src/db.py")

    store.create_function_node("test_graph", "src/auth.py", "login", 10, 25)
    store.create_function_node("test_graph", "src/auth.py", "logout", 27, 30)
    store.create_file_defines_function("test_graph", "src/auth.py", "login")
    store.create_file_defines_function("test_graph", "src/auth.py", "logout")

    store.create_class_node("test_graph", "src/db.py", "Database", 1, 40)
    store.create_file_defines_class("test_graph", "src/db.py", "Database")

    store.create_method_node("test_graph", "src/db.py", "Database", "find", 10, 15)
    store.create_class_contains_method("test_graph", "src/db.py", "Database", "find")

    overview = store.get_repository_overview("test_graph")
    print(f"  Files: {len(overview['files'])}")
    print(f"  Classes: {len(overview['classes'])}")
    print(f"  Functions: {len(overview['functions'])}")

    file_ctx = store.get_file_contents("test_graph", "src/auth.py")
    print(f"  File src/auth.py contents: {len(file_ctx)} record(s)")

    class_ctx = store.get_class_methods("test_graph", "Database")
    print(f"  Class Database methods: {len(class_ctx)} record(s)")

    store.clear_repository("test_graph")
    print("PASS: graph store works")


def test_full_ingestion_with_docker():
    print("\n=== Testing full ingestion with Qdrant + Neo4j ===")
    url = "https://github.com/pallets/markupsafe"
    repo_id, repo_path = clone_repository(url)
    print(f"  Cloned: {repo_id}")

    metadata = create_repository_metadata(url, repo_id, repo_path)
    print(f"  Languages: {metadata.languages}")

    entities = parse_repository(repo_path, repo_id)
    print(f"  Entities: {len(entities)}")

    chunks = create_chunks_from_entities(entities)
    file_chunks = create_file_chunks(repo_path, entities)
    all_chunks = chunks + file_chunks
    print(f"  Chunks: {len(all_chunks)}")

    model = get_embedding_model()
    vector_store = VectorStore(embedding_model=model)
    vector_store.store_chunks(all_chunks)
    print(f"  Stored {len(all_chunks)} chunks in Qdrant")

    graph_store = GraphStore()
    build_code_graph(
        repo_id=repo_id,
        repo_url=url,
        repo_path=repo_path,
        entities=entities,
        graph_store=graph_store,
    )
    print("  Built graph in Neo4j")

    results = vector_store.search(query="escape function for HTML", repository_id=repo_id, limit=3)
    print(f"\n  Vector search 'escape function for HTML':")
    for r in results:
        print(f"    [{r['score']:.3f}] {r['entity_type']} {r['entity_name']} ({r['file_path']}:{r['start_line']}-{r['end_line']})")

    overview = graph_store.get_repository_overview(repo_id)
    print(f"\n  Graph overview:")
    print(f"    Files: {len(overview['files'])}")
    print(f"    Classes: {len(overview['classes'])}")
    print(f"    Functions: {len(overview['functions'])}")

    graph_store.clear_repository(repo_id)
    shutil.rmtree(repo_path, ignore_errors=True)
    print("PASS: full ingestion pipeline works")


if __name__ == "__main__":
    test_vector_store()
    test_graph_store()
    test_full_ingestion_with_docker()
    print("\n=== All Docker integration tests complete ===")
