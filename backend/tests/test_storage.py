"""Repository metadata persistence."""

import pytest

from backend.app.models.entities import RepositoryMetadata, RepositoryStatus
from backend.app.storage.database import RepositoryStore


@pytest.fixture
def store(tmp_path) -> RepositoryStore:
    return RepositoryStore(f"sqlite:///{tmp_path}/test.db")


def make(repo_id="r1", **overrides) -> RepositoryMetadata:
    defaults = dict(
        id=repo_id,
        url="https://github.com/a/b",
        name="b",
        status=RepositoryStatus.QUEUED,
    )
    return RepositoryMetadata(**{**defaults, **overrides})


def test_create_and_get(store):
    store.create(make())
    stored = store.get("r1")
    assert stored.url == "https://github.com/a/b"
    assert stored.status is RepositoryStatus.QUEUED


def test_get_missing_returns_none(store):
    assert store.get("nope") is None


def test_status_transitions_are_persisted(store):
    store.create(make())
    for status in (
        RepositoryStatus.CLONING,
        RepositoryStatus.PARSING,
        RepositoryStatus.BUILDING_GRAPH,
        RepositoryStatus.READY,
    ):
        store.set_status("r1", status)
        assert store.get("r1").status is status


def test_failure_records_the_reason(store):
    store.create(make())
    store.set_status("r1", RepositoryStatus.FAILED, error="clone refused")
    stored = store.get("r1")
    assert stored.status is RepositoryStatus.FAILED
    assert stored.error == "clone refused"


def test_update_counts_and_json_fields(store):
    store.create(make())
    store.update(
        "r1",
        file_count=12,
        entity_count=41,
        languages=["python", "markdown"],
        language_counts={"python": 12},
        frameworks=["FastAPI"],
        overview="It does things.",
    )
    stored = store.get("r1")
    assert stored.file_count == 12
    assert stored.languages == ["python", "markdown"]
    assert stored.language_counts == {"python": 12}
    assert stored.frameworks == ["FastAPI"]
    assert stored.overview == "It does things."


def test_update_missing_returns_none(store):
    assert store.update("nope", file_count=1) is None


def test_list_is_newest_first(store):
    store.create(make("r1"))
    store.create(make("r2"))
    assert [m.id for m in store.list()] == ["r2", "r1"]


def test_delete(store):
    store.create(make())
    assert store.delete("r1") is True
    assert store.get("r1") is None
    assert store.delete("r1") is False


def test_records_survive_a_new_connection(tmp_path):
    url = f"sqlite:///{tmp_path}/persist.db"
    RepositoryStore(url).create(make())
    assert RepositoryStore(url).get("r1") is not None
