# Evaluation

Easy Code exists to answer one question:

> Does structural information from a code graph improve codebase understanding
> compared with semantic retrieval alone?

This document reports how that is measured and what the measurement says.

## Method

The experiment compares a **vector-only RAG baseline** against the **graph +
vector hybrid**. Both systems use the same index, the same 12-item context
budget, the same `all-MiniLM-L6-v2` embeddings and the same `qwen2.5:7b` model
via Ollama. The only difference is whether structural information is available
during retrieval, so any difference in the scores is attributable to the graph.

```bash
docker compose up -d qdrant neo4j
python scripts/run_evaluation.py --benchmark miniapp      # deterministic fixture
python scripts/run_evaluation.py --benchmark requests     # real repository
python scripts/run_evaluation.py --benchmark requests --no-generate   # retrieval only
```

## Benchmarks

Two, defined in `backend/app/evaluation/benchmark.py`:

- **`miniapp`** — a 13-file fixture at `backend/tests/fixtures/sample_repo`,
  written so the ground truth can be verified by hand. It needs no network, so
  it is deterministic and doubles as the unit-test fixture.
- **`requests`** — the real `psf/requests`: 121 files, 891 entities, 927 chunks,
  886 graph nodes and 2,194 relationships, indexed in 81 seconds. Ground truth
  was checked by hand against the source.

Ground truth is the set of files a careful engineer would agree are needed to
answer the question — deliberately conservative, so a high score cannot be
earned by retrieving everything. Questions span five categories: repository,
component, relationship, flow and navigation.

## Metrics

**Retrieval:** Recall@{1,3,5,10}, Precision@5, MRR, relevant-file rate, entity
coverage.

**Answers:** mention coverage, citation accuracy, citation coverage,
hallucination rate.

**Performance:** retrieval latency, generation latency, indexing time.

## Results

Both systems used the same index, the same 12-item context budget, the same
`all-MiniLM-L6-v2` embeddings and the same `qwen2.5:7b` model via Ollama.

**`psf/requests`** — 121 files, 891 entities, 927 chunks, 929 graph nodes,
2,286 relationships. 10 questions.

| Metric | Vector-only | Hybrid | Δ |
| --- | ---: | ---: | ---: |
| Recall@1 | 0.550 | **0.700** | +0.150 |
| Recall@3 | 0.850 | **0.900** | +0.050 |
| Recall@5 | 0.950 | 0.950 | 0.000 |
| Recall@10 | 0.950 | **1.000** | +0.050 |
| Precision@5 | 0.258 | **0.385** | +0.127 |
| MRR | 0.742 | **0.853** | +0.112 |
| Entity coverage | 0.583 | **0.767** | +0.183 |
| Mention coverage | 0.800 | **1.000** | +0.200 |
| Citation accuracy | 0.900 | **1.000** | +0.100 |
| Citation coverage | 0.850 | **0.900** | +0.050 |
| Hallucination rate | 0.000 | 0.000 | 0.000 |

**`miniapp`** (the deterministic fixture) — 13 files, 41 entities, 78
relationships. 13 questions.

| Metric | Vector-only | Hybrid | Δ |
| --- | ---: | ---: | ---: |
| Recall@3 | 0.731 | **0.853** | +0.122 |
| Recall@5 | 0.904 | **0.974** | +0.071 |
| Precision@5 | 0.323 | **0.473** | +0.150 |
| MRR | 0.801 | **0.833** | +0.032 |
| Entity coverage | 0.769 | **0.962** | +0.192 |
| Mention coverage | 0.654 | **0.885** | +0.231 |
| Citation coverage | 0.577 | **0.782** | +0.205 |
| Citation accuracy | 0.846 | **1.000** | +0.154 |
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

## Failure analysis

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

A fourth cause was a retrieval bug rather than a prompt one, and it produced
the opposite failure — abstaining when the answer was in hand. Asked *"What
imports `mods/diff/hooks/git/index.ts`?"* against `anthropics/claude-code`, the
system answered *"I couldn't find enough evidence"* on 5 runs out of 5, despite
the graph holding 80 importers and the fact appearing verbatim in the context.

The cause was a label. Graph results for import questions were tagged
`"imports this file"` — a dangling reference. Each retrieved item reaches the
model on its own, so "this file" pointed at nothing and the direction of the
edge was ambiguous. With the top semantic hits being modules *inside* that
directory, the model reconciled the two by deciding the facts meant something
else. Labels now name their target (`"imports mods/diff/hooks/git/index.ts"`),
matching what the caller and inheritance retrievers already did, and the
failure rate went to 0 out of 5.

Worth recording how it was found: a prompt change was tried first, declaring
structural facts authoritative. It changed nothing — still 5 out of 5. Only
dumping the assembled context showed the real cause. Reproducing the exact
failing case mattered too, because the same question shape against
`pallets/click` answered correctly every time.

One honest miss remains: on `requests`, the hybrid's single navigation question
(*"Where would I change the code to add a new retry policy?"*) scored 0.0 mention
coverage, against 1.0 for the baseline. It retrieved `adapters.py` and ranked it
first, then wrote an answer that never named the file. That is a generation
failure on a correct retrieval, and with n=1 it is as likely to be sampling noise
as a real regression.

## What these numbers do and do not support

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
