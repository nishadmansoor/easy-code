# EasyCode

AI-powered codebase understanding platform. Paste a GitHub repository URL, get grounded explanations about the code.

## Quick Start

```bash
# Copy environment config
cp .env.example .env

# Start Qdrant and Neo4j
docker compose up -d

# Install dependencies
pip install -e ".[dev]"

# Start the API server
uvicorn backend.app.api.main:app --reload
```

## API

```bash
# Index a repository
curl -X POST http://localhost:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/user/repo"}'

# Query the repository (semantic search)
curl -X POST http://localhost:8000/api/repositories/{id}/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How does authentication work?", "repository_id": "{id}"}'

# Get repository graph overview
curl http://localhost:8000/api/repositories/{id}/graph/overview

# Get file contents from graph
curl "http://localhost:8000/api/repositories/{id}/graph/file?file_path=src/auth.py"

# Get class methods from graph
curl "http://localhost:8000/api/repositories/{id}/graph/class?class_name=AuthService"

# Get callers of a function
curl "http://localhost:8000/api/repositories/{id}/graph/callers?function_name=login"

# Get files that import a file
curl "http://localhost:8000/api/repositories/{id}/graph/importers?file_path=src/auth.py"
```

## Project Structure

```text
backend/
├── app/
│   ├── api/          FastAPI endpoints
│   ├── config/       Settings
│   ├── ingestion/    Repository cloning, language detection
│   ├── parsing/      AST-based code parsing
│   ├── chunking/     Semantic chunking
│   ├── embeddings/   Local embedding models
│   ├── graph/        Neo4j graph store and builder
│   ├── vector/       Qdrant integration
│   ├── retrieval/    Semantic and graph retrieval
│   └── models/       Data models
└── tests/
```

## Phase 1 - Complete

- GitHub repository ingestion
- Repository cloning and language detection
- Ignore rules for non-source files
- Python AST parsing with entity extraction
- Semantic chunking by code unit
- Local embeddings (sentence-transformers)
- Qdrant vector storage
- Basic vector search

## Phase 2 - Complete

- Neo4j graph database
- File, class, function, method nodes
- Repository contains file relationships
- File defines class/function relationships
- Class contains method relationships
- File imports file relationships
- Graph query endpoints (overview, file, class, callers, importers)

## Next Phases

- **Phase 3**: Hybrid retrieval (semantic + structural)
- **Phase 4**: LLM explanation generation
- **Phase 5**: Web interface
- **Phase 6**: Evaluation framework
