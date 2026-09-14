"""Neo4j-backed structural index.

The graph exists for its relationships: what defines what, what imports what,
what calls what, what inherits from what. Writes are batched with ``UNWIND`` so
indexing a large repository is a handful of round trips rather than thousands.
"""

import logging

from neo4j import GraphDatabase

from backend.app.config.settings import settings

logger = logging.getLogger(__name__)

CONSTRAINTS = [
    "CREATE CONSTRAINT repo_key IF NOT EXISTS "
    "FOR (r:Repository) REQUIRE r.repository_id IS UNIQUE",
    "CREATE INDEX file_key IF NOT EXISTS FOR (f:File) ON (f.repository_id, f.file_path)",
    "CREATE INDEX class_key IF NOT EXISTS FOR (c:Class) ON (c.repository_id, c.name)",
    "CREATE INDEX function_key IF NOT EXISTS FOR (f:Function) ON (f.repository_id, f.name)",
    "CREATE INDEX method_key IF NOT EXISTS FOR (m:Method) ON (m.repository_id, m.name)",
]


class GraphStore:
    def __init__(self, driver=None):
        self.driver = driver or GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        self.ensure_schema()

    def close(self) -> None:
        self.driver.close()

    def ensure_schema(self) -> None:
        with self.driver.session() as session:
            for statement in CONSTRAINTS:
                try:
                    session.run(statement)
                except Exception:  # pragma: no cover - older server versions
                    logger.debug("Could not apply schema statement: %s", statement)

    def _write(self, query: str, **params) -> None:
        with self.driver.session() as session:
            session.run(query, **params).consume()

    def _read(self, query: str, **params) -> list[dict]:
        with self.driver.session() as session:
            return [dict(record) for record in session.run(query, **params)]

    # ----------------------------------------------------------------- write

    def clear_repository(self, repository_id: str) -> None:
        # Deleted in batches so a large repository does not build one huge
        # transaction. This subquery form works on Neo4j 5.x generally.
        while True:
            deleted = self._read(
                """
                MATCH (n {repository_id: $repo_id})
                WITH n LIMIT 5000
                DETACH DELETE n
                RETURN count(*) AS deleted
                """,
                repo_id=repository_id,
            )
            if not deleted or deleted[0]["deleted"] == 0:
                break

    def create_repository_node(self, repository_id: str, url: str, name: str = "") -> None:
        self._write(
            """
            MERGE (r:Repository {repository_id: $repo_id})
            SET r.url = $url, r.name = $name, r.node_type = 'Repository'
            """,
            repo_id=repository_id,
            url=url,
            name=name,
        )

    def create_files(self, repository_id: str, files: list[dict]) -> None:
        """files: [{file_path, language}]"""
        if not files:
            return
        self._write(
            """
            MATCH (r:Repository {repository_id: $repo_id})
            UNWIND $files AS row
            MERGE (f:File {repository_id: $repo_id, file_path: row.file_path})
            SET f.language = row.language, f.node_type = 'File'
            MERGE (r)-[:REPOSITORY_CONTAINS_FILE]->(f)
            """,
            repo_id=repository_id,
            files=files,
        )

    def create_classes(self, repository_id: str, classes: list[dict]) -> None:
        """classes: [{file_path, name, start_line, end_line, docstring, base_classes}]"""
        if not classes:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (f:File {repository_id: $repo_id, file_path: row.file_path})
            MERGE (c:Class {repository_id: $repo_id, file_path: row.file_path, name: row.name})
            SET c.start_line = row.start_line,
                c.end_line = row.end_line,
                c.docstring = row.docstring,
                c.base_classes = row.base_classes,
                c.node_type = 'Class'
            MERGE (f)-[:FILE_DEFINES_CLASS]->(c)
            """,
            repo_id=repository_id,
            rows=classes,
        )

    def create_functions(self, repository_id: str, functions: list[dict]) -> None:
        if not functions:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (f:File {repository_id: $repo_id, file_path: row.file_path})
            MERGE (fn:Function {repository_id: $repo_id, file_path: row.file_path, name: row.name})
            SET fn.start_line = row.start_line,
                fn.end_line = row.end_line,
                fn.signature = row.signature,
                fn.docstring = row.docstring,
                fn.node_type = 'Function'
            MERGE (f)-[:FILE_DEFINES_FUNCTION]->(fn)
            """,
            repo_id=repository_id,
            rows=functions,
        )

    def create_methods(self, repository_id: str, methods: list[dict]) -> None:
        if not methods:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (f:File {repository_id: $repo_id, file_path: row.file_path})
            MERGE (m:Method {
                repository_id: $repo_id, file_path: row.file_path,
                class_name: row.class_name, name: row.name
            })
            SET m.start_line = row.start_line,
                m.end_line = row.end_line,
                m.signature = row.signature,
                m.docstring = row.docstring,
                m.node_type = 'Method'
            MERGE (f)-[:FILE_DEFINES_METHOD]->(m)
            WITH m, row
            MATCH (c:Class {
                repository_id: $repo_id, file_path: row.file_path, name: row.class_name
            })
            MERGE (c)-[:CLASS_CONTAINS_METHOD]->(m)
            """,
            repo_id=repository_id,
            rows=methods,
        )

    def create_imports(self, repository_id: str, imports: list[dict]) -> None:
        """imports: [{source_file, target_file, module, line}] — resolved only."""
        if not imports:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (src:File {repository_id: $repo_id, file_path: row.source_file})
            MATCH (dst:File {repository_id: $repo_id, file_path: row.target_file})
            MERGE (src)-[i:FILE_IMPORTS_FILE]->(dst)
            SET i.module = row.module, i.line = row.line
            """,
            repo_id=repository_id,
            rows=imports,
        )

    def create_calls(self, repository_id: str, calls: list[dict]) -> None:
        """calls: [{caller_file, caller_name, caller_type, callee_file, callee_name,
        callee_type, line}] — resolved only."""
        if not calls:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (caller {repository_id: $repo_id, file_path: row.caller_file,
                           name: row.caller_name})
            WHERE caller.node_type IN ['Function', 'Method']
            MATCH (callee {repository_id: $repo_id, file_path: row.callee_file,
                           name: row.callee_name})
            WHERE callee.node_type IN ['Function', 'Method']
            WITH caller, callee, row
            LIMIT 1000000
            FOREACH (_ IN CASE WHEN caller.node_type = 'Method'
                                AND callee.node_type = 'Method' THEN [1] ELSE [] END |
                MERGE (caller)-[r:METHOD_CALLS_METHOD]->(callee) SET r.line = row.line)
            FOREACH (_ IN CASE WHEN caller.node_type <> 'Method'
                                OR callee.node_type <> 'Method' THEN [1] ELSE [] END |
                MERGE (caller)-[r:FUNCTION_CALLS_FUNCTION]->(callee) SET r.line = row.line)
            """,
            repo_id=repository_id,
            rows=calls,
        )

    def create_inheritance(self, repository_id: str, edges: list[dict]) -> None:
        """edges: [{child_file, child_class, parent_file, parent_class}] — resolved only."""
        if not edges:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (child:Class {repository_id: $repo_id, file_path: row.child_file,
                                name: row.child_class})
            MATCH (parent:Class {repository_id: $repo_id, file_path: row.parent_file,
                                 name: row.parent_class})
            MERGE (child)-[:CLASS_INHERITS_CLASS]->(parent)
            """,
            repo_id=repository_id,
            rows=edges,
        )

    # ------------------------------------------------------------------ read

    def get_repository_overview(self, repository_id: str) -> dict:
        rows = self._read(
            """
            MATCH (f:File {repository_id: $repo_id})
            OPTIONAL MATCH (f)-[:FILE_DEFINES_CLASS]->(c:Class)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_FUNCTION]->(fn:Function)
            RETURN f.file_path AS file_path,
                   f.language AS language,
                   collect(DISTINCT c.name) AS classes,
                   collect(DISTINCT fn.name) AS functions
            ORDER BY file_path
            """,
            repo_id=repository_id,
        )
        return {"files": rows}

    def get_statistics(self, repository_id: str) -> dict:
        rows = self._read(
            """
            MATCH (n {repository_id: $repo_id})
            WITH n.node_type AS type, count(*) AS count
            RETURN type, count
            """,
            repo_id=repository_id,
        )
        counts = {row["type"]: row["count"] for row in rows if row["type"]}
        relationships = self._read(
            """
            MATCH (a {repository_id: $repo_id})-[r]->(b {repository_id: $repo_id})
            RETURN type(r) AS type, count(*) AS count
            """,
            repo_id=repository_id,
        )
        return {
            "nodes": counts,
            "node_count": sum(counts.values()),
            "relationships": {row["type"]: row["count"] for row in relationships},
            "relationship_count": sum(row["count"] for row in relationships),
        }

    def get_files(self, repository_id: str) -> list[dict]:
        return self._read(
            """
            MATCH (f:File {repository_id: $repo_id})
            RETURN f.file_path AS file_path, f.language AS language
            ORDER BY file_path
            """,
            repo_id=repository_id,
        )

    def get_file_contents(self, repository_id: str, file_path: str) -> dict | None:
        rows = self._read(
            """
            MATCH (f:File {repository_id: $repo_id, file_path: $file_path})
            OPTIONAL MATCH (f)-[:FILE_DEFINES_CLASS]->(c:Class)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_FUNCTION]->(fn:Function)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_METHOD]->(m:Method)
            RETURN f.file_path AS file_path,
                   f.language AS language,
                   collect(DISTINCT {
                       name: c.name, start_line: c.start_line, end_line: c.end_line,
                       file_path: c.file_path
                   }) AS classes,
                   collect(DISTINCT {
                       name: fn.name, start_line: fn.start_line, end_line: fn.end_line,
                       file_path: fn.file_path, signature: fn.signature
                   }) AS functions,
                   collect(DISTINCT {
                       name: m.name, class_name: m.class_name, start_line: m.start_line,
                       end_line: m.end_line, file_path: m.file_path, signature: m.signature
                   }) AS methods
            """,
            repo_id=repository_id,
            file_path=file_path,
        )
        if not rows:
            return None
        record = rows[0]
        for key in ("classes", "functions", "methods"):
            record[key] = [item for item in record[key] if item.get("name")]
        return record

    def get_class_info(self, repository_id: str, class_name: str) -> list[dict]:
        return self._read(
            """
            MATCH (c:Class {repository_id: $repo_id, name: $class_name})
            OPTIONAL MATCH (c)-[:CLASS_CONTAINS_METHOD]->(m:Method)
            OPTIONAL MATCH (c)-[:CLASS_INHERITS_CLASS]->(parent:Class)
            OPTIONAL MATCH (child:Class)-[:CLASS_INHERITS_CLASS]->(c)
            RETURN c.name AS class_name,
                   c.file_path AS file_path,
                   c.start_line AS start_line,
                   c.end_line AS end_line,
                   collect(DISTINCT {
                       name: m.name, class_name: m.class_name, file_path: m.file_path,
                       start_line: m.start_line, end_line: m.end_line, signature: m.signature
                   }) AS methods,
                   collect(DISTINCT {
                       name: parent.name, file_path: parent.file_path,
                       start_line: parent.start_line, end_line: parent.end_line
                   }) AS bases,
                   collect(DISTINCT {
                       name: child.name, file_path: child.file_path,
                       start_line: child.start_line, end_line: child.end_line
                   }) AS subclasses
            """,
            repo_id=repository_id,
            class_name=class_name,
        )

    def get_function_callers(self, repository_id: str, function_name: str) -> list[dict]:
        return self._read(
            """
            MATCH (caller)-[:FUNCTION_CALLS_FUNCTION|METHOD_CALLS_METHOD]->
                  (target {repository_id: $repo_id, name: $name})
            WHERE caller.repository_id = $repo_id
            RETURN DISTINCT caller.node_type AS caller_type,
                   caller.name AS caller_name,
                   caller.file_path AS caller_file,
                   caller.start_line AS caller_start,
                   caller.end_line AS caller_end,
                   target.file_path AS target_file,
                   target.start_line AS target_start,
                   target.end_line AS target_end
            ORDER BY caller_file, caller_start
            """,
            repo_id=repository_id,
            name=function_name,
        )

    def get_function_callees(self, repository_id: str, function_name: str) -> list[dict]:
        return self._read(
            """
            MATCH (source {repository_id: $repo_id, name: $name})
                  -[:FUNCTION_CALLS_FUNCTION|METHOD_CALLS_METHOD]->(callee)
            WHERE callee.repository_id = $repo_id
            RETURN DISTINCT callee.node_type AS callee_type,
                   callee.name AS callee_name,
                   callee.file_path AS callee_file,
                   callee.start_line AS callee_start,
                   callee.end_line AS callee_end
            ORDER BY callee_file, callee_start
            """,
            repo_id=repository_id,
            name=function_name,
        )

    def get_importers(self, repository_id: str, file_path: str) -> list[dict]:
        return self._read(
            """
            MATCH (importer:File {repository_id: $repo_id})
                  -[:FILE_IMPORTS_FILE]->(:File {repository_id: $repo_id, file_path: $file_path})
            RETURN DISTINCT importer.file_path AS file_path
            ORDER BY file_path
            """,
            repo_id=repository_id,
            file_path=file_path,
        )

    def get_imported_files(self, repository_id: str, file_path: str) -> list[dict]:
        return self._read(
            """
            MATCH (:File {repository_id: $repo_id, file_path: $file_path})
                  -[:FILE_IMPORTS_FILE]->(target:File {repository_id: $repo_id})
            RETURN DISTINCT target.file_path AS file_path
            ORDER BY file_path
            """,
            repo_id=repository_id,
            file_path=file_path,
        )

    def get_file_network(self, repository_id: str, limit: int = 150) -> dict:
        """The file-level import graph, for visualisation.

        Files are ranked by degree so that a repository too large to draw is
        reduced to its most connected core rather than truncated arbitrarily.
        Isolated files (no imports either way) are excluded — they carry no
        information in a node-link diagram.
        """
        nodes = self._read(
            """
            MATCH (f:File {repository_id: $repo_id})
            OPTIONAL MATCH (f)-[:FILE_IMPORTS_FILE]->(out:File {repository_id: $repo_id})
            OPTIONAL MATCH (inc:File {repository_id: $repo_id})-[:FILE_IMPORTS_FILE]->(f)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_CLASS|FILE_DEFINES_FUNCTION]->(d)
            WITH f,
                 count(DISTINCT out) AS imports,
                 count(DISTINCT inc) AS importers,
                 count(DISTINCT d) AS definitions
            WHERE imports > 0 OR importers > 0
            RETURN f.file_path AS id,
                   f.language AS language,
                   imports, importers, definitions
            ORDER BY importers + imports DESC, id
            LIMIT $limit
            """,
            repo_id=repository_id,
            limit=limit,
        )

        total = self._read(
            """
            MATCH (f:File {repository_id: $repo_id})
            WHERE (f)-[:FILE_IMPORTS_FILE]-(:File {repository_id: $repo_id})
            RETURN count(DISTINCT f) AS total
            """,
            repo_id=repository_id,
        )
        total_nodes = total[0]["total"] if total else len(nodes)

        paths = [node["id"] for node in nodes]
        edges = (
            self._read(
                """
                MATCH (a:File {repository_id: $repo_id})
                      -[:FILE_IMPORTS_FILE]->(b:File {repository_id: $repo_id})
                WHERE a.file_path IN $paths AND b.file_path IN $paths
                RETURN DISTINCT a.file_path AS source, b.file_path AS target
                """,
                repo_id=repository_id,
                paths=paths,
            )
            if paths
            else []
        )

        return {
            "scope": "files",
            "nodes": nodes,
            "edges": edges,
            "total_nodes": total_nodes,
            "truncated": total_nodes > len(nodes),
        }

    def get_call_network(
        self, repository_id: str, name: str, depth: int = 2, limit: int = 200
    ) -> dict:
        """The call graph around one function or method, in both directions."""
        depth = max(1, min(int(depth), 3))
        rows = self._read(
            f"""
            MATCH (start {{repository_id: $repo_id, name: $name}})
            WHERE start.node_type IN ['Function', 'Method']
            MATCH path = (start)
                  -[:FUNCTION_CALLS_FUNCTION|METHOD_CALLS_METHOD*1..{depth}]-(other)
            WHERE other.repository_id = $repo_id
            UNWIND relationships(path) AS r
            WITH DISTINCT startNode(r) AS a, endNode(r) AS b
            RETURN a.name AS source_name, a.file_path AS source_file,
                   a.node_type AS source_type, a.start_line AS source_start,
                   a.end_line AS source_end,
                   b.name AS target_name, b.file_path AS target_file,
                   b.node_type AS target_type, b.start_line AS target_start,
                   b.end_line AS target_end
            LIMIT $limit
            """,
            repo_id=repository_id,
            name=name,
            limit=limit,
        )

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for row in rows:
            for side in ("source", "target"):
                key = f"{row[f'{side}_file']}::{row[f'{side}_name']}"
                nodes.setdefault(
                    key,
                    {
                        "id": key,
                        "label": row[f"{side}_name"],
                        "file_path": row[f"{side}_file"],
                        "entity_type": (row[f"{side}_type"] or "").lower(),
                        "start_line": row[f"{side}_start"] or 0,
                        "end_line": row[f"{side}_end"] or 0,
                        "focus": row[f"{side}_name"] == name,
                    },
                )
            edges.append(
                {
                    "source": f"{row['source_file']}::{row['source_name']}",
                    "target": f"{row['target_file']}::{row['target_name']}",
                }
            )

        return {
            "scope": "calls",
            "focus": name,
            "nodes": list(nodes.values()),
            "edges": edges,
            "total_nodes": len(nodes),
            "truncated": len(rows) >= limit,
        }

    def find_entities(self, repository_id: str, name: str, limit: int = 10) -> list[dict]:
        """Exact-name lookup across classes, functions and methods."""
        return self._read(
            """
            MATCH (n {repository_id: $repo_id, name: $name})
            WHERE n.node_type IN ['Class', 'Function', 'Method']
            RETURN n.node_type AS entity_type,
                   n.name AS name,
                   n.file_path AS file_path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.signature AS signature,
                   n.docstring AS docstring,
                   n.class_name AS class_name
            LIMIT $limit
            """,
            repo_id=repository_id,
            name=name,
            limit=limit,
        )

    def get_entity_neighborhood(
        self, repository_id: str, name: str, limit: int = 15
    ) -> list[dict]:
        """One-hop structural context around a named entity, in both directions."""
        return self._read(
            """
            MATCH (n {repository_id: $repo_id, name: $name})
            WHERE n.node_type IN ['Class', 'Function', 'Method']
            MATCH (n)-[r]-(other)
            WHERE other.repository_id = $repo_id
            RETURN DISTINCT type(r) AS relationship,
                   startNode(r) = n AS outgoing,
                   n.node_type AS source_type,
                   n.name AS source_name,
                   n.file_path AS source_file,
                   coalesce(other.node_type, 'File') AS other_type,
                   coalesce(other.name, other.file_path) AS other_name,
                   other.file_path AS other_file,
                   other.start_line AS other_start,
                   other.end_line AS other_end
            LIMIT $limit
            """,
            repo_id=repository_id,
            name=name,
            limit=limit,
        )

    def get_dependency_chain(
        self, repository_id: str, source_file: str, target_file: str, max_depth: int = 6
    ) -> list[dict]:
        return self._read(
            f"""
            MATCH path = shortestPath(
                (a:File {{repository_id: $repo_id, file_path: $source}})
                -[:FILE_IMPORTS_FILE*1..{int(max_depth)}]->
                (b:File {{repository_id: $repo_id, file_path: $target}})
            )
            RETURN [node IN nodes(path) | node.file_path] AS chain
            """,
            repo_id=repository_id,
            source=source_file,
            target=target_file,
        )

    def get_entry_points(self, repository_id: str, limit: int = 10) -> list[dict]:
        """Files nothing else imports, which are usually where execution starts."""
        return self._read(
            """
            MATCH (f:File {repository_id: $repo_id})
            WHERE f.language = 'python'
              AND NOT (:File {repository_id: $repo_id})-[:FILE_IMPORTS_FILE]->(f)
            OPTIONAL MATCH (f)-[:FILE_IMPORTS_FILE]->(dep:File)
            WITH f, count(dep) AS dependencies
            WHERE dependencies > 0
            RETURN f.file_path AS file_path, dependencies
            ORDER BY dependencies DESC, file_path
            LIMIT $limit
            """,
            repo_id=repository_id,
            limit=limit,
        )

    def get_central_files(self, repository_id: str, limit: int = 10) -> list[dict]:
        """Most-imported files: the modules the rest of the codebase leans on."""
        return self._read(
            """
            MATCH (importer:File {repository_id: $repo_id})
                  -[:FILE_IMPORTS_FILE]->(f:File {repository_id: $repo_id})
            WITH f, count(DISTINCT importer) AS importers
            RETURN f.file_path AS file_path, importers
            ORDER BY importers DESC, file_path
            LIMIT $limit
            """,
            repo_id=repository_id,
            limit=limit,
        )

    def run_cypher(self, query: str, **params) -> list[dict]:
        return self._read(query, **params)
