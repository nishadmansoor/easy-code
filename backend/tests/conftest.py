"""Shared test fixtures.

Unit tests never touch Qdrant, Neo4j or an LLM. Tests that do need those
services are marked ``integration`` and skip themselves when the service is
not reachable, so ``pytest`` passes on a bare checkout.
"""

import shutil
from pathlib import Path

import pytest

from backend.app.models.entities import ParsedRepository
from backend.app.parsing import parse_repository

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"
SAMPLE_JS_REPO = FIXTURES / "sample_js_repo"

REPO_ID = "test-repo"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: requires Qdrant and/or Neo4j to be running"
    )


@pytest.fixture(scope="session")
def sample_repo_path() -> Path:
    return SAMPLE_REPO


@pytest.fixture(scope="session")
def parsed_sample(sample_repo_path: Path) -> ParsedRepository:
    return parse_repository(sample_repo_path, REPO_ID)


@pytest.fixture(scope="session")
def js_repo_path() -> Path:
    return SAMPLE_JS_REPO


@pytest.fixture(scope="session")
def parsed_js_sample(js_repo_path: Path) -> ParsedRepository:
    return parse_repository(js_repo_path, REPO_ID)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """A writable copy of the sample repository."""
    destination = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, destination)
    return destination


@pytest.fixture
def qdrant_available() -> bool:
    from backend.app.config.settings import settings

    try:
        import httpx

        httpx.get(f"http://{settings.qdrant_host}:{settings.qdrant_port}/", timeout=2.0)
        return True
    except Exception:
        return False


@pytest.fixture
def neo4j_available() -> bool:
    try:
        from backend.app.graph.store import GraphStore

        store = GraphStore()
        store.run_cypher("RETURN 1 AS ok")
        store.close()
        return True
    except Exception:
        return False
