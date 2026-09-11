# EasyCode evaluation — miniapp

Repository: file:///Users/nishad/Projects/easy-code/backend/tests/fixtures/sample_repo — 13 files, 41 entities, 37 chunks, 41 graph nodes, 78 relationships
Embeddings: all-MiniLM-L6-v2 | LLM: ollama/qwen2.5:7b | context limit: 12

## Vector-only baseline vs graph + vector hybrid

| Metric | Vector-only | Hybrid | Delta |
| --- | ---: | ---: | ---: |
| recall_at_1 | 0.417 | 0.417 | 0.000 |
| recall_at_3 | 0.731 | 0.776 | +0.045 |
| recall_at_5 | 0.904 | 0.936 | +0.032 |
| recall_at_10 | 0.974 | 0.974 | 0.000 |
| precision_at_5 | 0.323 | 0.458 | +0.135 |
| mrr | 0.801 | 0.810 | +0.009 |
| relevant_file_rate | 1.000 | 1.000 | 0.000 |
| entity_coverage | 0.769 | 0.962 | +0.192 |
| mean_retrieval_seconds | 0.269 | 0.574 | +0.305 |
| mention_coverage | 0.769 | 1.000 | +0.231 |
| citation_accuracy | 1.000 | 0.974 | -0.026 |
| citation_coverage | 0.750 | 0.878 | +0.128 |
| hallucination_rate | 0.000 | 0.077 | +0.077 |
| mean_generation_seconds | 14.848 | 18.863 | +4.015 |

## vector — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| flow | 2 | 0.875 | 1.000 | 1.000 | 1.000 |
| navigation | 2 | 0.667 | 0.625 | 1.000 | 1.000 |
| relationship | 4 | 0.917 | 0.833 | 1.000 | 0.250 |
| repository | 2 | 1.000 | 0.417 | 1.000 | 1.000 |

## hybrid — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| flow | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| navigation | 2 | 0.833 | 0.600 | 1.000 | 1.000 |
| relationship | 4 | 0.875 | 0.875 | 1.000 | 1.000 |
| repository | 2 | 1.000 | 0.417 | 1.000 | 1.000 |

## Failure cases

- [hybrid] Where would I change the code to add a password reset endpoint? (navigation, classified mixed) — hallucinated citation; retrieved ['app/auth/service.py', 'app/auth/routes.py', 'app/auth/service.py', 'app/auth/service.py', 'app/auth/service.py']
