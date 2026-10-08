# EasyCode evaluation — miniapp

Repository: file:///Users/nishad/Projects/easy-code/backend/tests/fixtures/sample_repo — 13 files, 41 entities, 37 chunks, 41 graph nodes, 78 relationships
Embeddings: all-MiniLM-L6-v2 | LLM: ollama/qwen2.5:7b | context limit: 12

## Vector-only baseline vs graph + vector hybrid

| Metric | Vector-only | Hybrid | Delta |
| --- | ---: | ---: | ---: |
| recall_at_1 | 0.417 | 0.417 | 0.000 |
| recall_at_3 | 0.731 | 0.853 | +0.122 |
| recall_at_5 | 0.904 | 0.974 | +0.071 |
| recall_at_10 | 0.974 | 0.974 | 0.000 |
| precision_at_5 | 0.323 | 0.473 | +0.150 |
| mrr | 0.801 | 0.833 | +0.032 |
| relevant_file_rate | 1.000 | 1.000 | 0.000 |
| entity_coverage | 0.769 | 0.962 | +0.192 |
| mean_retrieval_seconds | 0.302 | 0.252 | -0.050 |
| mention_coverage | 0.654 | 0.885 | +0.231 |
| citation_accuracy | 0.846 | 1.000 | +0.154 |
| citation_coverage | 0.577 | 0.782 | +0.205 |
| hallucination_rate | 0.000 | 0.000 | 0.000 |
| mean_generation_seconds | 15.249 | 16.261 | +1.012 |

## vector — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 0.667 |
| flow | 2 | 0.875 | 1.000 | 1.000 | 1.000 |
| navigation | 2 | 0.667 | 0.625 | 1.000 | 1.000 |
| relationship | 4 | 0.917 | 0.833 | 1.000 | 0.250 |
| repository | 2 | 1.000 | 0.417 | 1.000 | 0.750 |

## hybrid — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 0.833 | 1.000 | 0.667 |
| flow | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| navigation | 2 | 0.833 | 1.000 | 1.000 | 1.000 |
| relationship | 4 | 1.000 | 0.875 | 1.000 | 1.000 |
| repository | 2 | 1.000 | 0.417 | 1.000 | 0.750 |
