"""Persistent application metadata.

Repository records survive restarts, which the in-memory dictionary they
replace did not. SQLite is the zero-setup default; set ``DATABASE_URL`` to a
PostgreSQL DSN for a deployment.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from backend.app.config.settings import settings
from backend.app.models.entities import RepositoryMetadata, RepositoryStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Timezone-aware UTC now. ``datetime.utcnow`` is deprecated."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RepositoryRecord(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(String(1024))
    name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    language_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    frameworks: Mapped[list] = mapped_column(JSON, default=list)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    graph_node_count: Mapped[int] = mapped_column(Integer, default=0)
    graph_relationship_count: Mapped[int] = mapped_column(Integer, default=0)
    overview: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexing_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def to_metadata(self) -> RepositoryMetadata:
        return RepositoryMetadata(
            id=self.id,
            url=self.url,
            name=self.name,
            status=RepositoryStatus(self.status),
            languages=self.languages or [],
            language_counts=self.language_counts or {},
            frameworks=self.frameworks or [],
            file_count=self.file_count,
            entity_count=self.entity_count,
            chunk_count=self.chunk_count,
            graph_node_count=self.graph_node_count,
            graph_relationship_count=self.graph_relationship_count,
            overview=self.overview or "",
            error=self.error,
            indexing_seconds=self.indexing_seconds,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class RepositoryStore:
    """CRUD for repository metadata."""

    def __init__(self, database_url: str | None = None):
        url = database_url or settings.database_url
        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args, future=True)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    def _session(self) -> Session:
        return self._session_factory()

    def create(self, metadata: RepositoryMetadata) -> RepositoryMetadata:
        with self._session() as session:
            fields = metadata.model_dump()
            fields["status"] = metadata.status.value
            record = RepositoryRecord(**fields)
            session.add(record)
            session.commit()
            return record.to_metadata()

    def get(self, repository_id: str) -> RepositoryMetadata | None:
        with self._session() as session:
            record = session.get(RepositoryRecord, repository_id)
            return record.to_metadata() if record else None

    def list(self) -> list[RepositoryMetadata]:
        with self._session() as session:
            records = session.scalars(
                select(RepositoryRecord).order_by(RepositoryRecord.created_at.desc())
            ).all()
            return [record.to_metadata() for record in records]

    def update(self, repository_id: str, **fields) -> RepositoryMetadata | None:
        with self._session() as session:
            record = session.get(RepositoryRecord, repository_id)
            if record is None:
                return None
            for key, value in fields.items():
                if key == "status" and isinstance(value, RepositoryStatus):
                    value = value.value
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = _utcnow()
            session.commit()
            return record.to_metadata()

    def set_status(
        self, repository_id: str, status: RepositoryStatus, error: str | None = None
    ) -> None:
        self.update(repository_id, status=status, error=error)

    def delete(self, repository_id: str) -> bool:
        with self._session() as session:
            record = session.get(RepositoryRecord, repository_id)
            if record is None:
                return False
            session.delete(record)
            session.commit()
            return True


_store: RepositoryStore | None = None


def get_repository_store() -> RepositoryStore:
    global _store
    if _store is None:
        _store = RepositoryStore()
    return _store


def set_repository_store(store: RepositoryStore | None) -> None:
    global _store
    _store = store
