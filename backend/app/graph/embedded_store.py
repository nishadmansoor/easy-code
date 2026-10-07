"""A dependency-free code graph, for running without Neo4j.

Implements the same interface as :class:`~backend.app.graph.store.GraphStore`
and returns identically shaped results, so everything above the store layer is
unaware of which backend is in use.

The graph is small — tens of thousands of nodes for a large repository — so it
is held in plain dicts and persisted as JSON, one file per repository under
``data/graph/``. JSON rather than pickle because the file is written once and
read many times, and a pickle would couple the on-disk format to the Python
version and be unsafe to load. ``networkx`` is used only for shortest paths,
where a real graph algorithm earns its keep.

The trade-off against Neo4j is scale: every query is a scan over in-memory
lists. That is microseconds for a 1,000-file repository and would be the wrong
choice for a 100,000-file one.
"""

import json
import logging
import threading
from collections import Counter, defaultdict
from pathlib import Path

from backend.app.config.settings import settings

logger = logging.getLogger(__name__)

#: Node labels, matching the Neo4j backend's ``node_type`` values exactly.
FILE = "File"
CLASS = "Class"
FUNCTION = "Function"
METHOD = "Method"
REPOSITORY = "Repository"

CALLABLE_LABELS = (FUNCTION, METHOD)


def _empty_repository(repository_id: str) -> dict:
    return {
        "repository_id": repository_id,
        "url": "",
        "name": "",
        "files": {},
        "classes": [],
        "functions": [],
        "methods": [],
        "imports": [],
        "calls": [],
        "inheritance": [],
    }


class EmbeddedGraphStore:
    """A JSON-backed code graph with the GraphStore interface."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or settings.graph_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------- storage

    def _path_for(self, repository_id: str) -> Path:
        # Repository ids are generated hex, but never trust one into a path.
        safe = "".join(c for c in repository_id if c.isalnum() or c in "-_")
        return self.directory / f"{safe}.json"

    def _load(self, repository_id: str) -> dict:
        with self._lock:
            cached = self._cache.get(repository_id)
            if cached is not None:
                return cached

            path = self._path_for(repository_id)
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    logger.exception("Could not read graph for %s", repository_id)
                    data = _empty_repository(repository_id)
            else:
                data = _empty_repository(repository_id)

            self._cache[repository_id] = data
            return data

    def _save(self, repository_id: str) -> None:
        with self._lock:
            data = self._cache.get(repository_id)
            if data is None:
                return
            path = self._path_for(repository_id)
            # Write to a sibling then replace, so a crash cannot truncate the
            # graph of an already-indexed repository.
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(path)

    def close(self) -> None:
        with self._lock:
            self._cache.clear()

    def ensure_schema(self) -> None:
        """No schema to create; present so the backends are interchangeable."""

    def ping(self) -> bool:
        """Always reachable: the backend is this process and a directory."""
        return self.directory.is_dir()

    # --------------------------------------------------------------- write

    def clear_repository(self, repository_id: str) -> None:
        with self._lock:
            self._cache[repository_id] = _empty_repository(repository_id)
            self._path_for(repository_id).unlink(missing_ok=True)
            self._cache.pop(repository_id, None)

    def create_repository_node(self, repository_id: str, url: str, name: str = "") -> None:
        data = self._load(repository_id)
        data["url"] = url
        data["name"] = name
        self._save(repository_id)

    def create_files(self, repository_id: str, files: list[dict]) -> None:
        if not files:
            return
        data = self._load(repository_id)
        for row in files:
            data["files"][row["file_path"]] = {"language": row.get("language") or ""}
        self._save(repository_id)

    def create_classes(self, repository_id: str, classes: list[dict]) -> None:
        self._append(repository_id, "classes", classes)

    def create_functions(self, repository_id: str, functions: list[dict]) -> None:
        self._append(repository_id, "functions", functions)

    def create_methods(self, repository_id: str, methods: list[dict]) -> None:
        self._append(repository_id, "methods", methods)

    def create_imports(self, repository_id: str, imports: list[dict]) -> None:
        self._append(repository_id, "imports", imports)

    def create_calls(self, repository_id: str, calls: list[dict]) -> None:
        self._append(repository_id, "calls", calls)

    def create_inheritance(self, repository_id: str, edges: list[dict]) -> None:
        self._append(repository_id, "inheritance", edges)

    def _append(self, repository_id: str, key: str, rows: list[dict]) -> None:
        if not rows:
            return
        data = self._load(repository_id)
        data[key].extend(rows)
        self._save(repository_id)

    # ---------------------------------------------------------------- read

    def get_repository_overview(self, repository_id: str) -> dict:
        data = self._load(repository_id)
        classes_by_file = defaultdict(list)
        functions_by_file = defaultdict(list)
        for row in data["classes"]:
            classes_by_file[row["file_path"]].append(row["name"])
        for row in data["functions"]:
            functions_by_file[row["file_path"]].append(row["name"])

        return {
            "files": [
                {
                    "file_path": path,
                    "language": meta.get("language") or "",
                    "classes": sorted(set(classes_by_file.get(path, []))),
                    "functions": sorted(set(functions_by_file.get(path, []))),
                }
                for path, meta in sorted(data["files"].items())
            ]
        }

    def get_statistics(self, repository_id: str) -> dict:
        data = self._load(repository_id)
        if not data["files"] and not data["classes"] and not data["functions"]:
            return {
                "nodes": {},
                "node_count": 0,
                "relationships": {},
                "relationship_count": 0,
            }

        nodes = {
            REPOSITORY: 1,
            FILE: len(data["files"]),
            CLASS: len(data["classes"]),
            FUNCTION: len(data["functions"]),
            METHOD: len(data["methods"]),
        }
        nodes = {label: count for label, count in nodes.items() if count}

        method_calls = sum(
            1
            for call in data["calls"]
            if call.get("caller_type") == "method" and call.get("callee_type") == "method"
        )
        relationships = {
            "REPOSITORY_CONTAINS_FILE": len(data["files"]),
            "FILE_DEFINES_CLASS": len(data["classes"]),
            "FILE_DEFINES_FUNCTION": len(data["functions"]),
            "FILE_DEFINES_METHOD": len(data["methods"]),
            "CLASS_CONTAINS_METHOD": len(data["methods"]),
            "FILE_IMPORTS_FILE": len(self._unique_imports(data)),
            "FUNCTION_CALLS_FUNCTION": len(data["calls"]) - method_calls,
            "METHOD_CALLS_METHOD": method_calls,
            "CLASS_INHERITS_CLASS": len(data["inheritance"]),
        }
        relationships = {name: count for name, count in relationships.items() if count}

        return {
            "nodes": nodes,
            "node_count": sum(nodes.values()),
            "relationships": relationships,
            "relationship_count": sum(relationships.values()),
        }

    @staticmethod
    def _unique_imports(data: dict) -> set[tuple[str, str]]:
        return {
            (row["source_file"], row["target_file"])
            for row in data["imports"]
            if row.get("target_file")
        }

    def get_files(self, repository_id: str) -> list[dict]:
        data = self._load(repository_id)
        return [
            {"file_path": path, "language": meta.get("language") or ""}
            for path, meta in sorted(data["files"].items())
        ]

    def get_file_contents(self, repository_id: str, file_path: str) -> dict | None:
        data = self._load(repository_id)
        if file_path not in data["files"]:
            return None

        return {
            "file_path": file_path,
            "language": data["files"][file_path].get("language") or "",
            "classes": [
                {
                    "name": row["name"],
                    "start_line": row.get("start_line"),
                    "end_line": row.get("end_line"),
                    "file_path": file_path,
                }
                for row in data["classes"]
                if row["file_path"] == file_path
            ],
            "functions": [
                {
                    "name": row["name"],
                    "start_line": row.get("start_line"),
                    "end_line": row.get("end_line"),
                    "file_path": file_path,
                    "signature": row.get("signature"),
                }
                for row in data["functions"]
                if row["file_path"] == file_path
            ],
            "methods": [
                {
                    "name": row["name"],
                    "class_name": row.get("class_name"),
                    "start_line": row.get("start_line"),
                    "end_line": row.get("end_line"),
                    "file_path": file_path,
                    "signature": row.get("signature"),
                }
                for row in data["methods"]
                if row["file_path"] == file_path
            ],
        }

    def get_class_info(self, repository_id: str, class_name: str) -> list[dict]:
        data = self._load(repository_id)
        classes = [row for row in data["classes"] if row["name"] == class_name]
        if not classes:
            return []

        by_name: dict[str, list[dict]] = defaultdict(list)
        for row in data["classes"]:
            by_name[row["name"]].append(row)

        def describe(name: str, file_path: str | None) -> dict:
            for row in by_name.get(name, []):
                if file_path is None or row["file_path"] == file_path:
                    return {
                        "name": row["name"],
                        "file_path": row["file_path"],
                        "start_line": row.get("start_line"),
                        "end_line": row.get("end_line"),
                    }
            return {"name": name, "file_path": file_path, "start_line": 0, "end_line": 0}

        results = []
        for cls in classes:
            methods = [
                {
                    "name": row["name"],
                    "class_name": row.get("class_name"),
                    "file_path": row["file_path"],
                    "start_line": row.get("start_line"),
                    "end_line": row.get("end_line"),
                    "signature": row.get("signature"),
                }
                for row in data["methods"]
                if row["file_path"] == cls["file_path"]
                and row.get("class_name") == class_name
            ]
            bases = [
                describe(edge["parent_class"], edge.get("parent_file"))
                for edge in data["inheritance"]
                if edge["child_class"] == class_name
                and edge["child_file"] == cls["file_path"]
                and edge.get("parent_file")
            ]
            subclasses = [
                describe(edge["child_class"], edge.get("child_file"))
                for edge in data["inheritance"]
                if edge["parent_class"] == class_name and edge.get("parent_file")
            ]
            results.append(
                {
                    "class_name": cls["name"],
                    "file_path": cls["file_path"],
                    "start_line": cls.get("start_line"),
                    "end_line": cls.get("end_line"),
                    "methods": methods,
                    "bases": bases,
                    "subclasses": subclasses,
                }
            )
        return results

    def _callable_index(self, data: dict) -> dict[tuple[str, str], dict]:
        """(file_path, name) -> node, for functions and methods."""
        index: dict[tuple[str, str], dict] = {}
        for row in data["functions"]:
            index[(row["file_path"], row["name"])] = {**row, "node_type": FUNCTION}
        for row in data["methods"]:
            index.setdefault((row["file_path"], row["name"]), {**row, "node_type": METHOD})
        return index

    def get_function_callers(self, repository_id: str, function_name: str) -> list[dict]:
        data = self._load(repository_id)
        index = self._callable_index(data)

        seen: set[tuple] = set()
        results: list[dict] = []
        for call in data["calls"]:
            if call.get("callee_name") != function_name or not call.get("callee_file"):
                continue
            caller = index.get((call["caller_file"], call["caller_name"]))
            target = index.get((call["callee_file"], call["callee_name"]))
            if caller is None:
                continue
            key = (caller["file_path"], caller["name"], call["callee_file"])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "caller_type": caller["node_type"],
                    "caller_name": caller["name"],
                    "caller_file": caller["file_path"],
                    "caller_start": caller.get("start_line"),
                    "caller_end": caller.get("end_line"),
                    "target_file": call["callee_file"],
                    "target_start": (target or {}).get("start_line"),
                    "target_end": (target or {}).get("end_line"),
                }
            )
        results.sort(key=lambda r: (r["caller_file"], r["caller_start"] or 0))
        return results

    def get_function_callees(self, repository_id: str, function_name: str) -> list[dict]:
        data = self._load(repository_id)
        index = self._callable_index(data)

        seen: set[tuple] = set()
        results: list[dict] = []
        for call in data["calls"]:
            if call.get("caller_name") != function_name or not call.get("callee_file"):
                continue
            callee = index.get((call["callee_file"], call["callee_name"]))
            if callee is None:
                continue
            key = (callee["file_path"], callee["name"])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "callee_type": callee["node_type"],
                    "callee_name": callee["name"],
                    "callee_file": callee["file_path"],
                    "callee_start": callee.get("start_line"),
                    "callee_end": callee.get("end_line"),
                }
            )
        results.sort(key=lambda r: (r["callee_file"], r["callee_start"] or 0))
        return results

    def get_importers(self, repository_id: str, file_path: str) -> list[dict]:
        data = self._load(repository_id)
        paths = sorted(
            {
                row["source_file"]
                for row in data["imports"]
                if row.get("target_file") == file_path
            }
        )
        return [{"file_path": path} for path in paths]

    def get_imported_files(self, repository_id: str, file_path: str) -> list[dict]:
        data = self._load(repository_id)
        paths = sorted(
            {
                row["target_file"]
                for row in data["imports"]
                if row["source_file"] == file_path and row.get("target_file")
            }
        )
        return [{"file_path": path} for path in paths]

    def find_entities(self, repository_id: str, name: str, limit: int = 10) -> list[dict]:
        data = self._load(repository_id)
        results: list[dict] = []
        for label, rows in ((CLASS, data["classes"]), (FUNCTION, data["functions"]),
                            (METHOD, data["methods"])):
            for row in rows:
                if row["name"] != name:
                    continue
                results.append(
                    {
                        "entity_type": label,
                        "name": row["name"],
                        "file_path": row["file_path"],
                        "start_line": row.get("start_line"),
                        "end_line": row.get("end_line"),
                        "signature": row.get("signature"),
                        "docstring": row.get("docstring"),
                        "class_name": row.get("class_name"),
                    }
                )
                if len(results) >= limit:
                    return results
        return results

    def get_entity_neighborhood(
        self, repository_id: str, name: str, limit: int = 15
    ) -> list[dict]:
        data = self._load(repository_id)
        index = self._callable_index(data)
        rows: list[dict] = []

        def add(source: dict, relationship: str, outgoing: bool, other: dict) -> None:
            rows.append(
                {
                    "relationship": relationship,
                    "outgoing": outgoing,
                    "source_type": source["node_type"],
                    "source_name": source["name"],
                    "source_file": source["file_path"],
                    "other_type": other.get("node_type", FILE),
                    "other_name": other.get("name") or other.get("file_path"),
                    "other_file": other.get("file_path"),
                    "other_start": other.get("start_line"),
                    "other_end": other.get("end_line"),
                }
            )

        for (file_path, entity_name), node in index.items():
            if entity_name != name:
                continue
            for call in data["calls"]:
                if len(rows) >= limit:
                    break
                if call["caller_file"] == file_path and call["caller_name"] == name:
                    other = index.get((call.get("callee_file"), call.get("callee_name")))
                    if other:
                        add(node, "FUNCTION_CALLS_FUNCTION", True, other)
                elif call.get("callee_file") == file_path and call.get("callee_name") == name:
                    other = index.get((call["caller_file"], call["caller_name"]))
                    if other:
                        add(node, "FUNCTION_CALLS_FUNCTION", False, other)

        for edge in data["inheritance"]:
            if len(rows) >= limit:
                break
            if edge["child_class"] == name and edge.get("parent_file"):
                rows.append(
                    {
                        "relationship": "CLASS_INHERITS_CLASS",
                        "outgoing": True,
                        "source_type": CLASS,
                        "source_name": name,
                        "source_file": edge["child_file"],
                        "other_type": CLASS,
                        "other_name": edge["parent_class"],
                        "other_file": edge["parent_file"],
                        "other_start": 0,
                        "other_end": 0,
                    }
                )
            elif edge["parent_class"] == name and edge.get("parent_file"):
                rows.append(
                    {
                        "relationship": "CLASS_INHERITS_CLASS",
                        "outgoing": False,
                        "source_type": CLASS,
                        "source_name": name,
                        "source_file": edge["parent_file"],
                        "other_type": CLASS,
                        "other_name": edge["child_class"],
                        "other_file": edge["child_file"],
                        "other_start": 0,
                        "other_end": 0,
                    }
                )

        return rows[:limit]

    def get_dependency_chain(
        self, repository_id: str, source_file: str, target_file: str, max_depth: int = 6
    ) -> list[dict]:
        import networkx as nx

        data = self._load(repository_id)
        graph = nx.DiGraph()
        graph.add_edges_from(self._unique_imports(data))
        if source_file not in graph or target_file not in graph:
            return []
        try:
            chain = nx.shortest_path(graph, source_file, target_file)
        except nx.NetworkXNoPath:
            return []
        if len(chain) - 1 > max_depth:
            return []
        return [{"chain": chain}]

    def _degree_counts(self, data: dict) -> tuple[Counter, Counter]:
        """(importers_per_file, imports_per_file) over unique import edges."""
        importers: Counter = Counter()
        imports: Counter = Counter()
        for source, target in self._unique_imports(data):
            imports[source] += 1
            importers[target] += 1
        return importers, imports

    def get_entry_points(self, repository_id: str, limit: int = 10) -> list[dict]:
        data = self._load(repository_id)
        importers, imports = self._degree_counts(data)
        rows = [
            {"file_path": path, "dependencies": imports.get(path, 0)}
            for path, meta in data["files"].items()
            if meta.get("language") == "python"
            and importers.get(path, 0) == 0
            and imports.get(path, 0) > 0
        ]
        rows.sort(key=lambda r: (-r["dependencies"], r["file_path"]))
        return rows[:limit]

    def get_central_files(self, repository_id: str, limit: int = 10) -> list[dict]:
        data = self._load(repository_id)
        importers, _ = self._degree_counts(data)
        rows = [
            {"file_path": path, "importers": count} for path, count in importers.items() if count
        ]
        rows.sort(key=lambda r: (-r["importers"], r["file_path"]))
        return rows[:limit]

    def get_file_network(self, repository_id: str, limit: int = 150) -> dict:
        data = self._load(repository_id)
        importers, imports = self._degree_counts(data)

        definitions: Counter = Counter()
        for row in data["classes"] + data["functions"]:
            definitions[row["file_path"]] += 1

        connected = [
            path
            for path in data["files"]
            if importers.get(path, 0) or imports.get(path, 0)
        ]
        connected.sort(key=lambda p: (-(importers.get(p, 0) + imports.get(p, 0)), p))
        selected = connected[:limit]
        chosen = set(selected)

        nodes = [
            {
                "id": path,
                "language": data["files"][path].get("language") or "",
                "imports": imports.get(path, 0),
                "importers": importers.get(path, 0),
                "definitions": definitions.get(path, 0),
            }
            for path in selected
        ]
        edges = [
            {"source": source, "target": target}
            for source, target in sorted(self._unique_imports(data))
            if source in chosen and target in chosen
        ]

        return {
            "scope": "files",
            "nodes": nodes,
            "edges": edges,
            "total_nodes": len(connected),
            "truncated": len(connected) > len(selected),
        }

    def get_call_network(
        self, repository_id: str, name: str, depth: int = 2, limit: int = 200
    ) -> dict:
        data = self._load(repository_id)
        index = self._callable_index(data)
        depth = max(1, min(int(depth), 3))

        adjacency: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        for call in data["calls"]:
            if not call.get("callee_file"):
                continue
            a = (call["caller_file"], call["caller_name"])
            b = (call["callee_file"], call["callee_name"])
            if a in index and b in index:
                adjacency[a].add(b)
                adjacency[b].add(a)

        starts = [key for key in index if key[1] == name]
        frontier = set(starts)
        reached = set(starts)
        for _ in range(depth):
            nxt: set[tuple[str, str]] = set()
            for node in frontier:
                nxt |= adjacency.get(node, set())
            frontier = nxt - reached
            reached |= nxt
            if not frontier:
                break

        def node_id(key: tuple[str, str]) -> str:
            return f"{key[0]}::{key[1]}"

        nodes = []
        for key in reached:
            row = index[key]
            nodes.append(
                {
                    "id": node_id(key),
                    "label": row["name"],
                    "file_path": row["file_path"],
                    "entity_type": row["node_type"].lower(),
                    "start_line": row.get("start_line") or 0,
                    "end_line": row.get("end_line") or 0,
                    "focus": row["name"] == name,
                }
            )

        edges = []
        emitted: set[tuple[str, str]] = set()
        for call in data["calls"]:
            if not call.get("callee_file"):
                continue
            a = (call["caller_file"], call["caller_name"])
            b = (call["callee_file"], call["callee_name"])
            if a in reached and b in reached:
                pair = (node_id(a), node_id(b))
                if pair not in emitted:
                    emitted.add(pair)
                    edges.append({"source": pair[0], "target": pair[1]})

        truncated = len(edges) > limit
        return {
            "scope": "calls",
            "focus": name,
            "nodes": nodes,
            "edges": edges[:limit],
            "total_nodes": len(nodes),
            "truncated": truncated,
        }

    def run_cypher(self, query: str, **params):
        """Present for interface parity. The embedded backend has no Cypher."""
        raise NotImplementedError(
            "The embedded graph backend does not support Cypher. "
            "Set GRAPH_BACKEND=neo4j to use raw queries."
        )
