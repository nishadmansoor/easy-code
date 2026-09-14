"""JavaScript and TypeScript parsing via tree-sitter."""

import pytest

from backend.app.models.entities import EntityType
from backend.app.parsing import js_module_to_candidate_paths, resolve_imports
from backend.app.parsing.javascript_parser import parse_javascript_file

pytest.importorskip("tree_sitter_javascript", reason="tree-sitter grammars not installed")


def named(parsed, name, entity_type=None):
    return [
        e
        for e in parsed.entities
        if e.entity_name == name and (entity_type is None or e.entity_type == entity_type)
    ]


def parse_source(tmp_path, filename: str, source: str):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return parse_javascript_file(path, "r", filename)


class TestEntityExtraction:
    def test_extracts_classes_methods_and_functions(self, parsed_js_sample):
        (circle,) = named(parsed_js_sample, "Circle", EntityType.CLASS)
        assert circle.file_path == "src/circle.ts"
        assert circle.base_classes == ["Base"]
        assert circle.language == "typescript"

        methods = {e.entity_name for e in parsed_js_sample.entities
                   if e.entity_type == EntityType.METHOD and e.parent_name == "Circle"}
        assert methods == {"constructor", "area"}

        (make,) = named(parsed_js_sample, "makeCircle", EntityType.FUNCTION)
        assert make.signature.startswith("function makeCircle(r: number)")

    def test_extracts_typescript_interfaces(self, parsed_js_sample):
        (shape,) = named(parsed_js_sample, "Shape", EntityType.CLASS)
        assert shape.file_path == "src/utils.ts"
        assert shape.signature == "interface Shape"

    def test_extracts_arrow_functions_assigned_to_const(self, parsed_js_sample):
        (identity,) = named(parsed_js_sample, "identity", EntityType.FUNCTION)
        assert identity.file_path == "src/utils.ts"

    def test_javascript_and_typescript_are_tagged_separately(self, parsed_js_sample):
        languages = {e.file_path: e.language for e in parsed_js_sample.entities}
        assert languages["app.js"] == "javascript"
        assert languages["src/circle.ts"] == "typescript"

    def test_captures_jsdoc(self, parsed_js_sample):
        (round2,) = named(parsed_js_sample, "round2", EntityType.FUNCTION)
        assert "Round to two places" in (round2.docstring or "")

    def test_line_ranges_are_one_based_and_ordered(self, parsed_js_sample):
        for entity in parsed_js_sample.entities:
            assert entity.start_line >= 1
            assert entity.end_line >= entity.start_line

    def test_paths_stay_repository_relative(self, parsed_js_sample):
        for entity in parsed_js_sample.entities:
            assert not entity.file_path.startswith("/")

    def test_syntax_errors_do_not_raise(self, tmp_path):
        parsed = parse_source(tmp_path, "broken.ts", "class {{{ not valid")
        assert all(e.entity_type == EntityType.MODULE for e in parsed.entities)

    def test_tsx_is_parsed(self, tmp_path):
        parsed = parse_source(
            tmp_path,
            "Widget.tsx",
            "export function Widget(props: {n: number}) { return <div>{props.n}</div>; }\n",
        )
        assert named(parsed, "Widget", EntityType.FUNCTION)


class TestImportResolution:
    def test_relative_specifier_candidates(self):
        candidates = js_module_to_candidate_paths("./utils", "src/circle.ts")
        assert "src/utils.ts" in candidates
        assert "src/utils.js" in candidates
        assert "src/utils/index.ts" in candidates

    def test_parent_relative_specifier(self):
        candidates = js_module_to_candidate_paths("../utils", "src/lib/base.ts")
        assert "src/utils.ts" in candidates

    def test_js_specifier_also_tries_typescript_source(self):
        # NodeNext imports a .ts file using a .js specifier.
        candidates = js_module_to_candidate_paths("./utils.js", "src/circle.ts")
        assert "src/utils.ts" in candidates

    def test_bare_specifier_is_not_a_repository_file(self):
        assert js_module_to_candidate_paths("react", "app.js") == []

    def test_resolves_across_the_fixture(self, parsed_js_sample):
        resolved = {
            (e.source_file, e.module): e.resolved_file for e in parsed_js_sample.imports
        }
        assert resolved[("src/circle.ts", "./lib/base")] == "src/lib/base.ts"
        assert resolved[("src/lib/base.ts", "../utils")] == "src/utils.ts"
        assert resolved[("app.js", "./src/circle")] == "src/circle.ts"

    def test_leaves_node_modules_imports_unresolved(self, parsed_js_sample):
        react = [e for e in parsed_js_sample.imports if e.module == "react"]
        assert react and all(e.resolved_file is None for e in react)

    def test_captures_require_calls(self, parsed_js_sample):
        modules = {e.module for e in parsed_js_sample.imports if e.source_file == "app.js"}
        assert "./src/circle" in modules

    def test_does_not_guess_by_path_suffix(self):
        """A relative JS specifier is rooted; a suffix match would be a guess."""
        from backend.app.models.entities import ImportEdge

        edges = [
            ImportEdge(repository_id="r", source_file="a/app.js", module="./missing", line=1)
        ]
        resolve_imports(edges, {"totally/other/missing.js"})
        assert edges[0].resolved_file is None


class TestCallAndInheritanceResolution:
    def test_resolves_cross_file_calls(self, parsed_js_sample):
        pairs = {(c.caller_name, c.callee_name, c.callee_file) for c in parsed_js_sample.calls}
        assert ("main", "makeCircle", "src/circle.ts") in pairs
        assert ("area", "round2", "src/utils.ts") in pairs

    def test_resolves_extends(self, parsed_js_sample):
        edges = {(e.child_class, e.parent_class, e.parent_file)
                 for e in parsed_js_sample.inheritance}
        assert ("Circle", "Base", "src/lib/base.ts") in edges

    def test_resolves_implements(self, parsed_js_sample):
        edges = {(e.child_class, e.parent_class) for e in parsed_js_sample.inheritance}
        assert ("Base", "Shape") in edges

    def test_skips_noisy_builtin_calls(self, tmp_path):
        parsed = parse_source(
            tmp_path,
            "noisy.js",
            "function go() { console.log('x'); [1].map(n => n); }\n",
        )
        assert [c.callee_name for c in parsed.calls] == []
