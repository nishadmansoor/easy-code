# EasyCode evaluation — requests

Repository: https://github.com/psf/requests — 121 files, 891 entities, 927 chunks, 886 graph nodes, 2194 relationships
Embeddings: all-MiniLM-L6-v2 | LLM: ollama/qwen2.5:7b | context limit: 12

## Vector-only baseline vs graph + vector hybrid

| Metric | Vector-only | Hybrid | Delta |
| --- | ---: | ---: | ---: |
| recall_at_1 | 0.550 | 0.700 | +0.150 |
| recall_at_3 | 0.850 | 0.950 | +0.100 |
| recall_at_5 | 0.950 | 0.950 | 0.000 |
| recall_at_10 | 0.950 | 0.950 | 0.000 |
| precision_at_5 | 0.258 | 0.395 | +0.137 |
| mrr | 0.742 | 0.867 | +0.125 |
| relevant_file_rate | 1.000 | 1.000 | 0.000 |
| entity_coverage | 0.583 | 0.767 | +0.183 |
| mean_retrieval_seconds | 0.571 | 0.422 | -0.149 |
| mention_coverage | 0.800 | 1.000 | +0.200 |
| citation_accuracy | 0.950 | 0.936 | -0.014 |
| citation_coverage | 0.900 | 0.850 | -0.050 |
| hallucination_rate | 0.100 | 0.200 | +0.100 |
| mean_generation_seconds | 24.994 | 26.904 | +1.910 |

## vector — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| flow | 2 | 1.000 | 0.750 | 1.000 | 1.000 |
| navigation | 1 | 1.000 | 0.333 | 1.000 | 1.000 |
| relationship | 3 | 0.833 | 0.750 | 1.000 | 0.333 |
| repository | 1 | 1.000 | 0.333 | 1.000 | 1.000 |

## hybrid — by question category

| Category | N | Recall@5 | MRR | Relevant file rate | Mention coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| component | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| flow | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| navigation | 1 | 1.000 | 1.000 | 1.000 | 1.000 |
| relationship | 3 | 0.833 | 0.778 | 1.000 | 1.000 |
| repository | 1 | 1.000 | 0.333 | 1.000 | 1.000 |

## Failure cases

- [vector] Where would I change the code to add a new retry policy? (navigation, classified vector_only) — hallucinated citation; retrieved ['src/requests/exceptions.py', 'tests/test_requests.py', 'src/requests/adapters.py', 'src/requests/utils.py', 'src/requests/exceptions.py']
- [hybrid] How is authentication handled? (component, classified semantic) — hallucinated citation; retrieved ['src/requests/auth.py', 'docs/user/authentication.rst', 'docs/user/authentication.rst', 'src/requests/utils.py', 'src/requests/sessions.py']
- [hybrid] Where would I change the code to add a new retry policy? (navigation, classified mixed) — hallucinated citation; retrieved ['src/requests/adapters.py', 'src/requests/auth.py', 'src/requests/cookies.py', 'src/requests/exceptions.py', 'src/requests/exceptions.py']
