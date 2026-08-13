# AGENT.md

# EasyCode — AI-Powered Codebase Understanding

## 1. Project Overview

EasyCode is an AI-powered platform that helps people understand unfamiliar software repositories.

The primary user is **anyone who wants to understand a codebase better**. The user should not need to be an experienced software engineer or already understand the repository.

The core workflow is:

1. User provides a GitHub repository URL.
2. EasyCode ingests and analyzes the repository.
3. EasyCode builds both:

   * a semantic representation of the codebase
   * a structural graph of the codebase
4. The user asks natural-language questions.
5. EasyCode retrieves relevant semantic and structural context.
6. An LLM produces a grounded explanation.
7. The response points the user back to the relevant files, classes, functions, and line ranges.

Example questions:

* What does this repository do?
* How is the application structured?
* Where does authentication happen?
* What happens when a user submits a request?
* Which files are responsible for user authentication?
* How does `AuthService` interact with `UserRepository`?
* Where is this function called?
* What would I need to change to add feature X?
* Explain the architecture of this project in simple terms.
* What is the entry point of this application?

---

# 2. Core Product Principle

The central goal of EasyCode is:

> **Help the user build a mental model of an unfamiliar codebase.**

EasyCode is NOT primarily a code-generation chatbot.

It should explain:

* what components do
* where they are located
* how components interact
* what depends on what
* how data flows through the system
* where important functionality is implemented
* how the repository is organized

Prefer explanations such as:

> Authentication begins in `auth/routes.py`, which receives the login request and calls `AuthService.login()`. The service validates the credentials through `UserRepository` and creates a session through `SessionManager`.

Avoid responses that simply dump large amounts of source code.

---

# 3. Core Technical Thesis

EasyCode should investigate whether combining:

1. **semantic retrieval**
2. **explicit code structure**

produces better codebase understanding than semantic retrieval alone.

The system therefore uses two complementary retrieval systems:

### Qdrant

Qdrant provides semantic retrieval.

It answers questions such as:

> "Find code that is conceptually related to authentication."

### Neo4j

Neo4j provides structural retrieval.

It answers questions such as:

> "What calls this function?"

or:

> "What files import this module?"

The LLM combines these two sources of context to produce the final explanation.

---

# 4. High-Level Architecture

```text
                         EASYCODE

                    GitHub Repository
                           |
                           v
                  Repository Ingestion
                           |
              +------------+------------+
              |                         |
              v                         v
        Code Parsing              Documentation
              |
              v
       Code Entity Extraction
              |
       +------+------+
       |             |
       v             v
    Qdrant         Neo4j
  Vector Store    Graph Store
       |             |
       +------+------+
              |
              v
       Hybrid Retrieval
              |
              v
       Context Assembly
              |
              v
             LLM
              |
              v
     Grounded Explanation
              |
              v
          User Interface
```

---

# 5. Required Technology Direction

The initial implementation should use:

### Backend

* Python
* FastAPI
* Pydantic
* PostgreSQL where persistent application metadata is needed

### Semantic Retrieval

* Qdrant
* local embedding model by default

### Graph Retrieval

* Neo4j

### Code Parsing

Use AST-based parsing whenever practical.

For multi-language support, use a parser architecture that can be extended cleanly.

### LLM

The system must support local inference.

Paid API providers may be supported optionally, but the core project must NOT require a paid API key.

### Frontend

Use a lightweight modern web frontend.

React/Next.js is preferred if a frontend framework is needed.

### Infrastructure

Use Docker for reproducible local development.

---

# 6. No Paid API Key Requirement

The default project must work without requiring:

* OpenAI API keys
* Anthropic API keys
* other paid model APIs

The architecture must use interfaces so models can be swapped.

For example:

```text
EmbeddingModel
    |
    +-- LocalEmbeddingModel
    |
    +-- OptionalAPIEmbeddingModel


LLMProvider
    |
    +-- LocalLLMProvider
    |
    +-- OptionalAPIProvider
```

API credentials must never be hard-coded.

If API-based providers are supported, credentials must come from environment variables.

---

# 7. Repository Ingestion

The ingestion pipeline must:

1. Accept a GitHub repository URL.
2. Clone the repository into an isolated workspace.
3. Detect repository languages.
4. Detect major frameworks where possible.
5. Apply ignore rules.
6. Parse source files.
7. Extract code entities.
8. Extract structural relationships.
9. Create semantic chunks.
10. Generate embeddings.
11. Store embeddings in Qdrant.
12. Store relationships in Neo4j.
13. Generate repository metadata.
14. Mark the repository as ready.

The ingestion process should expose status such as:

```text
queued
cloning
parsing
indexing
building_graph
generating_embeddings
ready
failed
```

---

# 8. Security Requirements for Repository Ingestion

Repositories are untrusted input.

EasyCode must NOT execute arbitrary repository code during ingestion.

Do not automatically:

* run repository scripts
* execute binaries
* execute package installation scripts
* run arbitrary build commands
* execute tests
* expose repository secrets

Do not execute:

```text
npm install
pip install
make
./script.sh
python setup.py
```

unless explicitly placed behind a secure sandbox in a future version.

The initial implementation should only **read and parse repository files**.

---

# 9. Files to Ignore

Do not index:

```text
.git/
node_modules/
.venv/
venv/
__pycache__/
dist/
build/
target/
coverage/
.cache/
```

Also ignore:

* binary files
* images
* videos
* generated assets
* compiled artifacts
* large lock files when they are not useful for understanding
* minified JavaScript
* generated documentation

Respect `.gitignore` where practical.

The ignore system should be configurable.

---

# 10. Supported Languages

Initial priority:

1. Python
2. JavaScript
3. TypeScript
4. Java

The architecture must allow additional languages later.

Do not design the system around Python-specific assumptions.

---

# 11. Code Parsing

Do not rely exclusively on arbitrary text splitting.

When practical, use AST or syntax-tree parsing.

Extract:

### Files

* path
* language
* module

### Classes

* name
* parent classes
* methods
* location

### Functions

* name
* parameters
* return information where available
* location

### Methods

* class
* name
* location

### Imports

* imported module
* source file

### Calls

* caller
* callee where resolvable

### Other relationships

* inheritance
* definitions
* references
* decorators
* exports

Every extracted entity should retain:

```text
repository_id
file_path
language
entity_type
entity_name
start_line
end_line
```

---

# 12. Code Graph

Neo4j should represent the repository as a graph.

Example:

```text
Repository
    |
    +-- CONTAINS --> File
                       |
                       +-- DEFINES --> Class
                       |                |
                       |                +-- CONTAINS --> Method
                       |
                       +-- DEFINES --> Function
                       |
                       +-- IMPORTS --> File
```

Useful relationship types include:

```text
REPOSITORY_CONTAINS_FILE
FILE_DEFINES_CLASS
FILE_DEFINES_FUNCTION
CLASS_CONTAINS_METHOD
FILE_IMPORTS_FILE
FUNCTION_CALLS_FUNCTION
FUNCTION_USES_CLASS
CLASS_INHERITS_CLASS
METHOD_CALLS_METHOD
FILE_REFERENCES_FILE
```

Relationships should only be created when they can be reasonably inferred from static analysis.

Do not invent relationships.

---

# 13. Semantic Chunking

Semantic chunks should correspond to meaningful code units.

Preferred chunk types:

* function
* method
* class
* module
* README section
* documentation section
* configuration section

Avoid arbitrary fixed-length chunks when a structural unit is available.

Every vector record must include metadata:

```text
repository_id
file_path
language
entity_type
entity_name
start_line
end_line
```

---

# 14. Embeddings

Use a local open-source embedding model by default.

The embedding implementation must be abstracted.

Example:

```python
class EmbeddingModel:
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...
```

Do not hard-code one embedding model throughout the application.

The model should be configurable.

---

# 15. Qdrant

Qdrant stores semantic representations of code.

Each vector should correspond to a meaningful code/documentation chunk.

Example payload:

```json
{
  "repository_id": "repo_123",
  "file_path": "src/auth/service.py",
  "entity_type": "class",
  "entity_name": "AuthService",
  "start_line": 20,
  "end_line": 87
}
```

Qdrant should support filtering by:

* repository
* language
* file
* entity type

---

# 16. Neo4j

Neo4j stores structural relationships.

The graph should allow queries such as:

* What functions does this file contain?
* What imports this file?
* What calls this function?
* What functions does this class use?
* What classes inherit from this class?
* What is the dependency chain between A and B?

Do not use Neo4j simply as another document store.

Its value comes from explicit relationships.

---

# 17. Hybrid Retrieval

Every user question should first be classified as one of:

### Semantic

Example:

> What does this application do?

### Structural

Example:

> What calls `AuthService.login()`?

### Mixed

Example:

> How does a login request move through the application?

Semantic questions should rely heavily on Qdrant.

Structural questions should rely heavily on Neo4j.

Mixed questions should combine both.

---

# 18. Retrieval Pipeline

The retrieval pipeline should follow:

```text
User Question
      |
      v
Question Analysis
      |
      +-------- Semantic --------> Qdrant
      |
      +-------- Structural ------> Neo4j
      |
      +-------- Mixed -----------> Both
                                     |
                                     v
                              Result Ranking
                                     |
                                     v
                              Deduplication
                                     |
                                     v
                              Context Assembly
```

Do not blindly send all retrieved content to the LLM.

---

# 19. Context Ranking

Prioritize:

1. Directly relevant functions/classes
2. Relevant callers/callees
3. Relevant imports/dependencies
4. Relevant files
5. Documentation
6. Broader repository context

Avoid excessive context.

The retrieval system should optimize for:

* relevance
* coverage
* diversity
* source grounding

---

# 20. Answer Generation

The LLM should be instructed:

> Answer only using evidence from the retrieved repository context. Do not invent files, functions, classes, dependencies, or behavior.

Every answer should contain:

1. Direct answer
2. Explanation
3. Relevant source locations
4. Relationships when applicable
5. Uncertainty/caveats when needed

Example:

```text
Authentication is handled primarily by AuthService.

The request enters:
src/auth/routes.py:14-32

which calls:
src/auth/service.py:42-78

AuthService then validates the user through:
src/users/repository.py:20-55
```

---

# 21. Hallucination Prevention

Hallucination prevention is a core requirement.

The system must never confidently invent:

* files
* functions
* classes
* imports
* dependencies
* code behavior
* relationships

If insufficient evidence exists, answer:

> "I couldn't find enough evidence in the indexed repository to determine this."

The system should prefer uncertainty over unsupported claims.

---

# 22. Source Citations

Responses should cite repository locations whenever possible.

Preferred format:

```text
src/auth/service.py:42-78
```

Clicking a source should eventually allow the user to inspect the relevant code.

Source references must come from actual indexed repository metadata.

Never fabricate line numbers.

---

# 23. Repository Overview

After indexing, EasyCode should automatically produce a high-level repository overview.

The overview should include:

* project purpose
* languages
* frameworks
* major directories
* major components
* entry points
* important dependencies
* high-level architecture
* major data flows

The goal is to give the user a mental model before they ask detailed questions.

---

# 24. User Interface

The UI should prioritize codebase exploration rather than generic chat.

Primary workflow:

```text
Paste GitHub URL
       |
       v
Index Repository
       |
       v
Repository Overview
       |
       v
Ask Question
       |
       v
Explanation
       |
       v
Explore Sources
```

The interface should make it easy to:

* navigate files
* view cited code
* inspect related components
* understand dependencies
* ask follow-up questions

---

# 25. API

Suggested endpoints:

```text
POST   /repositories
GET    /repositories/{id}
GET    /repositories/{id}/status
POST   /repositories/{id}/query
GET    /repositories/{id}/files
GET    /repositories/{id}/graph
DELETE /repositories/{id}
```

Example query response:

```json
{
  "answer": "Authentication begins in...",
  "sources": [
    {
      "file": "src/auth/service.py",
      "start_line": 42,
      "end_line": 78
    }
  ]
}
```

---

# 26. Evaluation

EasyCode must have a real evaluation framework.

Create benchmark repositories and questions.

Categories:

### Repository-level

* What does this repository do?
* What framework does it use?
* What are its major components?

### Component-level

* What does X do?
* Where is X implemented?

### Relationship-level

* What calls X?
* What does A depend on?

### Flow-level

* What happens when the user performs X?

### Navigation-level

* Where would I modify the code to implement X?

---

# 27. Evaluation Metrics

Measure:

### Retrieval

* Recall@K
* MRR
* relevant-file retrieval rate

### Answer quality

* correctness
* completeness
* groundedness

### Source quality

* citation accuracy
* citation coverage

### Reliability

* hallucination rate

### Performance

* latency
* indexing time
* memory usage

---

# 28. Critical Experiment

The final project should compare:

### Baseline

Vector-only RAG

against:

### Proposed system

Graph + vector hybrid retrieval

The key question is:

> Does structural information from Neo4j improve codebase understanding compared with semantic retrieval alone?

This experiment is essential to the project's research/technical story.

---

# 29. Testing

Write tests for:

* repository ingestion
* ignore rules
* parsing
* entity extraction
* relationship extraction
* chunking
* embedding generation
* Qdrant storage
* Neo4j storage
* vector retrieval
* graph retrieval
* hybrid retrieval
* context ranking
* answer generation
* API endpoints

Use small local fixture repositories for deterministic tests.

Do not rely on large external repositories for unit tests.

---

# 30. Project Structure

Use approximately:

```text
easycode/
│
├── AGENT.md
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── config/
│   │   ├── ingestion/
│   │   ├── parsing/
│   │   ├── chunking/
│   │   ├── embeddings/
│   │   ├── graph/
│   │   ├── vector/
│   │   ├── retrieval/
│   │   ├── generation/
│   │   ├── evaluation/
│   │   └── models/
│   │
│   └── tests/
│
├── frontend/
│
├── scripts/
│
├── docs/
│
└── data/
```

Keep backend components modular.

---

# 31. Development Phases

## Phase 1 — Basic Repository Understanding

Implement:

* GitHub ingestion
* repository cloning
* ignore rules
* Python parsing
* semantic chunking
* local embeddings
* Qdrant
* basic vector search

Goal:

```text
GitHub URL
    ↓
Question
    ↓
Relevant code
```

---

## Phase 2 — Code Graph

Implement:

* Neo4j
* file nodes
* class nodes
* function nodes
* method nodes
* import relationships
* call relationships
* inheritance relationships

Goal:

```text
GitHub URL
    ↓
Repository Graph
```

---

## Phase 3 — Hybrid Retrieval

Implement:

* question classification
* Qdrant retrieval
* Neo4j retrieval
* hybrid retrieval
* result ranking
* deduplication

Goal:

```text
Question
    ↓
Semantic + Structural Context
```

---

## Phase 4 — LLM Explanation

Implement:

* local LLM provider
* prompt templates
* grounded answer generation
* citations
* uncertainty handling

Goal:

```text
Question
    ↓
Grounded Explanation
```

---

## Phase 5 — Product Interface

Implement:

* repository submission
* indexing status
* repository overview
* question interface
* source navigation
* code visualization where useful

---

## Phase 6 — Evaluation

Build:

* benchmark repositories
* benchmark questions
* vector-only baseline
* hybrid system
* evaluation scripts
* metrics
* failure analysis

---

## Phase 7 — Production Polish

Add:

* Docker
* logging
* error handling
* caching
* configuration
* documentation
* deployment

---

# 32. Engineering Principles

Prioritize:

* modularity
* type safety
* testability
* clear interfaces
* reproducibility
* simple architecture
* useful logging
* meaningful errors

Avoid:

* giant files
* hard-coded configuration
* unnecessary dependencies
* duplicated code
* unnecessary abstractions
* premature optimization

Do not introduce technologies simply because they are trendy.

Every dependency should have a clear purpose.

---

# 33. Definition of Done

EasyCode is complete when a user can:

1. Paste a public GitHub repository URL.
2. Have the repository indexed automatically.
3. Receive a repository overview.
4. Ask natural-language questions.
5. Receive grounded explanations.
6. See relevant source files.
7. See relevant functions/classes.
8. Ask relationship questions.
9. Ask code-flow questions.
10. Receive useful answers without manually understanding the repository first.

The system must demonstrate measurable value from combining semantic and structural retrieval.

---

# 34. Final Portfolio Goal

The final project should demonstrate:

* LLM applications
* RAG
* vector databases
* graph databases
* code parsing
* information retrieval
* semantic search
* graph traversal
* software engineering
* API development
* evaluation
* AI reliability
* local/open-source model usage

The portfolio presentation should emphasize:

> **EasyCode combines semantic retrieval with a structural code graph to help users build accurate mental models of unfamiliar codebases.**

The final README should include:

* project motivation
* architecture diagram
* system demo
* example questions
* Qdrant architecture
* Neo4j architecture
* hybrid retrieval pipeline
* evaluation methodology
* vector-only vs hybrid comparison
* failure cases
* performance metrics
* setup instructions
* limitations
