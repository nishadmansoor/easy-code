import fnmatch
import uuid
from pathlib import Path

import git

from backend.app.config.settings import settings
from backend.app.models.entities import RepositoryMetadata, RepositoryStatus

IGNORE_PATTERNS = [
    ".git/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "dist/",
    "build/",
    "target/",
    "coverage/",
    ".cache/",
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.mp4",
    "*.mp3",
    "*.wav",
    "*.zip",
    "*.tar.gz",
    "*.min.js",
    "*.map",
]

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".txt": "text",
}

SUPPORTED_PARSERS = {"python"}


def should_ignore(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    rel_str = str(rel)
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
        if pattern.endswith("/") and rel_str.startswith(pattern.rstrip("/")):
            return True
    return False


def detect_languages(repo_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in repo_path.rglob("*"):
        if f.is_file() and not should_ignore(f, repo_path):
            lang = LANGUAGE_EXTENSIONS.get(f.suffix)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    return counts


def collect_source_files(repo_path: Path) -> list[Path]:
    files = []
    for f in repo_path.rglob("*"):
        if f.is_file() and not should_ignore(f, repo_path):
            lang = LANGUAGE_EXTENSIONS.get(f.suffix)
            if lang and lang in SUPPORTED_PARSERS:
                files.append(f)
    return sorted(files)


def clone_repository(url: str) -> tuple[str, Path]:
    repo_id = uuid.uuid4().hex[:12]
    repo_dir = settings.repos_dir / repo_id
    repo_dir.mkdir(parents=True, exist_ok=True)
    git.Repo.clone_from(url, str(repo_dir))
    return repo_id, repo_dir


def create_repository_metadata(url: str, repo_id: str, repo_path: Path) -> RepositoryMetadata:
    lang_counts = detect_languages(repo_path)
    source_files = collect_source_files(repo_path)
    return RepositoryMetadata(
        id=repo_id,
        url=url,
        status=RepositoryStatus.READY,
        languages=sorted(lang_counts.keys()),
        file_count=len(source_files),
    )
