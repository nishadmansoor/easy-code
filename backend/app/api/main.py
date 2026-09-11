import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import router
from backend.app.config.settings import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
# These libraries log every HTTP call at INFO, which drowns out the pipeline.
for noisy in ("httpx", "httpcore", "sentence_transformers", "neo4j", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.repos_dir.mkdir(parents=True, exist_ok=True)
    logger.info("EasyCode starting; repositories stored in %s", settings.repos_dir)
    yield
    from backend.app.api.dependencies import reset_services

    reset_services()


app = FastAPI(
    title="EasyCode",
    version="1.0.0",
    description=(
        "Combines semantic retrieval with a structural code graph so you can build "
        "an accurate mental model of an unfamiliar codebase."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND_DIR / "index.html")
