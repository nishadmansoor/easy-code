"""Quick test of the ingestion pipeline without Docker services."""

import json
import shutil
import tempfile
from pathlib import Path

from backend.app.ingestion.repository import (
    collect_source_files,
    detect_languages,
    should_ignore,
)
from backend.app.models.entities import EntityType
from backend.app.parsing import parse_repository
from backend.app.chunking.semantic import create_chunks_from_entities


def test_ignore_rules():
    print("=== Testing ignore rules ===")
    root = Path(tempfile.mkdtemp())
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    (root / "__pycache__").mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hello')")
    (root / ".git" / "config").write_text("git stuff")
    (root / "node_modules" / "lib.js").write_text("js stuff")
    (root / "__pycache__" / "main.cpython.pyc").write_bytes(b"\x00")

    assert should_ignore(root / ".git" / "config", root)
    assert should_ignore(root / "node_modules" / "lib.js", root)
    assert should_ignore(root / "__pycache__" / "main.cpython.pyc", root)
    assert not should_ignore(root / "src" / "main.py", root)
    print("PASS: ignore rules work")
    shutil.rmtree(root)


def test_language_detection():
    print("\n=== Testing language detection ===")
    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1")
    (root / "src" / "util.js").write_text("x = 1")
    (root / "src" / "style.css").write_text("body {}")

    langs = detect_languages(root)
    assert langs.get("python") == 1, f"Expected python=1, got {langs}"
    assert langs.get("javascript") == 1, f"Expected javascript=1, got {langs}"
    print(f"PASS: detected languages: {langs}")
    shutil.rmtree(root)


def test_source_collection():
    print("\n=== Testing source file collection ===")
    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("x = 1")
    (root / "src" / "app.js").write_text("x = 1")
    (root / "src" / "data.json").write_text("{}")

    files = collect_source_files(root)
    names = [f.name for f in files]
    assert "main.py" in names
    assert "app.js" not in names  # JS not in SUPPORTED_PARSERS yet
    print(f"PASS: collected files: {names}")
    shutil.rmtree(root)


def test_python_parsing():
    print("\n=== Testing Python AST parsing ===")
    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    (root / "src" / "auth.py").write_text('''
class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        return self.db.find(user_id)

    def create_user(self, name, email):
        return self.db.insert(name, email)

def format_user(user):
    return f"{user.name} <{user.email}>"

import os
from pathlib import Path
''')

    entities = parse_repository(root, "test_repo")
    print(f"  Found {len(entities)} entities:")
    for e in entities:
        print(f"    {e.entity_type.value:10s} {e.entity_name:20s} lines {e.start_line}-{e.end_line}")

    assert any(e.entity_type == EntityType.CLASS and e.entity_name == "UserService" for e in entities)
    assert any(e.entity_type == EntityType.METHOD and e.entity_name == "get_user" for e in entities)
    assert any(e.entity_type == EntityType.FUNCTION and e.entity_name == "format_user" for e in entities)
    assert any(e.entity_type == EntityType.IMPORT and "os" in e.entity_name for e in entities)
    print("PASS: Python parsing works")
    shutil.rmtree(root)


def test_chunking():
    print("\n=== Testing semantic chunking ===")
    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    (root / "src" / "service.py").write_text('''
import os

DEFAULT_TIMEOUT = 30

class DataService:
    def fetch(self, query):
        return query

    def save(self, data):
        pass

def helper():
    pass
''')

    entities = parse_repository(root, "test_repo")
    chunks = create_chunks_from_entities(entities)
    print(f"  Created {len(chunks)} chunks from entities:")
    for c in chunks:
        print(f"    {c.entity_type:10s} {c.entity_name:20s} ({c.start_line}-{c.end_line})")

    assert len(chunks) >= 3  # class + 2 methods + function
    print("PASS: chunking works")
    shutil.rmtree(root)


def test_full_pipeline_small_repo():
    print("\n=== Testing with a real small repo ===")
    from backend.app.ingestion.repository import clone_repository

    # Use a tiny public repo
    url = "https://github.com/pallets/markupsafe"
    try:
        repo_id, repo_path = clone_repository(url)
        print(f"  Cloned to {repo_path}")

        langs = detect_languages(repo_path)
        print(f"  Languages: {langs}")

        source_files = collect_source_files(repo_path)
        print(f"  Source files: {len(source_files)}")

        entities = parse_repository(repo_path, repo_id)
        print(f"  Entities: {len(entities)}")
        for e in entities[:10]:
            print(f"    {e.entity_type.value:10s} {e.entity_name:30s} {e.file_path}")
        if len(entities) > 10:
            print(f"    ... and {len(entities) - 10} more")

        chunks = create_chunks_from_entities(entities)
        print(f"  Chunks: {len(chunks)}")

        print("PASS: full pipeline works")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        if 'repo_path' in locals() and repo_path.exists():
            shutil.rmtree(repo_path)


if __name__ == "__main__":
    test_ignore_rules()
    test_language_detection()
    test_source_collection()
    test_python_parsing()
    test_chunking()
    test_full_pipeline_small_repo()
    print("\n=== All tests complete ===")
