"""Ingestion: ignore rules, language detection, framework detection, URL safety."""

import pytest

from backend.app.ingestion.repository import (
    IngestionError,
    collect_documentation_files,
    collect_source_files,
    detect_frameworks,
    detect_languages,
    iter_repository_files,
    normalize_repository_url,
    relative_path,
    repository_name,
    should_ignore,
)


class TestIgnoreRules:
    def test_ignores_vcs_and_dependency_directories(self, tmp_path):
        for directory in (".git", "node_modules", "__pycache__", ".venv", "dist"):
            path = tmp_path / directory / "file.py"
            path.parent.mkdir(parents=True)
            path.write_text("x = 1")
            assert should_ignore(path, tmp_path), directory

    def test_ignores_nested_dependency_directories(self, tmp_path):
        path = tmp_path / "src" / "node_modules" / "pkg" / "index.js"
        path.parent.mkdir(parents=True)
        path.write_text("x")
        assert should_ignore(path, tmp_path)

    def test_ignores_binaries_images_and_lockfiles(self, tmp_path):
        for name in ("logo.png", "app.min.js", "poetry.lock", "lib.so", "bundle.js.map"):
            path = tmp_path / name
            path.write_text("x")
            assert should_ignore(path, tmp_path), name

    def test_keeps_source_files(self, tmp_path):
        path = tmp_path / "src" / "main.py"
        path.parent.mkdir(parents=True)
        path.write_text("x = 1")
        assert not should_ignore(path, tmp_path)

    def test_respects_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("secrets/\n*.generated.py\n")
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "keys.py").write_text("KEY = 'x'")
        (tmp_path / "schema.generated.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("x = 1")

        discovered = {path.name for path in iter_repository_files(tmp_path)}
        assert "app.py" in discovered
        assert "keys.py" not in discovered
        assert "schema.generated.py" not in discovered

    def test_skips_binary_content(self, tmp_path):
        (tmp_path / "data.py").write_bytes(b"import os\x00\x00binary")
        assert iter_repository_files(tmp_path) == []


class TestDiscovery:
    def test_detects_languages(self, sample_repo_path):
        counts = detect_languages(sample_repo_path)
        assert counts["python"] == 12
        assert counts["markdown"] == 1

    def test_collects_only_parseable_source(self, sample_repo_path):
        sources = collect_source_files(sample_repo_path)
        assert all(path.suffix == ".py" for path in sources)
        assert len(sources) == 12

    def test_collects_documentation(self, sample_repo_path):
        docs = collect_documentation_files(sample_repo_path)
        assert [path.name for path in docs] == ["README.md"]

    def test_relative_path_is_posix_and_root_relative(self, sample_repo_path):
        target = sample_repo_path / "app" / "auth" / "service.py"
        assert relative_path(target, sample_repo_path) == "app/auth/service.py"

    def test_detects_frameworks_from_declared_dependencies(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi==0.104.0\npydantic>=2\n")
        assert set(detect_frameworks(tmp_path)) >= {"FastAPI", "Pydantic"}

    def test_reports_no_frameworks_when_there_is_no_evidence(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        assert detect_frameworks(tmp_path) == []


class TestUrlHandling:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:psf/requests.git",
            "file:///etc/passwd",
            "ssh://github.com/a/b",
            "https://github.com/a/b; rm -rf /",
            "https://github.com/a/b?token=secret",
            "",
            "   ",
        ],
    )
    def test_rejects_unsafe_urls(self, url):
        with pytest.raises(IngestionError):
            normalize_repository_url(url)

    def test_accepts_and_normalizes_https_urls(self):
        assert normalize_repository_url("https://github.com/psf/requests/") == (
            "https://github.com/psf/requests"
        )

    def test_repository_name(self):
        assert repository_name("https://github.com/psf/requests.git") == "requests"
        assert repository_name("https://github.com/psf/requests/") == "requests"
