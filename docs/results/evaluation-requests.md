# EasyCode evaluation — requests

Repository: https://github.com/psf/requests — 121 files, 891 entities, 927 chunks, 929 graph nodes, 2286 relationships
Embeddings: all-MiniLM-L6-v2 | LLM: ollama/qwen2.5:7b | context limit: 12

## Vector-only baseline vs graph + vector hybrid

| Metric | Vector-only | Hybrid | Delta |
| --- | ---: | ---: | ---: |
| recall_at_1 | 0.550 | 0.700 | +0.150 |
| recall_at_3 | 0.850 | 0.900 | +0.050 |
| recall_at_5 | 0.950 | 0.950 | 0.000 |
| recall_at_10 | 0.950 | 1.000 | +0.050 |
| precision_at_5 | 0.258 | 0.385 | +0.127 |
| mrr | 0.742 | 0.853 | +0.112 |
| relevant_file_rate | 1.000 | 1.000 | 0.000 |
| entity_coverage | 0.583 | 0.767 | +0.183 |
| mean_retrieval_seconds | 0.473 | 0.305 | -0.168 |
| mention_coverage | 0.800 | 1.000 | +0.200 |
| citation_accuracy | 0.900 | 1.000 | +0.100 |
| citation_coverage | 0.850 | 0.900 | +0.050 |
| hallucination_rate | 0.000 | 0.000 | 0.000 |
| mean_generation_seconds | 28.084 | 29.310 | +1.226 |

## vector — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| flow | 2 | 1.000 | 0.750 | 1.000 | 0.500 |
| navigation | 1 | 1.000 | 0.333 | 1.000 | 1.000 |
| relationship | 3 | 0.833 | 0.750 | 1.000 | 0.667 |
| repository | 1 | 1.000 | 0.333 | 1.000 | 1.000 |

## hybrid — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| flow | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| navigation | 1 | 1.000 | 1.000 | 1.000 | 1.000 |
| relationship | 3 | 0.833 | 0.733 | 1.000 | 1.000 |
| repository | 1 | 1.000 | 0.333 | 1.000 | 1.000 |
