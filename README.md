# EasyCode

An AI-powered codebase understanding platform. Paste a GitHub repository URL and get grounded explanations about the code.

Current Stage: 

You give it a GitHub URL. It clones the repo, parses Python files with AST, extracts classes/functions/methods/imports, chunks them into semantic units, embeds them with a local model, and stores everything in Qdrant (vectors) and Neo4j (graph). You can then search the codebase with natural language queries and get back relevant code with file paths and line numbers.

Example: you ask "How does HTML escaping work?" and it returns the escape function, _escape_inner, the Markup class, ranked by relevance with scores.

What's done (Phases 1-2):

GitHub ingestion and cloning
Python AST parsing
Semantic chunking
Local embeddings
Vector storage and search (Qdrant)
Code graph with file/class/function/method nodes and relationships (Neo4j)
REST API endpoints for all of the above


Next steps (phases 3-7):

3. Hybrid retrieval	Combines Qdrant results with Neo4j graph traversal. Classifies questions as semantic/structural/mixed, queries the right source, merges and deduplicates results.
4. LLM explanation	Takes retrieved context, sends it to a local LLM (Ollama), generates grounded explanations with citations. This is the "ask a question, get an answer" step.
5. Frontend	React/Next.js UI — paste URL, see overview, ask questions, browse code.
6. Evaluation	Benchmark repos with known questions, measure retrieval recall, answer correctness, hallucination rate. Compare vector-only vs hybrid.
7. Production polish	Docker for the backend, error handling, logging, config management, deployment.
The biggest gap right now is Phase 4 — without the LLM, users get raw search results but no natural language explanations. Phases 3 and 4 together are what make it actually useful.
