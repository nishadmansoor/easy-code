FROM python:3.11-slim

# git is required to clone the repositories being indexed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/hf-cache

# Install dependencies first so application edits do not invalidate the layer.
COPY pyproject.toml README.md ./
RUN mkdir -p backend && touch backend/__init__.py && pip install --no-cache-dir .

COPY backend/ backend/
COPY frontend/ frontend/

RUN mkdir -p data/repos

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
