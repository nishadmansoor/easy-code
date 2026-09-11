from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Vector store
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "easycode_chunks"

    # Graph store
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "easycode"

    # Application metadata database (SQLite by default, PostgreSQL supported)
    database_url: str = "sqlite:///./data/easycode.db"

    # Embeddings
    embedding_model_name: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 64

    # LLM generation
    llm_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    llm_timeout_seconds: float = 180.0
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1200

    # Optional OpenAI-compatible provider. Never hard-code credentials.
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Retrieval
    retrieval_vector_limit: int = 20
    retrieval_context_limit: int = 12
    retrieval_max_context_chars: int = 24000

    # Ingestion limits
    repos_dir: Path = Path("./data/repos")
    max_file_bytes: int = 1_000_000
    clone_depth: int = 1

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
