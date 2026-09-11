"""Semantic chunking."""

from backend.app.chunking.semantic import (
    MAX_CHUNK_CHARS,
    Chunk,
    build_chunks,
    create_chunks_from_entities,
    create_module_chunks,
)
from backend.app.models.entities import CodeEntity, EntityType


def make_entity(**overrides) -> CodeEntity:
    defaults = dict(
        repository_id="r",
        file_path="app/x.py",
        language="python",
        entity_type=EntityType.FUNCTION,
        entity_name="do_work",
        start_line=1,
        end_line=10,
        source_code="def do_work():\n    return compute_the_answer_for_the_caller()",
    )
    return CodeEntity(**{**defaults, **overrides})


class TestChunkBoundaries:
    def test_chunks_follow_structural_units(self, parsed_sample):
        chunks = create_chunks_from_entities(parsed_sample.entities)
        types = {chunk.entity_type for chunk in chunks}
        assert types <= {"class", "function", "method", "doc_section"}

    def test_every_chunk_carries_required_metadata(self, parsed_sample):
        for chunk in build_chunks(parsed_sample.entities):
            assert chunk.repository_id
            assert chunk.file_path and not chunk.file_path.startswith("/")
            assert chunk.language
            assert chunk.entity_type
            assert chunk.entity_name
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line

    def test_line_numbers_match_the_source_entity(self, parsed_sample):
        by_name = {
            (e.file_path, e.entity_name): e
            for e in parsed_sample.entities
            if e.entity_type == EntityType.FUNCTION
        }
        for chunk in create_chunks_from_entities(parsed_sample.entities):
            entity = by_name.get((chunk.file_path, chunk.entity_name))
            if entity is not None:
                assert (chunk.start_line, chunk.end_line) == (
                    entity.start_line,
                    entity.end_line,
                )

    def test_trivial_entities_are_skipped(self):
        tiny = make_entity(source_code="x = 1")
        assert create_chunks_from_entities([tiny]) == []


class TestClassSummaries:
    def test_class_with_methods_is_summarised(self, parsed_sample):
        chunks = create_chunks_from_entities(parsed_sample.entities)
        (auth_service,) = [
            c for c in chunks if c.entity_name == "AuthService" and c.entity_type == "class"
        ]
        assert "def login" in auth_service.content
        assert "def logout" in auth_service.content
        # The method bodies live in their own chunks, not in the class chunk.
        assert "find_by_email" not in auth_service.content
        assert auth_service.metadata["method_names"] == ["__init__", "login", "logout"]


class TestOversizedChunks:
    def test_long_units_are_split_with_correct_line_numbers(self):
        body = "\n".join(f"    line_{index} = {index}" for index in range(2000))
        entity = make_entity(
            source_code=f"def huge():\n{body}", start_line=1, end_line=2001
        )
        parts = create_chunks_from_entities([entity])

        assert len(parts) > 1
        assert all(len(part.content) <= MAX_CHUNK_CHARS for part in parts)
        assert parts[0].start_line == 1
        assert parts[-1].end_line == 2001
        for earlier, later in zip(parts, parts[1:], strict=False):
            assert later.start_line == earlier.end_line + 1


class TestModuleChunks:
    def test_module_chunk_lists_its_definitions(self, parsed_sample):
        chunks = create_module_chunks(parsed_sample.entities)
        (service,) = [c for c in chunks if c.file_path == "app/auth/service.py"]
        assert "AuthService" in service.content
        assert "hash_password" in service.content

    def test_empty_modules_are_skipped(self, parsed_sample):
        chunks = create_module_chunks(parsed_sample.entities)
        assert not any(chunk.file_path.endswith("__init__.py") for chunk in chunks)


class TestEmbeddingText:
    def test_includes_location_signature_and_docstring(self):
        chunk = Chunk(
            repository_id="r",
            file_path="app/auth/service.py",
            language="python",
            entity_type="method",
            entity_name="login",
            start_line=10,
            end_line=20,
            content="def login(self): ...",
            metadata={
                "parent_name": "AuthService",
                "signature": "def login(self, email, password)",
                "docstring": "Authenticate a user and return a session token.",
            },
        )
        text = chunk.embedding_text()
        assert "app/auth/service.py" in text
        assert "AuthService" in text
        assert "Authenticate a user" in text
        assert chunk.citation == "app/auth/service.py:10-20"
