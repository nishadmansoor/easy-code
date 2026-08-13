from neo4j import GraphDatabase

from backend.app.config.settings import settings


class GraphStore:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def close(self):
        self.driver.close()

    def clear_repository(self, repository_id: str):
        with self.driver.session() as session:
            session.run(
                "MATCH (n {repository_id: $repo_id}) DETACH DELETE n",
                repo_id=repository_id,
            )

    def create_file_node(self, repository_id: str, file_path: str, language: str):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (f:File {repository_id: $repo_id, file_path: $file_path})
                SET f.language = $language, f.node_type = 'File'
                """,
                repo_id=repository_id,
                file_path=file_path,
                language=language,
            )

    def create_repository_node(self, repository_id: str, url: str):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (r:Repository {repository_id: $repo_id})
                SET r.url = $url, r.node_type = 'Repository'
                """,
                repo_id=repository_id,
                url=url,
            )

    def create_class_node(
        self,
        repository_id: str,
        file_path: str,
        class_name: str,
        start_line: int,
        end_line: int,
    ):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (c:Class {
                    repository_id: $repo_id,
                    file_path: $file_path,
                    name: $name
                })
                SET c.start_line = $start_line,
                    c.end_line = $end_line,
                    c.node_type = 'Class'
                """,
                repo_id=repository_id,
                file_path=file_path,
                name=class_name,
                start_line=start_line,
                end_line=end_line,
            )

    def create_function_node(
        self,
        repository_id: str,
        file_path: str,
        function_name: str,
        start_line: int,
        end_line: int,
    ):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (f:Function {
                    repository_id: $repo_id,
                    file_path: $file_path,
                    name: $name
                })
                SET f.start_line = $start_line,
                    f.end_line = $end_line,
                    f.node_type = 'Function'
                """,
                repo_id=repository_id,
                file_path=file_path,
                name=function_name,
                start_line=start_line,
                end_line=end_line,
            )

    def create_method_node(
        self,
        repository_id: str,
        file_path: str,
        class_name: str,
        method_name: str,
        start_line: int,
        end_line: int,
    ):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (m:Method {
                    repository_id: $repo_id,
                    file_path: $file_path,
                    class_name: $class_name,
                    name: $method_name
                })
                SET m.start_line = $start_line,
                    m.end_line = $end_line,
                    m.node_type = 'Method'
                """,
                repo_id=repository_id,
                file_path=file_path,
                class_name=class_name,
                method_name=method_name,
                start_line=start_line,
                end_line=end_line,
            )

    def create_repository_contains_file(self, repository_id: str, file_path: str):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (r:Repository {repository_id: $repo_id})
                MATCH (f:File {repository_id: $repo_id, file_path: $file_path})
                MERGE (r)-[:REPOSITORY_CONTAINS_FILE]->(f)
                """,
                repo_id=repository_id,
                file_path=file_path,
            )

    def create_file_defines_class(self, repository_id: str, file_path: str, class_name: str):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:File {repository_id: $repo_id, file_path: $file_path})
                MATCH (c:Class {repository_id: $repo_id, file_path: $file_path, name: $name})
                MERGE (f)-[:FILE_DEFINES_CLASS]->(c)
                """,
                repo_id=repository_id,
                file_path=file_path,
                name=class_name,
            )

    def create_file_defines_function(self, repository_id: str, file_path: str, function_name: str):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:File {repository_id: $repo_id, file_path: $file_path})
                MATCH (fn:Function {repository_id: $repo_id, file_path: $file_path, name: $name})
                MERGE (f)-[:FILE_DEFINES_FUNCTION]->(fn)
                """,
                repo_id=repository_id,
                file_path=file_path,
                name=function_name,
            )

    def create_class_contains_method(
        self, repository_id: str, file_path: str, class_name: str, method_name: str
    ):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (c:Class {
                    repository_id: $repo_id, file_path: $file_path, name: $class_name
                })
                MATCH (m:Method {
                    repository_id: $repo_id, file_path: $file_path,
                    class_name: $class_name, name: $method_name
                })
                MERGE (c)-[:CLASS_CONTAINS_METHOD]->(m)
                """,
                repo_id=repository_id,
                file_path=file_path,
                class_name=class_name,
                method_name=method_name,
            )

    def create_file_imports_file(self, repository_id: str, source_file: str, target_module: str):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (sf:File {repository_id: $repo_id, file_path: $source_file})
                OPTIONAL MATCH (tf:File {
                    repository_id: $repo_id
                }) WHERE $target_module ENDS WITH tf.file_path
                OR tf.file_path ENDS WITH $target_module
                WITH sf, tf
                WHERE tf IS NOT NULL
                MERGE (sf)-[:FILE_IMPORTS_FILE]->(tf)
                """,
                repo_id=repository_id,
                source_file=source_file,
                target_module=target_module,
            )

    def get_file_contents(self, repository_id: str, file_path: str) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (f:File {repository_id: $repo_id, file_path: $file_path})
                OPTIONAL MATCH (f)-[:FILE_DEFINES_CLASS]->(c:Class)
                OPTIONAL MATCH (f)-[:FILE_DEFINES_FUNCTION]->(fn:Function)
                OPTIONAL MATCH (c)-[:CLASS_CONTAINS_METHOD]->(m:Method)
                RETURN f,
                       collect(DISTINCT c) AS classes,
                       collect(DISTINCT fn) AS functions,
                       collect(DISTINCT m) AS methods
                """,
                repo_id=repository_id,
                file_path=file_path,
            )
            record = result.single()
            if not record:
                return []
            return [dict(record)]

    def get_class_methods(self, repository_id: str, class_name: str) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (c:Class {repository_id: $repo_id, name: $class_name})
                OPTIONAL MATCH (c)-[:CLASS_CONTAINS_METHOD]->(m:Method)
                RETURN c AS class, collect(DISTINCT m) AS methods
                """,
                repo_id=repository_id,
                class_name=class_name,
            )
            return [dict(r) for r in result]

    def get_function_callers(self, repository_id: str, function_name: str) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (caller)-[:FUNCTION_CALLS_FUNCTION|METHOD_CALLS_METHOD]->(target {
                    repository_id: $repo_id, name: $name
                })
                RETURN caller.node_type AS caller_type,
                       caller.name AS caller_name,
                       caller.file_path AS caller_file,
                       caller.start_line AS caller_start,
                       caller.end_line AS caller_end
                """,
                repo_id=repository_id,
                name=function_name,
            )
            return [dict(r) for r in result]

    def get_importers(self, repository_id: str, file_path: str) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (importer:File {repository_id: $repo_id})-[:FILE_IMPORTS_FILE]->(
                    target:File {repository_id: $repo_id, file_path: $file_path}
                )
                RETURN importer.file_path AS file_path
                """,
                repo_id=repository_id,
                file_path=file_path,
            )
            return [dict(r) for r in result]

    def get_repository_overview(self, repository_id: str) -> dict:
        with self.driver.session() as session:
            files = session.run(
                "MATCH (f:File {repository_id: $repo_id}) "
                "RETURN f.file_path AS file_path, f.language AS language",
                repo_id=repository_id,
            )
            classes = session.run(
                "MATCH (c:Class {repository_id: $repo_id}) "
                "RETURN c.name AS name, c.file_path AS file_path",
                repo_id=repository_id,
            )
            functions = session.run(
                "MATCH (fn:Function {repository_id: $repo_id}) "
                "RETURN fn.name AS name, fn.file_path AS file_path",
                repo_id=repository_id,
            )
            return {
                "files": [dict(r) for r in files],
                "classes": [dict(r) for r in classes],
                "functions": [dict(r) for r in functions],
            }

    def run_cypher(self, query: str, **params) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [dict(r) for r in result]
