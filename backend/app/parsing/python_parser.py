"""AST-based Python parser.

Extracts modules, classes, functions, methods, imports, calls and inheritance.
Only relationships that are directly visible in the syntax tree are emitted;
call and import targets are resolved against the repository index in a later
pass (see :mod:`backend.app.parsing`), and unresolvable targets are dropped
rather than guessed.
"""

import ast
from pathlib import Path

from backend.app.models.entities import (
    CallEdge,
    CodeEntity,
    EntityType,
    ImportEdge,
    InheritanceEdge,
    ParsedRepository,
)

LANGUAGE = "python"


def _decorator_name(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on valid trees
        return ""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:  # pragma: no cover
        args = "..."
    returns = ""
    if node.returns is not None:
        try:
            returns = f" -> {ast.unparse(node.returns)}"
        except Exception:  # pragma: no cover
            returns = ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){returns}"


def _callee_name(node: ast.Call) -> str | None:
    """The simple name of a call target, or None when it is not a plain name.

    ``foo()`` -> ``foo``; ``self.bar()`` / ``obj.bar()`` / ``mod.bar()`` -> ``bar``.
    Calls on subscripts or arbitrary expressions are ignored.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _collect_calls(node: ast.AST) -> list[tuple[str, int]]:
    """Call targets appearing in ``node``, excluding nested function bodies."""
    found: list[tuple[str, int]] = []
    nested_scopes = {
        child
        for child in ast.walk(node)
        if child is not node
        and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    excluded: set[ast.AST] = set()
    for scope in nested_scopes:
        excluded.update(ast.walk(scope))

    for child in ast.walk(node):
        if child in excluded or not isinstance(child, ast.Call):
            continue
        name = _callee_name(child)
        if name:
            found.append((name, getattr(child, "lineno", 0)))
    return found


def _source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment:
        return segment
    lines = source.splitlines()
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    return "\n".join(lines[start:end])


class _ModuleVisitor:
    """Walks one module, emitting entities and edges with correct scoping."""

    def __init__(self, repo_id: str, rel_path: str, source: str):
        self.repo_id = repo_id
        self.rel_path = rel_path
        self.source = source
        self.result = ParsedRepository()

    def visit_module(self, tree: ast.Module) -> ParsedRepository:
        docstring = ast.get_docstring(tree)
        line_count = len(self.source.splitlines()) or 1
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=LANGUAGE,
                entity_type=EntityType.MODULE,
                entity_name=Path(self.rel_path).stem,
                start_line=1,
                end_line=line_count,
                docstring=docstring,
                source_code="",
            )
        )
        self._visit_body(tree.body, class_name=None, scope_name=None)
        return self.result

    def _visit_body(
        self, body: list[ast.stmt], class_name: str | None, scope_name: str | None
    ) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._visit_class(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit_function(node, class_name=class_name, scope_name=scope_name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self._visit_import(node)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith)):
                # Conditional definitions (``if TYPE_CHECKING:``, ``try: import``)
                # are common and still real definitions.
                self._visit_body(node.body, class_name, scope_name)
                for attr in ("orelse", "finalbody"):
                    self._visit_body(getattr(node, attr, []) or [], class_name, scope_name)
                for handler in getattr(node, "handlers", []) or []:
                    self._visit_body(handler.body, class_name, scope_name)

    def _visit_class(self, node: ast.ClassDef) -> None:
        bases = [_decorator_name(base) for base in node.bases]
        bases = [base for base in bases if base]
        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=LANGUAGE,
                entity_type=EntityType.CLASS,
                entity_name=node.name,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                source_code=_source_segment(self.source, node),
                docstring=ast.get_docstring(node),
                decorators=[_decorator_name(d) for d in node.decorator_list],
                base_classes=bases,
                signature=(
                    f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
                ),
            )
        )
        for base in bases:
            # Only the trailing attribute is a class name: ``a.b.Base`` -> ``Base``.
            simple = base.split("[", 1)[0].rsplit(".", 1)[-1].strip()
            if simple and simple.isidentifier():
                self.result.inheritance.append(
                    InheritanceEdge(
                        repository_id=self.repo_id,
                        child_file=self.rel_path,
                        child_class=node.name,
                        parent_class=simple,
                    )
                )
        self._visit_body(node.body, class_name=node.name, scope_name=node.name)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_name: str | None,
        scope_name: str | None,
    ) -> None:
        is_method = class_name is not None
        entity_type = EntityType.METHOD if is_method else EntityType.FUNCTION
        parent = class_name if is_method else scope_name

        self.result.entities.append(
            CodeEntity(
                repository_id=self.repo_id,
                file_path=self.rel_path,
                language=LANGUAGE,
                entity_type=entity_type,
                entity_name=node.name,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                source_code=_source_segment(self.source, node),
                docstring=ast.get_docstring(node),
                decorators=[_decorator_name(d) for d in node.decorator_list],
                signature=_signature(node),
                parent_name=parent,
                calls=sorted({name for name, _ in _collect_calls(node)}),
            )
        )

        for callee, line in _collect_calls(node):
            self.result.calls.append(
                CallEdge(
                    repository_id=self.repo_id,
                    caller_file=self.rel_path,
                    caller_name=node.name,
                    caller_type=entity_type,
                    callee_name=callee,
                    line=line,
                )
            )

        # Nested functions are entities in their own right, scoped to their parent.
        self._visit_body(node.body, class_name=None, scope_name=node.name)

    def _visit_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.result.imports.append(
                    ImportEdge(
                        repository_id=self.repo_id,
                        source_file=self.rel_path,
                        module=alias.name,
                        imported_names=[alias.asname or alias.name],
                        line=node.lineno,
                    )
                )
            return

        module = node.module or ""
        level = node.level or 0
        self.result.imports.append(
            ImportEdge(
                repository_id=self.repo_id,
                source_file=self.rel_path,
                module=("." * level) + module,
                imported_names=[alias.name for alias in node.names],
                line=node.lineno,
                is_relative=level > 0,
            )
        )


def parse_python_file(file_path: Path, repo_id: str, rel_path: str) -> ParsedRepository:
    """Parse one Python file. Syntax errors yield an empty result, not a crash."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ParsedRepository()
    try:
        tree = ast.parse(source, filename=rel_path)
    except (SyntaxError, ValueError, RecursionError):
        return ParsedRepository()

    return _ModuleVisitor(repo_id, rel_path, source).visit_module(tree)
