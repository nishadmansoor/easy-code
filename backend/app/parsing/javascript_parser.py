"""JavaScript and TypeScript parser, built on tree-sitter.

Python has ``ast`` in the standard library; JavaScript does not, so this uses
tree-sitter — the same incremental parser GitHub uses for code navigation. One
module covers .js/.jsx/.mjs/.cjs, .ts and .tsx by selecting the matching
grammar, because the extracted shapes are identical.

As with the Python parser, only relationships visible in the syntax tree are
emitted; resolution against the repository index happens later.
"""

import logging
from functools import lru_cache
from pathlib import Path

from backend.app.models.entities import (
    CallEdge,
    CodeEntity,
    EntityType,
    ImportEdge,
    InheritanceEdge,
    ParsedRepository,
)

logger = logging.getLogger(__name__)

TYPESCRIPT_SUFFIXES = {".ts", ".mts", ".cts"}
TSX_SUFFIXES = {".tsx"}

#: Callee names so common they would bury real structure in noise.
CALL_NAME_DENYLIST = {
    "require",
    "console",
    "log",
    "warn",
    "error",
    "then",
    "catch",
    "map",
    "filter",
    "forEach",
    "reduce",
    "push",
    "pop",
    "slice",
    "splice",
    "join",
    "split",
    "trim",
    "replace",
    "test",
    "match",
    "parse",
    "stringify",
    "toString",
    "keys",
    "values",
    "entries",
    "assign",
    "bind",
    "call",
    "apply",
    "setTimeout",
    "setInterval",
    "addEventListener",
    "querySelector",
    "getElementById",
    # Math and other global helpers that would otherwise look like call edges.
    "round",
    "floor",
    "ceil",
    "abs",
    "min",
    "max",
    "random",
    "now",
}


@lru_cache(maxsize=4)
def _parser_for(grammar: str):
    """Build and cache a tree-sitter parser. None when grammars are missing."""
    try:
        from tree_sitter import Language, Parser

        if grammar == "typescript":
            import tree_sitter_typescript as ts

            language = Language(ts.language_typescript())
        elif grammar == "tsx":
            import tree_sitter_typescript as ts

            language = Language(ts.language_tsx())
        else:
            import tree_sitter_javascript as js

            language = Language(js.language())
        return Parser(language)
    except Exception:
        logger.warning(
            "tree-sitter grammar %r unavailable; JavaScript/TypeScript files "
            "will be indexed for text but not parsed structurally",
            grammar,
            exc_info=True,
        )
        return None


def _grammar_for(rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    if suffix in TSX_SUFFIXES:
        return "tsx"
    if suffix in TYPESCRIPT_SUFFIXES:
        return "typescript"
    return "javascript"


def _language_for(rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    if suffix in TYPESCRIPT_SUFFIXES | TSX_SUFFIXES:
        return "typescript"
    return "javascript"


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_of(node, source: bytes) -> str | None:
    field = node.child_by_field_name("name")
    return _text(field, source) if field is not None else None


def _line_range(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _docstring_before(node, source: bytes) -> str | None:
    """The JSDoc block immediately preceding a declaration, if any.

    For `export function foo()` the comment is a sibling of the *export*
    statement rather than of the declaration, so walk up through wrappers.
    """
    wrappers = {
        "export_statement",
        "lexical_declaration",
        "variable_declaration",
        "variable_declarator",
    }

    # Climb to the outermost statement, since `export function f()` puts the
    # comment before the export rather than before the declaration.
    current = node
    while current.parent is not None and current.parent.type in wrappers:
        current = current.parent

    previous = current.prev_sibling
    if previous is not None and previous.type == "comment":
        text = _text(previous, source).strip()
        if text.startswith("/**"):
            return text
    return None


def _signature(node, source: bytes, name: str, keyword: str) -> str:
    params = node.child_by_field_name("parameters")
    returns = node.child_by_field_name("return_type")
    rendered = _text(params, source) if params is not None else "()"
    suffix = _text(returns, source) if returns is not None else ""
    return f"{keyword} {name}{rendered}{suffix}".strip()


#: Nodes that introduce a new callable scope; calls inside them belong to them.
SCOPE_TYPES = {
    "function_declaration",
    "function_expression",
    "generator_function_declaration",
    "arrow_function",
    "method_definition",
    "class_declaration",
    "abstract_class_declaration",
    "class",
}


def _collect_calls(node, source: bytes) -> list[tuple[str, int]]:
    """Call targets directly inside ``node``, excluding nested scopes."""
    found: list[tuple[str, int]] = []

    def visit(current, is_root: bool) -> None:
        if not is_root and current.type in SCOPE_TYPES:
            return
        if current.type in ("call_expression", "new_expression"):
            function = current.child_by_field_name(
                "function"
            ) or current.child_by_field_name("constructor")
            if function is not None:
                name = None
                if function.type == "identifier":
                    name = _text(function, source)
                elif function.type == "member_expression":
                    prop = function.child_by_field_name("property")
                    if prop is not None:
                        name = _text(prop, source)
                if name and name not in CALL_NAME_DENYLIST and name.isidentifier():
                    found.append((name, current.start_point[0] + 1))
        for child in current.children:
            visit(child, False)

    visit(node, True)
    return found


class _ModuleWalker:
    """Walks one module, emitting entities and edges."""

    def __init__(self, repo_id: str, rel_path: str, source: bytes):
        self.repo_id = repo_id
        self.rel_path = rel_path
        self.source = source
        self.language = _language_for(rel_path)
        self.result = ParsedRepository()

    def walk(self, root) -> ParsedRepository:
        line_count = self.source.count(b"\n") + 1
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=self.language,
                entity_type=EntityType.MODULE,
                entity_name=Path(self.rel_path).stem,
                start_line=1,
                end_line=line_count,
            )
        )
        self._visit(root, class_name=None, scope_name=None)
        return self.result

    def _visit(self, node, class_name: str | None, scope_name: str | None) -> None:
        for child in node.children:
            kind = child.type

            if kind == "export_statement":
                # `export class Foo {}` — recurse so the declaration is seen.
                self._visit(child, class_name, scope_name)
            elif kind in ("class_declaration", "abstract_class_declaration", "class"):
                self._visit_class(child)
            elif kind in (
                "function_declaration",
                "generator_function_declaration",
                "function_expression",
            ):
                self._visit_function(child, class_name=None, scope_name=scope_name)
            elif kind in ("lexical_declaration", "variable_declaration"):
                self._visit_variable_declaration(child, scope_name)
            elif kind in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                self._visit_type_declaration(child)
            elif kind == "import_statement":
                self._visit_import(child)
            elif kind in ("statement_block", "program", "if_statement", "try_statement"):
                self._visit(child, class_name, scope_name)

    def _visit_class(self, node) -> None:
        name = _name_of(node, self.source)
        if not name:
            return

        bases = self._heritage(node)
        start, end = _line_range(node)
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=self.language,
                entity_type=EntityType.CLASS,
                entity_name=name,
                start_line=start,
                end_line=end,
                source_code=_text(node, self.source),
                docstring=_docstring_before(node, self.source),
                base_classes=bases,
                signature=f"class {name}" + (f" extends {bases[0]}" if bases else ""),
            )
        )
        for base in bases:
            self.result.inheritance.append(
                InheritanceEdge(
                    repository_id=self.repo_id,
                    child_file=self.rel_path,
                    child_class=name,
                    parent_class=base,
                )
            )

        body = node.child_by_field_name("body")
        if body is not None:
            for member in body.children:
                if member.type in ("method_definition", "abstract_method_signature"):
                    self._visit_method(member, class_name=name)

    def _heritage(self, node) -> list[str]:
        """Base classes and implemented interfaces, by simple name."""
        names: list[str] = []
        for child in node.children:
            if child.type != "class_heritage":
                continue
            for clause in child.children:
                if clause.type not in ("extends_clause", "implements_clause"):
                    continue
                for item in clause.children:
                    if item.type in ("identifier", "type_identifier", "member_expression"):
                        simple = _text(item, self.source).rsplit(".", 1)[-1].strip()
                        if simple.isidentifier() and simple not in names:
                            names.append(simple)
        return names

    def _visit_method(self, node, class_name: str) -> None:
        name = _name_of(node, self.source)
        if not name:
            return
        start, end = _line_range(node)
        calls = _collect_calls(node, self.source)
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=self.language,
                entity_type=EntityType.METHOD,
                entity_name=name,
                start_line=start,
                end_line=end,
                source_code=_text(node, self.source),
                docstring=_docstring_before(node, self.source),
                signature=_signature(node, self.source, name, ""),
                parent_name=class_name,
                calls=sorted({call for call, _ in calls}),
            )
        )
        self._record_calls(name, EntityType.METHOD, calls)

    def _visit_function(self, node, class_name: str | None, scope_name: str | None) -> None:
        name = _name_of(node, self.source)
        if not name:
            return
        self._emit_function(node, name, scope_name)

    def _emit_function(self, node, name: str, scope_name: str | None) -> None:
        start, end = _line_range(node)
        calls = _collect_calls(node, self.source)
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=self.language,
                entity_type=EntityType.FUNCTION,
                entity_name=name,
                start_line=start,
                end_line=end,
                source_code=_text(node, self.source),
                docstring=_docstring_before(node, self.source),
                signature=_signature(node, self.source, name, "function"),
                parent_name=scope_name,
                calls=sorted({call for call, _ in calls}),
            )
        )
        self._record_calls(name, EntityType.FUNCTION, calls)

        body = node.child_by_field_name("body")
        if body is not None:
            self._visit(body, class_name=None, scope_name=name)

    def _visit_variable_declaration(self, node, scope_name: str | None) -> None:
        """`const f = () => {}` and `const x = require('./y')`."""
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            value = declarator.child_by_field_name("value")
            if value is None:
                continue

            if value.type in ("arrow_function", "function_expression", "function"):
                name = _name_of(declarator, self.source)
                if name:
                    self._emit_function(value, name, scope_name)
            elif value.type == "call_expression":
                self._maybe_require(value)

    def _maybe_require(self, node) -> None:
        function = node.child_by_field_name("function")
        if function is None or _text(function, self.source) != "require":
            return
        args = node.child_by_field_name("arguments")
        if args is None:
            return
        for arg in args.children:
            if arg.type == "string":
                module = _text(arg, self.source).strip("\"'`")
                if module:
                    self.result.imports.append(
                        ImportEdge(
                            repository_id=self.repo_id,
                            source_file=self.rel_path,
                            module=module,
                            line=node.start_point[0] + 1,
                            is_relative=module.startswith("."),
                        )
                    )

    def _visit_type_declaration(self, node) -> None:
        """TypeScript interfaces, type aliases and enums are indexable units."""
        name = _name_of(node, self.source)
        if not name:
            return
        start, end = _line_range(node)
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=self.language,
                entity_type=EntityType.CLASS,
                entity_name=name,
                start_line=start,
                end_line=end,
                source_code=_text(node, self.source),
                docstring=_docstring_before(node, self.source),
                signature=f"{node.type.split('_')[0]} {name}",
            )
        )

    def _visit_import(self, node) -> None:
        source_node = node.child_by_field_name("source")
        if source_node is None:
            return
        module = _text(source_node, self.source).strip("\"'`")
        if not module:
            return

        names: list[str] = []
        for child in node.children:
            if child.type == "import_clause":
                names.extend(
                    _text(part, self.source)
                    for part in child.children
                    if part.type in ("identifier", "namespace_import", "named_imports")
                )

        self.result.imports.append(
            ImportEdge(
                repository_id=self.repo_id,
                source_file=self.rel_path,
                module=module,
                imported_names=names,
                line=node.start_point[0] + 1,
                is_relative=module.startswith("."),
            )
        )

    def _record_calls(
        self, caller: str, caller_type: EntityType, calls: list[tuple[str, int]]
    ) -> None:
        for callee, line in calls:
            self.result.calls.append(
                CallEdge(
                    repository_id=self.repo_id,
                    caller_file=self.rel_path,
                    caller_name=caller,
                    caller_type=caller_type,
                    callee_name=callee,
                    line=line,
                )
            )


def parse_javascript_file(file_path: Path, repo_id: str, rel_path: str) -> ParsedRepository:
    """Parse one JS/TS file. Missing grammars or parse errors yield no entities."""
    parser = _parser_for(_grammar_for(rel_path))
    if parser is None:
        return ParsedRepository()

    try:
        source = file_path.read_bytes()
    except OSError:
        return ParsedRepository()

    try:
        tree = parser.parse(source)
    except Exception:
        logger.debug("tree-sitter failed on %s", rel_path, exc_info=True)
        return ParsedRepository()

    return _ModuleWalker(repo_id, rel_path, source).walk(tree.root_node)
