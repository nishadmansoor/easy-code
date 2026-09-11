"""Parsing: entity extraction and relationship resolution."""

from pathlib import Path

from backend.app.models.entities import EntityType
from backend.app.parsing import module_to_candidate_paths, resolve_imports
from backend.app.parsing.markdown_parser import parse_markdown_file
from backend.app.parsing.python_parser import parse_python_file


def entities_of(parsed, entity_type):
    return [e for e in parsed.entities if e.entity_type == entity_type]


def named(parsed, name, entity_type=None):
    return [
        e
        for e in parsed.entities
        if e.entity_name == name and (entity_type is None or e.entity_type == entity_type)
    ]


class TestEntityExtraction:
    def test_extracts_every_entity_type(self, parsed_sample):
        found = {e.entity_type for e in parsed_sample.entities}
        assert {
            EntityType.MODULE,
            EntityType.CLASS,
            EntityType.FUNCTION,
            EntityType.METHOD,
            EntityType.DOC_SECTION,
        } <= found

    def test_file_paths_are_repository_relative(self, parsed_sample):
        for entity in parsed_sample.entities:
            assert not entity.file_path.startswith("/")
            assert "\\" not in entity.file_path

    def test_class_carries_bases_and_docstring(self, parsed_sample):
        (repository,) = named(parsed_sample, "UserRepository", EntityType.CLASS)
        assert repository.base_classes == ["BaseRepository"]
        assert "email" in (repository.docstring or "")
        assert repository.file_path == "app/users/repository.py"

    def test_method_records_its_class(self, parsed_sample):
        (login,) = named(parsed_sample, "login", EntityType.METHOD)
        assert login.parent_name == "AuthService"
        assert login.qualified_name == "AuthService.login"
        assert login.signature.startswith("def login(self, email, password)")

    def test_function_has_signature_and_line_range(self, parsed_sample):
        (hash_password,) = named(parsed_sample, "hash_password", EntityType.FUNCTION)
        assert hash_password.signature == "def hash_password(password)"
        assert hash_password.start_line < hash_password.end_line

    def test_citation_format(self, parsed_sample):
        (login,) = named(parsed_sample, "login", EntityType.METHOD)
        assert login.citation == f"app/auth/service.py:{login.start_line}-{login.end_line}"

    def test_syntax_errors_do_not_raise(self, tmp_path):
        broken = tmp_path / "broken.py"
        broken.write_text("def oops(:\n  pass")
        assert parse_python_file(broken, "r", "broken.py").entities == []

    def test_nested_functions_are_extracted(self, tmp_path):
        source = tmp_path / "nested.py"
        source.write_text("def outer():\n    def inner():\n        return 1\n    return inner\n")
        parsed = parse_python_file(source, "r", "nested.py")
        names = {e.entity_name for e in entities_of(parsed, EntityType.FUNCTION)}
        assert names == {"outer", "inner"}

    def test_conditional_definitions_are_extracted(self, tmp_path):
        source = tmp_path / "conditional.py"
        source.write_text(
            "import sys\n"
            "if sys.version_info >= (3, 11):\n"
            "    def modern():\n        return 1\n"
            "else:\n"
            "    def legacy():\n        return 0\n"
        )
        parsed = parse_python_file(source, "r", "conditional.py")
        names = {e.entity_name for e in entities_of(parsed, EntityType.FUNCTION)}
        assert names == {"modern", "legacy"}

    def test_decorators_are_captured(self, tmp_path):
        source = tmp_path / "decorated.py"
        source.write_text("@app.route('/x')\ndef handler():\n    return 1\n")
        parsed = parse_python_file(source, "r", "decorated.py")
        assert parsed.entities[1].decorators == ["app.route('/x')"]


class TestImportResolution:
    def test_module_to_candidate_paths_absolute(self):
        assert module_to_candidate_paths("app.users.repository", "app/main.py") == [
            "app/users/repository.py",
            "app/users/repository/__init__.py",
        ]

    def test_module_to_candidate_paths_relative(self):
        assert module_to_candidate_paths(".service", "app/auth/routes.py") == [
            "app/auth/service.py",
            "app/auth/service/__init__.py",
        ]

    def test_module_to_candidate_paths_parent_relative(self):
        assert module_to_candidate_paths("..core.base", "app/users/repository.py") == [
            "app/core/base.py",
            "app/core/base/__init__.py",
        ]

    def test_resolves_internal_imports(self, parsed_sample):
        resolved = {
            (edge.source_file, edge.resolved_file)
            for edge in parsed_sample.imports
            if edge.resolved_file
        }
        assert ("app/users/repository.py", "app/core/base.py") in resolved
        assert ("app/auth/service.py", "app/core/session.py") in resolved
        assert ("app/main.py", "app/auth/routes.py") in resolved

    def test_leaves_external_imports_unresolved(self, parsed_sample):
        uuid_imports = [e for e in parsed_sample.imports if e.module == "uuid"]
        assert uuid_imports and all(e.resolved_file is None for e in uuid_imports)

    def test_never_resolves_a_file_to_itself(self):
        from backend.app.models.entities import ImportEdge

        edges = [ImportEdge(repository_id="r", source_file="a.py", module="a", line=1)]
        resolve_imports(edges, {"a.py"})
        assert edges[0].resolved_file is None


class TestCallResolution:
    def test_resolves_internal_calls(self, parsed_sample):
        pairs = {(c.caller_name, c.callee_name, c.callee_file) for c in parsed_sample.calls}
        assert ("login", "find_by_email", "app/users/repository.py") in pairs
        assert ("login", "create", "app/core/session.py") in pairs
        assert ("login_route", "login", "app/auth/service.py") in pairs

    def test_every_recorded_call_is_resolved(self, parsed_sample):
        assert all(call.callee_file for call in parsed_sample.calls)

    def test_does_not_invent_calls_to_unknown_names(self, tmp_path):
        source = tmp_path / "external.py"
        source.write_text("def run():\n    return some_library_function()\n")
        parsed = parse_python_file(source, "r", "external.py")
        # The parser records the call site, but resolution drops it.
        from backend.app.parsing import resolve_calls

        resolve_calls(parsed)
        assert parsed.calls == []

    def test_skips_builtin_names(self, tmp_path):
        source = tmp_path / "builtins_only.py"
        source.write_text("def run():\n    return len([1, 2, 3])\n")
        parsed = parse_python_file(source, "r", "builtins_only.py")
        from backend.app.parsing import resolve_calls

        resolve_calls(parsed)
        assert parsed.calls == []


class TestInheritanceResolution:
    def test_resolves_internal_base_class(self, parsed_sample):
        edges = {
            (e.child_class, e.parent_class, e.parent_file) for e in parsed_sample.inheritance
        }
        assert ("UserRepository", "BaseRepository", "app/core/base.py") in edges

    def test_drops_external_base_classes(self, tmp_path):
        source = tmp_path / "external_base.py"
        source.write_text("from pydantic import BaseModel\n\nclass Thing(BaseModel):\n    pass\n")
        parsed = parse_python_file(source, "r", "external_base.py")
        from backend.app.parsing import resolve_inheritance

        resolve_inheritance(parsed)
        assert parsed.inheritance == []


class TestMarkdownParsing:
    def test_splits_on_headings(self, tmp_path):
        doc = tmp_path / "README.md"
        doc.write_text(
            "# Title\n\nIntro paragraph that is long enough to be kept as a section.\n\n"
            "## Setup\n\nRun the installer and wait for it to finish completely.\n"
        )
        parsed = parse_markdown_file(doc, "r", "README.md")
        assert [e.entity_name for e in parsed.entities] == ["Title", "Setup"]
        assert parsed.entities[1].start_line == 5

    def test_ignores_headings_inside_code_fences(self, tmp_path):
        doc = tmp_path / "README.md"
        doc.write_text(
            "# Real\n\nSome text here that is definitely long enough to keep.\n\n"
            "```\n# not a heading\n```\n\nMore text that is also long enough to keep.\n"
        )
        parsed = parse_markdown_file(doc, "r", "README.md")
        assert [e.entity_name for e in parsed.entities] == ["Real"]

    def test_sample_readme_sections(self, parsed_sample):
        sections = [
            e for e in parsed_sample.entities if e.entity_type == EntityType.DOC_SECTION
        ]
        assert any(section.entity_name == "Architecture" for section in sections)
        assert all(section.file_path == "README.md" for section in sections)


def test_parse_repository_covers_the_whole_fixture(parsed_sample, sample_repo_path: Path):
    files = {e.file_path for e in parsed_sample.entities}
    assert "app/main.py" in files
    assert "README.md" in files
    assert len(parsed_sample.entities) > 30
