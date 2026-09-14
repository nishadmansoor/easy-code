"""Parser registry and repository-level static analysis.

Adding a language means writing a ``parse_<lang>_file(path, repo_id, rel_path)``
function and registering it in :data:`PARSERS`; nothing else in the pipeline is
language-specific.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from backend.app.ingestion.repository import (
    LANGUAGE_EXTENSIONS,
    iter_repository_files,
    relative_path,
)
from backend.app.models.entities import (
    CallEdge,
    CodeEntity,
    EntityType,
    ImportEdge,
    InheritanceEdge,
    ParsedRepository,
)
from backend.app.parsing.javascript_parser import parse_javascript_file
from backend.app.parsing.markdown_parser import parse_markdown_file
from backend.app.parsing.python_parser import parse_python_file

logger = logging.getLogger(__name__)

FileParser = Callable[[Path, str, str], ParsedRepository]

PARSERS: dict[str, FileParser] = {
    "python": parse_python_file,
    "javascript": parse_javascript_file,
    "typescript": parse_javascript_file,
    "markdown": parse_markdown_file,
}

#: Extensions a JavaScript/TypeScript import may resolve to, in resolution order.
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

CALLABLE_TYPES = {EntityType.FUNCTION, EntityType.METHOD}

# Names that are almost always builtins or stdlib helpers; linking them would
# create noise rather than structure.
CALL_NAME_DENYLIST = {
    "print",
    "len",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "range",
    "isinstance",
    "getattr",
    "setattr",
    "hasattr",
    "super",
    "open",
    "sorted",
    "enumerate",
    "zip",
    "map",
    "filter",
    "format",
    "append",
    "extend",
    "get",
    "items",
    "keys",
    "values",
    "join",
    "split",
    "strip",
    "add",
    "update",
}


def parse_repository(repo_path: Path, repo_id: str) -> ParsedRepository:
    """Parse every supported file and resolve cross-file relationships."""
    result = ParsedRepository()

    for path in iter_repository_files(repo_path):
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
        parser = PARSERS.get(language or "")
        if parser is None:
            continue
        rel = relative_path(path, repo_path)
        try:
            result.extend(parser(path, repo_id, rel))
        except Exception:  # a single bad file must not fail the whole repository
            logger.exception("Failed to parse %s", rel)

    known_files = {e.file_path for e in result.entities}
    resolve_imports(result.imports, known_files)
    resolve_calls(result)
    resolve_inheritance(result)
    return result


# --------------------------------------------------------------------------- #
# Resolution passes
# --------------------------------------------------------------------------- #


def module_to_candidate_paths(module: str, source_file: str) -> list[str]:
    """Candidate repository paths for a Python import, most specific first."""
    if not module:
        return []

    if module.startswith("."):
        level = len(module) - len(module.lstrip("."))
        remainder = module.lstrip(".")
        base = Path(source_file).parent
        for _ in range(level - 1):
            base = base.parent
        parts = [p for p in remainder.split(".") if p]
        target = base.joinpath(*parts) if parts else base
        stem = target.as_posix().lstrip("./")
        return [f"{stem}.py", f"{stem}/__init__.py"] if stem else []

    parts = module.split(".")
    stem = "/".join(parts)
    return [f"{stem}.py", f"{stem}/__init__.py"]


def js_module_to_candidate_paths(module: str, source_file: str) -> list[str]:
    """Candidate repository paths for a JavaScript/TypeScript import.

    Only relative specifiers resolve. A bare specifier like ``react`` is a
    ``node_modules`` package, which is not part of the repository.
    """
    if not module.startswith("."):
        return []

    base = Path(source_file).parent / module
    # Path normalises "a/b/../c" only via resolve(), which touches the disk;
    # do it textually so this stays a pure function.
    parts: list[str] = []
    for part in base.as_posix().split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    stem = "/".join(parts)
    if not stem:
        return []

    candidates: list[str] = []
    suffix = Path(stem).suffix
    if suffix in JS_EXTENSIONS:
        candidates.append(stem)
        # TypeScript sources are imported with a .js specifier under NodeNext.
        without = stem[: -len(suffix)]
        candidates.extend(f"{without}{ext}" for ext in JS_EXTENSIONS)
    else:
        candidates.extend(f"{stem}{ext}" for ext in JS_EXTENSIONS)
        candidates.extend(f"{stem}/index{ext}" for ext in JS_EXTENSIONS)

    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen


def candidate_paths_for(module: str, source_file: str) -> list[str]:
    """Dispatch to the right resolver for the importing file's language."""
    if Path(source_file).suffix.lower() in JS_EXTENSIONS:
        return js_module_to_candidate_paths(module, source_file)
    return module_to_candidate_paths(module, source_file)


def resolve_imports(imports: list[ImportEdge], known_files: set[str]) -> None:
    """Point each import at a repository file when one plainly matches.

    Absolute imports are matched against the full path first and then against a
    path suffix, which covers ``src/`` and other package layouts. Imports that
    resolve to nothing (third-party or stdlib) are left unresolved.
    """
    for edge in imports:
        is_javascript = Path(edge.source_file).suffix.lower() in JS_EXTENSIONS

        for candidate in candidate_paths_for(edge.module, edge.source_file):
            if candidate in known_files:
                edge.resolved_file = candidate
                break
            # A relative JS specifier is already rooted at the importing file,
            # so a suffix match would be a guess rather than a resolution.
            if is_javascript:
                continue
            matches = [path for path in known_files if path.endswith("/" + candidate)]
            if len(matches) == 1:
                edge.resolved_file = matches[0]
                break

        if is_javascript:
            if edge.resolved_file == edge.source_file:
                edge.resolved_file = None
            continue

        if edge.resolved_file is None and not edge.is_relative:
            # ``from package import module`` where the module is the leaf file.
            for name in edge.imported_names:
                submodule = f"{edge.module}.{name}" if edge.module else name
                for candidate in module_to_candidate_paths(submodule, edge.source_file):
                    matches = [
                        path
                        for path in known_files
                        if path == candidate or path.endswith("/" + candidate)
                    ]
                    if len(matches) == 1:
                        edge.resolved_file = matches[0]
                        break
                if edge.resolved_file:
                    break

        if edge.resolved_file == edge.source_file:
            edge.resolved_file = None


def _index_by_name(
    entities: list[CodeEntity], types: set[EntityType]
) -> dict[str, list[CodeEntity]]:
    index: dict[str, list[CodeEntity]] = {}
    for entity in entities:
        if entity.entity_type in types:
            index.setdefault(entity.entity_name, []).append(entity)
    return index


def _pick_target(
    candidates: list[CodeEntity], source_file: str, visible_files: set[str]
) -> CodeEntity | None:
    """Choose a definition for a name, or None when the choice is ambiguous.

    Preference order: same file, then a file the source file imports, then a
    globally unique definition. Ambiguity is resolved by giving up — an invented
    relationship is worse than a missing one.
    """
    if not candidates:
        return None

    same_file = [c for c in candidates if c.file_path == source_file]
    if same_file:
        return same_file[0]

    imported = [c for c in candidates if c.file_path in visible_files]
    if len(imported) == 1:
        return imported[0]

    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_calls(parsed: ParsedRepository) -> None:
    """Attach a file and type to call edges whose callee is defined in-repo."""
    index = _index_by_name(parsed.entities, CALLABLE_TYPES)
    imports_by_file: dict[str, set[str]] = {}
    for edge in parsed.imports:
        if edge.resolved_file:
            imports_by_file.setdefault(edge.source_file, set()).add(edge.resolved_file)

    resolved: list[CallEdge] = []
    for call in parsed.calls:
        if call.callee_name in CALL_NAME_DENYLIST:
            continue
        target = _pick_target(
            index.get(call.callee_name, []),
            call.caller_file,
            imports_by_file.get(call.caller_file, set()),
        )
        if target is None:
            continue
        if target.file_path == call.caller_file and target.entity_name == call.caller_name:
            continue  # direct recursion adds no navigational value
        call.callee_file = target.file_path
        call.callee_type = target.entity_type
        resolved.append(call)

    parsed.calls = resolved


def resolve_inheritance(parsed: ParsedRepository) -> None:
    index = _index_by_name(parsed.entities, {EntityType.CLASS})
    imports_by_file: dict[str, set[str]] = {}
    for edge in parsed.imports:
        if edge.resolved_file:
            imports_by_file.setdefault(edge.source_file, set()).add(edge.resolved_file)

    resolved: list[InheritanceEdge] = []
    for edge in parsed.inheritance:
        target = _pick_target(
            index.get(edge.parent_class, []),
            edge.child_file,
            imports_by_file.get(edge.child_file, set()),
        )
        if target is None:
            continue  # external base class (object, BaseModel from a library, ...)
        edge.parent_file = target.file_path
        resolved.append(edge)

    parsed.inheritance = resolved
