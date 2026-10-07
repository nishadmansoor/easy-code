# Easy Code

A platform to help users understand complex repositories. Paste a GitHub repository URL and get grounded explanations about the code.

You give it a GitHub URL. It clones the repo, parses the source with AST and tree-sitter, extracts classes/functions/methods/imports/calls, chunks them into semantic units, and stores everything in Qdrant (vectors) and Neo4j (graph). You then ask questions in plain English and get an explanation back, with file paths and line numbers you can click through to the source.

Everything runs locally. No paid API key is needed.

## Why two databases

Most code search embeds files and retrieves by similarity. That answers "where is authentication handled?" and fails at "what calls this function?", because vector similarity has no notion of a call edge.

So Easy Code keeps two indexes and uses each for what it is good at. Qdrant finds code that is conceptually related to your question. Neo4j knows what actually calls what, what imports what, and what inherits from what. Questions are classified as semantic, structural or mixed, and routed accordingly.

The difference is measurable. Asked "Which classes inherit from `BaseAdapter`?" against `psf/requests`, vector-only search retrieved the correct file and still answered *"No classes directly inherit from BaseAdapter"* — the right text was in context, but nothing in it stated the edge. Reading the edge from the graph gives the correct answer, `HTTPAdapter`.

## Example

Ask "How does HTML escaping work?" and you get back the `escape` function, `_escape_inner`, and the `Markup` class, ranked by relevance, with an explanation of how they fit together and citations like `src/markupsafe/__init__.py:42-78`.

Ask "What calls `resolve_redirects`?" and it answers from the graph instead of guessing from surrounding text.

## Quick start

No databases to install and nothing to run alongside it:

```bash
pip install -e ".[dev]"
STORAGE_MODE=embedded uvicorn backend.app.api.main:app
```

Open <http://localhost:8000>, paste a repository URL, wait for indexing, then ask questions.

Embedded mode runs Qdrant in-process from a local directory and keeps the code
graph as a JSON file per repository, so there is no Docker and no server to
manage. It locks its storage to one process, so run a single worker. For larger
repositories, or to run more than one worker, use the real databases instead:

```bash
cp .env.example .env
docker compose up -d qdrant neo4j
uvicorn backend.app.api.main:app --reload
```

Both modes behave identically — the same test suite passes against each.

For written explanations rather than a bare list of matching locations, run a local model:

```bash
ollama pull qwen2.5:7b
```

Without Ollama it still works — answers fall back to listing the exact repository locations that matched, which is honest rather than broken.

To run the whole thing in Docker instead:

```bash
docker compose up --build
```

From the command line:

```bash
python scripts/index_repository.py https://github.com/psf/requests --ask "What happens when you call requests.get?"
```

## How it works

```text
GitHub URL → clone → parse → chunk ─┬─→ Qdrant (vectors)
                                    └─→ Neo4j (graph)
                                            │
             question → classify → retrieve from one or both
                                            │
                        rank → deduplicate → assemble context
                                            │
                                    local LLM → answer + citations
```

**Parsing.** Python via the standard library `ast` module, JavaScript and TypeScript via tree-sitter. Both produce the same shape: modules, classes, functions, methods, imports, calls, inheritance, with exact line ranges. Other languages are detected and their documentation is indexed, but no structure is extracted for them. Adding a language means writing one parser function and registering it.

**Chunking.** Chunks follow structural boundaries — one function, method, class or documentation section — never a fixed character window.

**Retrieval.** For mixed questions the strongest semantic hits seed a graph traversal, so the vector index finds where to look and the graph explains how that code connects to everything else. Results are ranked by relevance tier, deduplicated, and capped so one large file cannot crowd out the repository.

**Grounding.** Every citation the model produces is checked against what was actually retrieved. A citation survives only if its file was retrieved and its line range overlaps a retrieved range; anything else is stripped from the answer and reported. If the evidence is thin, the answer says so rather than inventing something.

Full detail in [docs/architecture.md](docs/architecture.md).

## Interface

The web UI has four views: a generated overview of the repository, a question box with a hybrid/vector toggle, a file browser that jumps to cited line ranges, and an interactive code graph you can drag, zoom and click through. There is also a REST API — see `/docs` for the full schema.

## Evaluation

Easy Code ships a benchmark that compares vector-only retrieval against the graph + vector hybrid on the same index, the same context budget and the same model.

```bash
python scripts/run_evaluation.py --benchmark requests
```

On `psf/requests` the hybrid improves Recall@1 by 0.15, MRR by 0.13 and Precision@5 by 0.14 over the baseline, with no fabricated citations from either system. The graph does not find *more* relevant files — it ranks the right one first and sends less irrelevant material to the model.

Methodology, per-category results and failure analysis are in [docs/evaluation.md](docs/evaluation.md). Generated reports land in `docs/results/`.

## Testing

```bash
pytest                                   # unit tests, no services needed
docker compose up -d qdrant neo4j
pytest -m integration                    # against real Qdrant and Neo4j
```

## Limitations

- Structural parsing covers Python, JavaScript and TypeScript. Java and everything else get file listing and documentation search, but an empty graph.
- Call resolution is name-based. Dynamic dispatch, monkey-patching and reflection are not tracked, and when a name is ambiguous no edge is written — recall is traded for precision, because an invented relationship is worse than a missing one.
- Answer quality is bounded by the local model. A 7B model follows the citation format less reliably than a frontier model.
- Repositories are indexed and queried in isolation; there are no cross-repository questions.
- Indexing time is dominated by embedding generation on CPU.

## Security

Repositories are untrusted input. Easy Code only reads and parses files — it never runs repository scripts, installers, build commands or tests. Repository URLs are validated before use, and the file-reading endpoint refuses any path resolving outside the repository workspace.
