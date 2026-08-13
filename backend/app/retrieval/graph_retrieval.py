from backend.app.graph.store import GraphStore


def get_file_graph_context(
    repository_id: str,
    file_path: str,
    graph_store: GraphStore,
) -> list[dict]:
    return graph_store.get_file_contents(repository_id=repository_id, file_path=file_path)


def get_class_graph_context(
    repository_id: str,
    class_name: str,
    graph_store: GraphStore,
) -> list[dict]:
    return graph_store.get_class_methods(repository_id=repository_id, class_name=class_name)


def get_caller_graph_context(
    repository_id: str,
    function_name: str,
    graph_store: GraphStore,
) -> list[dict]:
    return graph_store.get_function_callers(
        repository_id=repository_id, function_name=function_name
    )


def get_importer_graph_context(
    repository_id: str,
    file_path: str,
    graph_store: GraphStore,
) -> list[dict]:
    return graph_store.get_importers(repository_id=repository_id, file_path=file_path)
