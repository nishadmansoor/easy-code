# EasyCode architecture

## The idea

Most code-RAG systems embed source files and retrieve by similarity. That works
for "where is X" and fails for "what calls X", because similarity has no notion
of a call edge. EasyCode keeps a second index — an explicit graph of the
repository's structure — and uses each index for the questions it can actually
answer.

```text
                          GitHub repository
                                  │
                                  ▼
                       Repository ingestion
                    (clone, ignore rules, read)
                                  │
                                  ▼
                       AST parsing (per language)
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
             Semantic chunks            Entities + relationships
                    │                           │
                    ▼                           ▼
              Qdrant (vectors)            Neo4j (graph)
                    │                           │
                    └─────────────┬─────────────┘
                                  ▼
                       Hybrid retrieval
              (classify → retrieve → rank → dedupe)
                                  │
                                  ▼
                        Context assembly
                                  │
                                  ▼
                     LLM (local via Ollama)
                                  │
                                  ▼
                 Grounded answer + verified citations
```

## Ingestion

`backend/app/ingestion/` clones a repository into an isolated workspace under
`data/repos/<repository_id>/`.

Repositories are untrusted input. Ingestion **only reads files**. It never runs
repository scripts, installers, build commands or tests. URLs are validated
before use: only `http(s)` is accepted, and anything containing shell
metacharacters, a query string or a non-HTTP scheme is rejected, so a URL can
never reach a shell or address the local filesystem.

Files are excluded when they live in a dependency or build directory, match a
binary/generated/lockfile pattern, exceed `MAX_FILE_BYTES`, contain NUL bytes,
or match a simple `.gitignore` rule.

Status moves through `queued → cloning → parsing → generating_embeddings →
building_graph → indexing → ready`, or `failed` with the reason recorded.

## Parsing

`backend/app/parsing/` holds a registry mapping a language to a
`parse_<lang>_file(path, repo_id, rel_path) -> ParsedRepository` function.
Adding a language means writing that function and registering it; nothing else
in the pipeline is language-specific.

Python is parsed with the standard library `ast` module. JavaScript and
TypeScript (including `.jsx`/`.tsx`) are parsed with **tree-sitter**, since
Python ships no JS grammar; both produce the same `ParsedRepository` shape —
modules, classes, functions, methods, imports, calls and inheritance, along with
signatures, docstrings, decorators and exact line ranges. Markdown is split at
heading boundaries into documentation sections.

JavaScript resolution differs from Python's in one respect: only *relative*
specifiers (`./x`, `../x`) can name a repository file, and a specifier may omit
its extension or point at a directory's `index` file, so a candidate list is
tried in order (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, then `index.*`).
A bare specifier like `react` is a `node_modules` package and stays unresolved.
TypeScript's NodeNext convention of importing `./util.js` to mean `./util.ts` is
handled explicitly.

Every path stored anywhere is **relative to the repository root**, so citations
are stable and never leak the indexing machine's filesystem.

### Relationship resolution

Cross-file relationships are resolved in a pass over the whole repository:

- **Imports** — a dotted module maps to candidate paths (`a.b.c` →
  `a/b/c.py`, `a/b/c/__init__.py`), matched against the full path first and
  then a unique path suffix so `src/` layouts work. Relative imports resolve
  against the importing file's directory.
- **Calls** — a call target is matched against the definition index,
  preferring a definition in the same file, then one in a file the caller
  imports, then a globally unique definition.
- **Inheritance** — base class names resolve the same way.

**Ambiguity is resolved by giving up.** If a name has several plausible
definitions and none is clearly preferred, no edge is written. A missing
relationship is a gap; an invented one is a lie the LLM will repeat.

## Semantic index (Qdrant)

Chunks follow structural boundaries — one function, method, class or
documentation section per chunk — never a fixed character window. A class that
contains methods is indexed as an outline (declaration, docstring, method
signatures) because its method bodies are already indexed separately. Units
that exceed the size limit are split on line boundaries with line numbers
preserved.

Each chunk is embedded with a natural-language header (`"method login in
app/auth/service.py"`, plus signature and docstring) so that prose questions can
match code whose tokens are mostly identifiers.

Point IDs are a UUIDv5 of `repository_id | file_path | entity_type |
entity_name | line range`. Re-indexing overwrites rather than duplicating, and
two repositories can never collide. Payload indexes on `repository_id`,
`language`, `entity_type` and `file_path` make filtered search cheap.

## Storage modes

Both indexes have two interchangeable backends, chosen by `STORAGE_MODE`.

**`server`** (the default) uses Qdrant and Neo4j over the network, which is
what `docker compose` starts.

**`embedded`** needs no services at all. Qdrant's Python client runs the vector
index in-process from a local directory, and the code graph becomes one JSON
file per repository (`backend/app/graph/embedded_store.py`), queried with plain
dicts and `networkx` for shortest paths. Nothing above the store layer knows
which backend is in use, and the same integration suite passes against both.

Two constraints come with embedded mode. Embedded Qdrant locks its storage
directory to a single client, so the client is a process-wide singleton and the
API must run one worker. And every graph query is a scan over in-memory lists —
microseconds for a 1,000-file repository, the wrong choice for a 100,000-file
one.

## Structural index (Neo4j)

Nodes: `Repository`, `File`, `Class`, `Function`, `Method`.

Relationships: `REPOSITORY_CONTAINS_FILE`, `FILE_DEFINES_CLASS`,
`FILE_DEFINES_FUNCTION`, `FILE_DEFINES_METHOD`, `CLASS_CONTAINS_METHOD`,
`FILE_IMPORTS_FILE`, `FUNCTION_CALLS_FUNCTION`, `METHOD_CALLS_METHOD`,
`CLASS_INHERITS_CLASS`.

Writes are batched with `UNWIND`, so indexing a large repository is a handful of
round trips. The graph answers questions similarity cannot: callers, callees,
importers, dependency chains, subclass hierarchies, most-depended-on files and
likely entry points.

## Hybrid retrieval

`backend/app/retrieval/` implements the pipeline:

1. **Classification** — each question is `semantic`, `structural` or `mixed`.
   A structural intent (callers, importers, inheritance, definition, …) plus a
   concrete target makes a question structural. The same intent phrased vaguely,
   or combined with "explain"/"how does", makes it mixed. A structural question
   with no resolvable target falls back to semantic search rather than
   returning nothing.
2. **Retrieval** — semantic questions lean on Qdrant, structural on Neo4j,
   mixed on both. The vector budget scales with the question type.
3. **Graph expansion** — for mixed questions, the strongest semantic hits seed
   a one-hop graph traversal. This is the step that makes the system a hybrid
   rather than two retrievers run side by side: the vector index finds *where*
   to look, the graph explains how that code connects to the rest of the system.
4. **Ranking** — results are scored by a priority tier (direct hit → related
   callers/callees → dependencies → file → documentation → broad context)
   weighted by retrieval score. An item found by *both* retrievers is boosted,
   because agreement is evidence.
5. **Deduplication** — identical regions merge; a range fully contained in a
   higher-ranked one is dropped.
6. **Diversity** — a soft per-file cap keeps one large file from crowding out
   the repository, backfilling only if the budget would otherwise go unspent.
7. **Assembly** — the context is rendered with each snippet's exact citation,
   under a character budget.

## Generation

`LLMProvider` has three implementations: `OllamaProvider` (the local default),
`OpenAICompatibleProvider` (optional, credentials from the environment only)
and `ExtractiveProvider`, which lists retrieved locations without a model. If
the configured provider is unreachable, the system falls back to extractive
answers rather than failing.

### Hallucination prevention

Two layers:

1. **Prompt** — the model is told to use only the supplied evidence, to cite in
   `path:START-END` form, and to answer *"I couldn't find enough evidence in the
   indexed repository to determine this."* when the context is insufficient.
2. **Verification** — after generation, every citation in the answer is checked
   against what was actually retrieved. A citation is accepted only when its
   file was retrieved and its line range overlaps a retrieved range (±5 lines
   of slack for paraphrasing). Unsupported citations are stripped from the
   answer text, reported in `unverified_citations`, and flip `grounded` to
   false.

This makes hallucination *measurable*: the evaluation harness counts an answer
as hallucinated exactly when it cited a location that was never retrieved.

## Persistence

Repository metadata lives in SQLAlchemy — SQLite by default so the project runs
with no setup, PostgreSQL via `DATABASE_URL` for a deployment. Records survive
restarts.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/repositories` | Register and start background indexing (202) |
| `GET` | `/api/repositories` | List indexed repositories |
| `GET` | `/api/repositories/{id}` | Repository metadata |
| `GET` | `/api/repositories/{id}/status` | Poll indexing progress |
| `GET` | `/api/repositories/{id}/overview` | Generated overview |
| `DELETE` | `/api/repositories/{id}` | Remove from all stores |
| `POST` | `/api/repositories/{id}/query` | Ask a question (`hybrid` or `vector`) |
| `GET` | `/api/repositories/{id}/files` | Indexed file list |
| `GET` | `/api/repositories/{id}/file` | Read one file's source |
| `GET` | `/api/repositories/{id}/graph` | Graph statistics, central files, entry points |
| `GET` | `/api/repositories/{id}/graph/file` | Definitions in a file |
| `GET` | `/api/repositories/{id}/graph/class` | Methods, bases, subclasses |
| `GET` | `/api/repositories/{id}/graph/callers` | What calls a function |
| `GET` | `/api/repositories/{id}/graph/callees` | What a function calls |
| `GET` | `/api/repositories/{id}/graph/importers` | What imports a file |
| `GET` | `/api/repositories/{id}/graph/imports` | What a file imports |
| `GET` | `/api/repositories/{id}/graph/path` | Dependency chain between two files |
| `GET` | `/api/health` | Service and LLM availability |

Interactive documentation is at `/docs`.

## Frontend

A dependency-free single-page app in `frontend/`, served by the same FastAPI
process. Repository submission, live indexing progress, the overview, the Q&A
interface with provenance badges (`vector` / `graph` / `both`), a file browser
that jumps to a cited line range, and a graph explorer. No npm, no build step.
