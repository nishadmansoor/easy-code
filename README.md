# EasyCode

**Paste a GitHub URL. Get a grounded explanation of the codebase, with citations
you can click.**

EasyCode combines semantic retrieval with a structural code graph to help you
build an accurate mental model of an unfamiliar repository. It runs entirely on
local models — no paid API key is required, ever.

---

## Why

Most code-RAG systems embed source files and retrieve by similarity. That
answers *"where is authentication handled?"* reasonably well and fails at
*"what calls this function?"*, because vector similarity has no notion of a call
edge.

EasyCode keeps two indexes and uses each for what it is actually good at:

| | Qdrant (semantic) | Neo4j (structural) |
| --- | --- | --- |
| Answers | "find code related to authentication" | "what calls `login`?" |
| Built from | embedded structural chunks | AST-extracted entities and relationships |
| Strength | conceptual similarity | exact, verifiable relationships |
| Weakness | cannot follow an edge | cannot match on meaning |

The research question this project exists to answer:

> **Does structural information from a code graph improve codebase understanding
> compared with semantic retrieval alone?**

The evaluation harness below measures exactly that, on the same index, with the
same context budget and the same model.

---

## Quick start

```bash
git clone <this-repo> && cd easy-code
cp .env.example .env
docker compose up -d qdrant neo4j
pip install -e ".[dev]"
uvicorn backend.app.api.main:app --reload
```

Open <http://localhost:8000>.

For written explanations rather than a list of locations, run a local model:

```bash
ollama pull qwen2.5:7b
```

Without Ollama the system still works: answers fall back to listing the exact
repository locations that matched, which is honest rather than broken.

Everything in Docker instead:

```bash
docker compose up --build
```

### Command line

```bash
python scripts/index_repository.py https://github.com/psf/requests --ask "What happens when you call requests.get?"
```

---

## Architecture

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

Full detail in [docs/architecture.md](docs/architecture.md).

### Qdrant: the semantic index

Chunks follow **structural boundaries** — one function, method, class or
documentation section — never a fixed character window. Each is embedded with a
natural-language header (`"method login in app/auth/service.py"` plus its
signature and docstring) so prose questions can match code whose tokens are
mostly identifiers. Point IDs are derived from the chunk's identity, so
re-indexing overwrites instead of duplicating.

### Neo4j: the structural index

```text
Repository
  └── REPOSITORY_CONTAINS_FILE ──▶ File
                                    ├── FILE_DEFINES_CLASS ──▶ Class
                                    │                            ├── CLASS_CONTAINS_METHOD ──▶ Method
                                    │                            └── CLASS_INHERITS_CLASS ──▶ Class
                                    ├── FILE_DEFINES_FUNCTION ──▶ Function
                                    │                              └── FUNCTION_CALLS_FUNCTION ──▶ Function
                                    └── FILE_IMPORTS_FILE ──▶ File
```

Relationships are written **only when static analysis resolves them**. If a name
has several plausible definitions and none is clearly preferred, no edge is
written. A missing relationship is a gap; an invented one is a lie the LLM will
repeat.

### Hybrid retrieval pipeline

1. **Classify** the question as `semantic`, `structural` or `mixed`, and extract
   the identifiers and file paths it names.
2. **Retrieve** — semantic leans on Qdrant, structural on Neo4j, mixed on both.
3. **Expand** — for mixed questions, the strongest semantic hits seed a one-hop
   graph traversal. *This is the step that makes it a hybrid rather than two
   retrievers side by side:* the vector index finds where to look, the graph
   explains how that code connects to the rest of the system.
4. **Rank** by priority tier (direct hit → callers/callees → dependencies →
   file → docs → broad context), boosting anything found by **both** retrievers.
5. **Deduplicate**, drop contained ranges, cap per file for diversity.
6. **Assemble** context under a character budget, each snippet carrying its
   exact citation.

---

## Hallucination prevention

Prompting is not enough, so citations are verified after generation. Every
`path:START-END` in the answer is checked against what was actually retrieved.
A citation survives only if its file was retrieved **and** its line range
overlaps a retrieved range. Unsupported citations are stripped from the answer,
returned in `unverified_citations`, and set `grounded: false`.

When the evidence is insufficient the system says so:

> I couldn't find enough evidence in the indexed repository to determine this.

This makes hallucination *measurable*: the evaluation counts an answer as
hallucinated exactly when it cited a location that was never retrieved.

---

## Example

```bash
curl -X POST http://localhost:8000/api/repositories/{id}/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "Which classes inherit from BaseAdapter?"}'
```

Classified `structural` — 0 vector hits, 5 graph hits — answered from the graph:

> HTTPAdapter inherits from BaseAdapter.
>
> Sources:
> - `src/requests/adapters.py:122-155`
>
> Caveat: The exact definition of HTTPAdapter is not provided in the retrieved
> code snippet, but it is clear that it is a subclass of BaseAdapter based on
> the structural facts.

More example questions:

- What does this repository do?
- How is this application structured?
- Where is cookie handling implemented?
- What happens when you call `requests.get`?
- Which classes inherit from `BaseAdapter`?
- What imports `src/requests/models.py`?
- Where would I change the code to add a new retry policy?

---

## Evaluation

The critical experiment compares a **vector-only RAG baseline** against the
**graph + vector hybrid** on the same index, the same context budget and the
same model. The only difference is whether structural information is available
during retrieval.

```bash
docker compose up -d qdrant neo4j
python scripts/run_evaluation.py --benchmark miniapp      # deterministic fixture
python scripts/run_evaluation.py --benchmark requests     # real repository
python scripts/run_evaluation.py --benchmark requests --no-generate   # retrieval only, no LLM
```

Benchmarks live in `backend/app/evaluation/benchmark.py`. Ground truth is the
set of files a careful engineer would agree are needed to answer the question —
deliberately conservative, so a high score cannot be earned by retrieving
everything. Questions span the five categories in AGENT.md §26: repository,
component, relationship, flow and navigation.

**Metrics.** Retrieval: Recall@{1,3,5,10}, Precision@5, MRR, relevant-file rate,
entity coverage. Answers: mention coverage, citation accuracy, citation
coverage, hallucination rate. Performance: retrieval and generation latency,
indexing time.

Reports are written to `docs/results/` as JSON and Markdown, including a
failure-case section.

### Results

Both systems used the same index, the same 12-item context budget, the same
`all-MiniLM-L6-v2` embeddings and the same `qwen2.5:7b` model via Ollama.

**`psf/requests`** — 121 files, 891 entities, 927 chunks, 886 graph nodes,
2,194 relationships, indexed in 81s. 10 questions.

| Metric | Vector-only | Hybrid | Δ |
| --- | ---: | ---: | ---: |
| Recall@1 | 0.550 | **0.700** | +0.150 |
| Recall@3 | 0.850 | **0.950** | +0.100 |
| Recall@5 | 0.950 | 0.950 | 0.000 |
| Precision@5 | 0.258 | **0.395** | +0.137 |
| MRR | 0.742 | **0.867** | +0.125 |
| Entity coverage | 0.583 | **0.767** | +0.183 |
| Citation accuracy | 0.900 | **1.000** | +0.100 |
| Hallucination rate | 0.000 | 0.000 | 0.000 |

**`miniapp`** (the deterministic fixture) — 13 files, 41 entities, 78
relationships. 13 questions.

| Metric | Vector-only | Hybrid | Δ |
| --- | ---: | ---: | ---: |
| Recall@3 | 0.731 | **0.776** | +0.045 |
| Recall@5 | 0.904 | **0.936** | +0.032 |
| Precision@5 | 0.323 | **0.458** | +0.135 |
| MRR | 0.801 | **0.810** | +0.009 |
| Entity coverage | 0.769 | **0.962** | +0.192 |
| Mention coverage | 0.846 | **0.962** | +0.115 |
| Citation coverage | 0.712 | **0.859** | +0.147 |
| Citation accuracy | 0.923 | **1.000** | +0.077 |
| Hallucination rate | 0.000 | 0.000 | 0.000 |

**The answer to the research question is yes, with a specific shape.** The graph
does not help the system find *more* relevant files — `relevant_file_rate` is
1.000 for both, and Recall@5 is a tie on `requests`. It helps it rank the right
file *first* (Recall@1 +0.150, MRR +0.125) and it sharply reduces the irrelevant
material sent to the model (Precision@5 +0.137). Retrieving less, better.

The clearest single case is *"Which classes inherit from `BaseAdapter`?"*. The
vector baseline retrieved exactly the right file, `src/requests/adapters.py`,
and still answered:

> No classes directly inherit from `BaseAdapter`.

That is wrong. Similarity put the right text in the context, but nothing in that
text states the edge, so the model concluded it did not exist. The hybrid reads
`CLASS_INHERITS_CLASS` from the graph and answers `HTTPAdapter`, correctly. This
is the failure mode the project was built to fix: **a vector index can retrieve
the right file and still not answer a question about structure.**

Both systems hallucinate at 0.000 on the final run. That number was not free —
see the failure analysis below.

### Failure analysis

The measurable definition of hallucination here is an answer citing a location
that was never retrieved. Three distinct causes showed up during development,
and all three were prompt-induced rather than retrieval failures:

1. **The model copied the worked example out of the system prompt.** An early
   prompt illustrated the citation format with `src/users/repository.py:20-78`.
   The model reproduced those paths and line numbers against an unrelated
   repository. Fixed by removing every copyable path and line number from the
   example.
2. **The model copied the format placeholder.** The rule said to cite as
   `path/to/file.py:START-END`, and one answer cited
   `path/to/src/requests/adapters.py:201-221` — prefix and all. Fixed by
   describing the format instead of showing a fake path.
3. **The model invented line ranges for files it had no snippet for.** Graph
   facts legitimately name files (`models.py is imported by adapters.py`) that
   may not be in the retrieved snippet set. Asked for a citation, the model
   guessed plausible whole-file ranges like `:1-409`. Fixed by instructing it to
   refer to such files by bare path with no line numbers.

The verifier caught all three before they reached the user, which is the point
of verifying rather than trusting the prompt. But they are a useful reminder
that a small local model treats prompt content as source material.

One honest miss remains: on `requests`, the hybrid's single navigation question
(*"Where would I change the code to add a new retry policy?"*) scored 0.0 mention
coverage, against 1.0 for the baseline. It retrieved `adapters.py` and ranked it
first, then wrote an answer that never named the file. That is a generation
failure on a correct retrieval, and with n=1 it is as likely to be sampling noise
as a real regression.

### What these numbers do and do not support

The retrieval metrics are deterministic and reproduce exactly across runs. The
answer-quality metrics do not: they come from a sampling 7B model over 10 and 13
questions, so a single question flipping moves a category score by 0.1–0.2.
Across four full runs during development, `mention_coverage` on `requests` moved
between 0.800 and 1.000 for the hybrid with no code change between some of them.

Treat the retrieval numbers as measured and the generation numbers as
directional. Mention coverage in particular is a keyword proxy — it asks whether
an answer names the components a correct answer should name, not whether the
explanation is any good. Judging that needs human raters, and this harness does
not pretend to.


---

## Project structure

```text
backend/
├── app/
│   ├── api/          FastAPI routes, schemas, service singletons
│   ├── config/       Settings (environment-driven)
│   ├── ingestion/    Cloning, ignore rules, the indexing pipeline
│   ├── parsing/      Parser registry, Python AST, Markdown, resolution passes
│   ├── chunking/     Structural semantic chunking
│   ├── embeddings/   EmbeddingModel interface + local implementation
│   ├── vector/       Qdrant store
│   ├── graph/        Neo4j store and graph builder
│   ├── retrieval/    Question analysis, vector, graph, hybrid, ranking
│   ├── generation/   LLM providers, prompts, grounded answers, overview
│   ├── evaluation/   Benchmarks, metrics, baseline-vs-hybrid runner
│   ├── storage/      Repository metadata persistence
│   └── models/       Shared data models
└── tests/            Unit tests + service-backed integration tests
frontend/             Dependency-free SPA served by FastAPI
scripts/              Indexing and evaluation CLIs
docs/                 Architecture notes and evaluation results
```

---

## Testing

```bash
pytest                              # unit tests only; no services needed
docker compose up -d qdrant neo4j
pytest -m integration               # real Qdrant + Neo4j
ruff check backend/ scripts/
```

Unit tests use a small local fixture repository (`backend/tests/fixtures/sample_repo`)
with hand-verified relationships, so results are deterministic and no external
repository is needed. Integration tests skip themselves when the services are
not running.

---

## Configuration

Every setting has a working default; see `.env.example`. The ones that matter
most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `ollama` | `ollama`, `openai`, or `extractive` |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local model for generation |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | Local embedding model |
| `DATABASE_URL` | `sqlite:///./data/easycode.db` | SQLite, or a PostgreSQL DSN |
| `RETRIEVAL_CONTEXT_LIMIT` | `12` | Evidence items sent to the LLM |
| `OPENAI_API_KEY` | *(empty)* | Optional; never required |

---

## Security

Repositories are untrusted input. EasyCode **only reads and parses files** — it
never runs repository scripts, installers, build commands or tests. Repository
URLs are validated before use: only `http(s)` is accepted, and anything with a
non-HTTP scheme, shell metacharacters or a query string is rejected. The
file-reading endpoint refuses any path that resolves outside the repository
workspace. API credentials are never hard-coded and come only from the
environment.

---

## Limitations

Known and worth stating plainly:

- **Python, JavaScript and TypeScript** have structural parsers. Java and
  everything else are detected and their documentation is indexed, but no
  entities or relationships are extracted for them, so the graph is empty for
  those files. Adding a language means writing one parser function and
  registering it; nothing downstream is language-specific.
- **Static call resolution is name-based.** Dynamic dispatch, monkey-patching,
  reflection and decorator-rewritten functions are not tracked. When a name is
  ambiguous, no edge is written — recall is traded for precision.
- **Type inference is absent.** `obj.method()` resolves on the method name, not
  on the inferred type of `obj`, so an unusual name shared by several classes
  may go unresolved.
- **Answer quality is bounded by the local model.** A 7B model follows the
  citation format less reliably than a frontier model; when it omits citations
  entirely, EasyCode appends the retrieved locations rather than presenting an
  uncited answer.
- **Automatic answer metrics are proxies.** Mention coverage is not correctness.
- **Cross-repository questions** are not supported; each repository is indexed
  and queried in isolation.
- **Large repositories** are limited by indexing time, which is dominated by
  embedding generation on CPU.

---

## Development phases

All seven phases from `AGENT.md` are implemented:

1. **Repository understanding** — ingestion, ignore rules, Python parsing,
   semantic chunking, local embeddings, Qdrant, vector search.
2. **Code graph** — Neo4j nodes and relationships including calls and
   inheritance.
3. **Hybrid retrieval** — question classification, both retrievers, graph
   expansion, ranking, deduplication.
4. **LLM explanation** — provider abstraction, prompts, grounded answers,
   verified citations, uncertainty handling.
5. **Product interface** — submission, live status, overview, Q&A, source
   navigation, graph explorer.
6. **Evaluation** — benchmarks, metrics, vector-only baseline vs hybrid,
   failure analysis.
7. **Production polish** — Docker, logging, error handling, persistence,
   configuration, documentation.
