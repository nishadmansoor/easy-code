"""Repository acquisition and file discovery.

Repositories are untrusted input: this module only ever *reads* files. It never
runs repository scripts, build commands, installers or tests.
"""

import fnmatch
import logging
import re
import shutil
import uuid
from pathlib import Path

import git

from backend.app.config.settings import settings

logger = logging.getLogger(__name__)

IGNORE_DIRECTORIES = [
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    "coverage",
    ".cache",
    ".next",
    ".nuxt",
    ".tox",
    "site-packages",
    "vendor",
    "third_party",
]

IGNORE_FILE_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.o",
    "*.a",
    "*.class",
    "*.jar",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.webp",
    "*.pdf",
    "*.mp4",
    "*.mp3",
    "*.wav",
    "*.mov",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.gz",
    "*.7z",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "*.ipynb_checkpoints",
]

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".cfg": "config",
    ".ini": "config",
    ".md": "markdown",
    ".rst": "markdown",
    ".txt": "text",
}

#: Languages for which a structural parser is registered. Documentation
#: languages are handled separately by the documentation parser.
CODE_LANGUAGES = {"python", "javascript", "typescript"}
DOC_LANGUAGES = {"markdown"}

# Framework detection is deliberately evidence-based: a marker only counts when
# the corresponding file exists or the dependency is declared.
DEPENDENCY_FILES = [
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "package.json",
    "pom.xml",
    "build.gradle",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
]

FRAMEWORK_MARKERS: dict[str, tuple[str, ...]] = {
    "FastAPI": ("fastapi",),
    "Django": ("django",),
    "Flask": ("flask",),
    "Pyramid": ("pyramid",),
    "SQLAlchemy": ("sqlalchemy",),
    "Pydantic": ("pydantic",),
    "PyTorch": ("torch",),
    "TensorFlow": ("tensorflow",),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "Pandas": ("pandas",),
    "Celery": ("celery",),
    "React": ("react",),
    "Next.js": ("next",),
    "Vue": ("vue",),
    "Angular": ("@angular/core",),
    "Express": ("express",),
    "Spring": ("spring-boot", "springframework"),
    "Rails": ("rails",),
}


class IngestionError(RuntimeError):
    """Raised when a repository cannot be acquired or read."""


def _load_gitignore_patterns(root: Path) -> list[str]:
    """Read simple .gitignore globs. Negations and complex syntax are skipped."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns: list[str] = []
    try:
        for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            patterns.append(line.rstrip("/"))
    except OSError:
        return []
    return patterns


def should_ignore(path: Path, root: Path, extra_patterns: list[str] | None = None) -> bool:
    """Return True when ``path`` must not be indexed."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True

    parts = rel.parts
    for part in parts[:-1] if path.is_file() else parts:
        if part in IGNORE_DIRECTORIES:
            return True
    # A directory path passed directly (e.g. root/node_modules) must also match.
    if parts and parts[-1] in IGNORE_DIRECTORIES:
        return True

    rel_str = rel.as_posix()
    for pattern in IGNORE_FILE_PATTERNS + (extra_patterns or []):
        if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(rel_str, pattern):
            return True
        if fnmatch.fnmatch(rel_str, f"{pattern}/*") or rel_str.startswith(f"{pattern}/"):
            return True
    return False


def _is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True


def iter_repository_files(repo_path: Path, respect_gitignore: bool = True) -> list[Path]:
    """All indexable files in the repository, as absolute paths."""
    extra = _load_gitignore_patterns(repo_path) if respect_gitignore else []
    files: list[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if should_ignore(path, repo_path, extra):
            continue
        if path.stat().st_size > settings.max_file_bytes:
            continue
        if _is_probably_binary(path):
            continue
        files.append(path)
    return sorted(files)


def relative_path(path: Path, root: Path) -> str:
    """Repository-relative POSIX path, used for every citation and node key."""
    return path.relative_to(root).as_posix()


def detect_languages(repo_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in iter_repository_files(repo_path):
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def collect_source_files(repo_path: Path) -> list[Path]:
    """Files a structural parser can handle."""
    return [
        path
        for path in iter_repository_files(repo_path)
        if LANGUAGE_EXTENSIONS.get(path.suffix.lower()) in CODE_LANGUAGES
    ]


def collect_documentation_files(repo_path: Path) -> list[Path]:
    return [
        path
        for path in iter_repository_files(repo_path)
        if LANGUAGE_EXTENSIONS.get(path.suffix.lower()) in DOC_LANGUAGES
    ]


def detect_frameworks(repo_path: Path) -> list[str]:
    """Infer frameworks from declared dependencies and import statements."""
    haystack: list[str] = []
    for name in DEPENDENCY_FILES:
        path = repo_path / name
        if path.is_file():
            try:
                haystack.append(path.read_text(encoding="utf-8", errors="replace").lower())
            except OSError:
                continue

    for path in collect_source_files(repo_path)[:400]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        haystack.extend(
            line.lower() for line in text.splitlines() if line.startswith(("import ", "from "))
        )

    blob = "\n".join(haystack)
    found = [
        framework
        for framework, markers in FRAMEWORK_MARKERS.items()
        if any(re.search(rf"\b{re.escape(marker)}\b", blob) for marker in markers)
    ]
    return sorted(found)


def repository_name(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1] or url


def normalize_repository_url(url: str) -> str:
    """Validate and normalise a repository URL.

    Only http(s) URLs are accepted; ``git@``/``ssh://``/``file://`` and shell
    metacharacters are rejected so that a URL can never reach a shell or read
    the local filesystem.
    """
    candidate = url.strip()
    if not candidate:
        raise IngestionError("Repository URL is empty")
    if not re.match(r"^https?://[^\s]+$", candidate):
        raise IngestionError("Only http(s) repository URLs are supported")
    if any(ch in candidate for ch in " \t\n\r\"'`;|&$<>\\"):
        raise IngestionError("Repository URL contains unsupported characters")
    if re.search(r"[?#]", candidate):
        raise IngestionError("Repository URL must not contain a query or fragment")
    return candidate.removesuffix("/")


def clone_repository(url: str, repo_id: str | None = None) -> tuple[str, Path]:
    """Shallow-clone ``url`` into an isolated per-repository workspace."""
    normalized = normalize_repository_url(url)
    repo_id = repo_id or uuid.uuid4().hex[:12]
    repo_dir = settings.repos_dir / repo_id

    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Cloning %s into %s", normalized, repo_dir)
    try:
        git.Repo.clone_from(
            normalized,
            str(repo_dir),
            depth=settings.clone_depth,
            multi_options=["--no-single-branch"] if settings.clone_depth == 0 else None,
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
        )
    except git.GitCommandError as exc:
        shutil.rmtree(repo_dir, ignore_errors=True)
        raise IngestionError(f"Failed to clone repository: {exc.stderr or exc}") from exc
    return repo_id, repo_dir


def delete_repository_workspace(repo_id: str) -> None:
    shutil.rmtree(settings.repos_dir / repo_id, ignore_errors=True)
