"""
Tests for the code context builder — file tree caching and targeted file fetch.
"""

from unittest.mock import AsyncMock

import pytest

from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.github.client import GitHubAPIClient
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import CachedFile, CodeContext


@pytest.fixture
async def code_context_db(tmp_path):
    """Initialize a real GitHubDB for code context tests."""
    config = DatabaseConfig(sqlite_path=str(tmp_path / "github_test.db"))
    backend = SQLiteBackend(config)
    db = GitHubDB(backend)
    await db.setup()
    yield db
    await db.close()


class TestCodeContextBuilder:
    """Test file tree caching, file selection, and context building."""

    def test_should_include_python(self):
        """Python files are included by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
        )
        assert builder._should_include("src/main.py") is True

    def test_should_include_yaml(self):
        """YAML files are included by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
        )
        assert builder._should_include("config/settings.yaml") is True

    def test_should_exclude_lock(self):
        """Lock files are excluded by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
        )
        assert builder._should_include("poetry.lock") is False

    def test_should_exclude_pycache(self):
        """__pycache__ is excluded by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
        )
        assert builder._should_include("__pycache__/module.cpython-313.pyc") is False

    def test_should_exclude_non_matching(self):
        """Files not matching any include pattern are excluded."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
        )
        assert builder._should_include("image.png") is False

    def test_custom_patterns(self):
        """Custom include/exclude patterns override defaults."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
            include_patterns=["*.rs", "*.go"],
            exclude_patterns=["vendor/*"],
        )
        assert builder._should_include("src/main.rs") is True
        assert builder._should_include("src/main.py") is False
        assert builder._should_include("vendor/lib.go") is False

    def test_parse_file_list_valid_json(self):
        """Parse valid JSON array of file paths."""
        result = CodeContextBuilder._parse_file_list('["src/main.py", "README.md"]')
        assert result == ["src/main.py", "README.md"]

    def test_parse_file_list_wrapped_json(self):
        """Parse JSON array wrapped in markdown code block."""
        raw = 'Some text before\n["src/main.py"]\nand after'
        result = CodeContextBuilder._parse_file_list(raw)
        assert result == ["src/main.py"]

    def test_parse_file_list_invalid(self):
        """Gracefully handle unparseable response."""
        result = CodeContextBuilder._parse_file_list("just some text")
        assert result == []

    def test_parse_file_list_empty_array(self):
        """Parse empty JSON array."""
        result = CodeContextBuilder._parse_file_list("[]")
        assert result == []

    def test_format_context_empty(self):
        """Format empty context."""
        ctx = CodeContext(repo="test/repo", question="test?")
        result = CodeContextBuilder.format_context(ctx)
        assert "no code context" in result.lower()

    def test_format_context_with_files(self):
        """Format context with files includes paths and content."""
        ctx = CodeContext(
            repo="test/repo",
            question="how?",
            files=[
                CachedFile(repo="test/repo", path="main.py", sha="abc", content="print('hello')"),
                CachedFile(
                    repo="test/repo", path="utils.py", sha="def", content="def helper(): pass"
                ),
            ],
            total_size=100,
        )
        result = CodeContextBuilder.format_context(ctx)
        assert "main.py" in result
        assert "utils.py" in result
        assert "print('hello')" in result

    @pytest.mark.asyncio
    async def test_refresh_tree(self, code_context_db, mock_github_client):
        """refresh_tree fetches and caches the file tree."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,  # Always refresh
        )

        refreshed = await builder.refresh_tree("moltbook/api")
        assert refreshed is True

        # Verify paths were cached
        paths = await code_context_db.get_tree_paths("moltbook/api")
        assert "src/main.py" in paths
        assert "README.md" in paths

    @pytest.mark.asyncio
    async def test_refresh_tree_skips_unchanged(self, code_context_db, mock_github_client):
        """refresh_tree skips if root sha hasn't changed."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )

        # First refresh
        await builder.refresh_tree("moltbook/api")

        # Second refresh — same sha, should skip
        # But tree_refresh_minutes=0 means it'll try; the sha check will cause skip
        refreshed = await builder.refresh_tree("moltbook/api")
        assert refreshed is False

    @pytest.mark.asyncio
    async def test_select_files_with_llm(self, code_context_db, mock_github_client):
        """select_files uses LLM to pick relevant files."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content='["src/main.py", "src/utils.py"]',
            )
        )

        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=mock_pipeline,
            tree_refresh_minutes=0,
        )

        # First populate the tree
        await builder.refresh_tree("moltbook/api")

        # Then select files
        selected = await builder.select_files("moltbook/api", "How does main work?")
        assert "src/main.py" in selected
        assert "src/utils.py" in selected

    @pytest.mark.asyncio
    async def test_select_files_filters_invalid(self, code_context_db, mock_github_client):
        """select_files filters out paths not in the tree."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content='["src/main.py", "nonexistent.py"]',
            )
        )

        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=mock_pipeline,
            tree_refresh_minutes=0,
        )

        await builder.refresh_tree("moltbook/api")
        selected = await builder.select_files("moltbook/api", "test")

        assert "src/main.py" in selected
        assert "nonexistent.py" not in selected

    @pytest.mark.asyncio
    async def test_fetch_files_caches(self, code_context_db, mock_github_client):
        """fetch_files caches content and reuses on second call."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )

        # Populate tree first so we have shas
        await builder.refresh_tree("moltbook/api")

        # First fetch — hits API
        files = await builder.fetch_files("moltbook/api", ["src/main.py"])
        assert len(files) == 1
        assert mock_github_client.get_file_content.call_count == 1

        # Cache the sha manually for the second call
        await code_context_db.cache_file("moltbook/api", "src/main.py", "sha1", "cached content")

        # Reset mock
        mock_github_client.get_file_content.reset_mock()

        # Second fetch — should hit cache (sha1 from tree matches)
        files2 = await builder.fetch_files("moltbook/api", ["src/main.py"])
        assert len(files2) == 1
        assert mock_github_client.get_file_content.call_count == 0

    @pytest.mark.asyncio
    async def test_build_context_orchestrates(self, code_context_db, mock_github_client):
        """build_context orchestrates tree refresh, file selection, and fetch."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content='["src/main.py"]',
            )
        )

        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=mock_pipeline,
            tree_refresh_minutes=0,
        )

        context = await builder.build_context("moltbook/api", "What does main do?")

        assert context.repo == "moltbook/api"
        assert context.question == "What does main do?"
        assert len(context.files) >= 1

    @pytest.mark.asyncio
    async def test_refresh_tree_skips_when_cache_fresh(self, code_context_db, mock_github_client):
        """refresh_tree returns False when cache is still fresh."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=9999,  # Very long refresh window
        )

        # First refresh populates cache
        refreshed = await builder.refresh_tree("moltbook/api")
        assert refreshed is True

        # Manually set a timezone-aware ISO timestamp so the age check works
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await code_context_db._db.execute(
            "UPDATE repo_tree_meta SET last_refreshed = ? WHERE repo = ?",
            (now_iso, "moltbook/api"),
        )

        # Second refresh — cache is fresh, should skip
        refreshed = await builder.refresh_tree("moltbook/api")
        assert refreshed is False

    @pytest.mark.asyncio
    async def test_refresh_tree_returns_false_on_api_error(self, code_context_db):
        """refresh_tree returns False when API call fails."""
        client = AsyncMock()
        client.get_file_tree = AsyncMock(side_effect=Exception("network error"))

        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )

        refreshed = await builder.refresh_tree("moltbook/api")
        assert refreshed is False

    @pytest.mark.asyncio
    async def test_refresh_tree_skips_directories(self, code_context_db):
        """refresh_tree only processes blob items, skipping trees."""
        client = AsyncMock()
        client.get_file_tree = AsyncMock(return_value={
            "sha": "new_sha",
            "tree": [
                {"path": "src", "type": "tree", "sha": "dir_sha", "size": 0},
                {"path": "src/main.py", "type": "blob", "sha": "file_sha", "size": 100},
            ],
        })

        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )

        await builder.refresh_tree("test/repo")
        paths = await code_context_db.get_tree_paths("test/repo")
        assert "src/main.py" in paths
        assert "src" not in paths

    @pytest.mark.asyncio
    async def test_refresh_tree_skips_non_matching_files(self, code_context_db):
        """refresh_tree skips files that don't match include patterns."""
        client = AsyncMock()
        client.get_file_tree = AsyncMock(return_value={
            "sha": "new_sha",
            "tree": [
                {"path": "image.png", "type": "blob", "sha": "img_sha", "size": 100},
                {"path": "src/main.py", "type": "blob", "sha": "py_sha", "size": 100},
            ],
        })

        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )

        await builder.refresh_tree("test/repo")
        paths = await code_context_db.get_tree_paths("test/repo")
        assert "src/main.py" in paths
        assert "image.png" not in paths

    def test_should_return_empty_when_no_llm_pipeline(self):
        """select_files returns [] when no LLM pipeline is set."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
            llm_pipeline=None,
        )
        # Synchronous check — the actual test is async below

    @pytest.mark.asyncio
    async def test_select_files_returns_empty_without_llm(self):
        """select_files returns [] without an LLM pipeline."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=AsyncMock(),
            llm_pipeline=None,
        )
        result = await builder.select_files("test/repo", "question?")
        assert result == []

    @pytest.mark.asyncio
    async def test_select_files_returns_empty_without_tree(self, code_context_db):
        """select_files returns [] when tree cache is empty."""
        mock_pipeline = AsyncMock()
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=code_context_db,
            llm_pipeline=mock_pipeline,
        )
        result = await builder.select_files("empty/repo", "question?")
        assert result == []

    @pytest.mark.asyncio
    async def test_select_files_returns_empty_when_llm_blocked(self, code_context_db, mock_github_client):
        """select_files returns [] when LLM result is blocked."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="", blocked=True)
        )
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=mock_pipeline,
            tree_refresh_minutes=0,
        )
        await builder.refresh_tree("moltbook/api")
        result = await builder.select_files("moltbook/api", "test")
        assert result == []

    @pytest.mark.asyncio
    async def test_select_files_returns_empty_when_llm_returns_none(self, code_context_db, mock_github_client):
        """select_files returns [] when LLM returns None."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=None)
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=mock_pipeline,
            tree_refresh_minutes=0,
        )
        await builder.refresh_tree("moltbook/api")
        result = await builder.select_files("moltbook/api", "test")
        assert result == []

    @pytest.mark.asyncio
    async def test_select_files_returns_empty_on_exception(self, code_context_db, mock_github_client):
        """select_files returns [] when LLM raises an exception."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(side_effect=Exception("LLM error"))
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=mock_pipeline,
            tree_refresh_minutes=0,
        )
        await builder.refresh_tree("moltbook/api")
        result = await builder.select_files("moltbook/api", "test")
        assert result == []

    def test_parse_file_list_wrapped_invalid_json(self):
        """Parse wrapped JSON that contains invalid inner JSON."""
        raw = 'Some text [not valid json] end'
        result = CodeContextBuilder._parse_file_list(raw)
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_files_stops_at_context_limit(self, code_context_db, mock_github_client):
        """fetch_files stops fetching when context size limit is reached."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            max_context_chars=10,  # Very small limit
            tree_refresh_minutes=0,
        )
        await builder.refresh_tree("moltbook/api")

        # Cache first file with content larger than limit
        await code_context_db.cache_file("moltbook/api", "src/main.py", "sha1", "x" * 15)

        files = await builder.fetch_files("moltbook/api", ["src/main.py", "src/utils.py"])
        # First file should be fetched (from cache), second should be skipped due to limit
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_fetch_files_handles_api_error(self, code_context_db):
        """fetch_files skips files that fail to fetch."""
        client = AsyncMock()
        client.get_file_content = AsyncMock(side_effect=Exception("API error"))

        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        # No sha in tree cache, so it will try API
        files = await builder.fetch_files("test/repo", ["missing.py"])
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_fetch_files_skips_oversized(self, code_context_db):
        """fetch_files skips files larger than max_file_size."""
        client = AsyncMock()
        import base64
        large_content = base64.b64encode(("x" * 100).encode()).decode()
        client.get_file_content = AsyncMock(return_value={
            "content": large_content,
            "sha": "oversized_sha",
        })

        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            max_file_size=50,  # Very small limit
            tree_refresh_minutes=0,
        )
        files = await builder.fetch_files("test/repo", ["big.py"])
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_build_context_returns_empty_when_no_files_selected(self, code_context_db, mock_github_client):
        """build_context returns empty CodeContext when no files are selected."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            llm_pipeline=None,  # No LLM -> no file selection
            tree_refresh_minutes=0,
        )
        context = await builder.build_context("moltbook/api", "test?")
        assert context.repo == "moltbook/api"
        assert context.files == []

    @pytest.mark.asyncio
    async def test_build_repo_summary(self, code_context_db, mock_github_client):
        """build_repo_summary generates and caches summary."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        summary = await builder.build_repo_summary("moltbook/api")
        assert "moltbook/api" in summary
        assert "Total files" in summary

    @pytest.mark.asyncio
    async def test_build_repo_summary_returns_cached(self, code_context_db, mock_github_client):
        """build_repo_summary returns cached summary on second call."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        summary1 = await builder.build_repo_summary("moltbook/api")
        summary2 = await builder.build_repo_summary("moltbook/api")
        assert summary1 == summary2

    @pytest.mark.asyncio
    async def test_build_repo_summary_returns_empty_without_tree(self, code_context_db):
        """build_repo_summary returns empty string when no tree exists."""
        client = AsyncMock()
        client.get_file_tree = AsyncMock(return_value={"sha": "", "tree": []})
        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        summary = await builder.build_repo_summary("empty/repo")
        assert summary == ""

    @pytest.mark.asyncio
    async def test_build_repo_summary_detects_languages_and_dirs(self, code_context_db):
        """build_repo_summary detects primary language and directories."""
        client = AsyncMock()
        client.get_file_tree = AsyncMock(return_value={
            "sha": "sum_sha",
            "tree": [
                {"path": "src/main.py", "type": "blob", "sha": "a", "size": 100},
                {"path": "src/utils.py", "type": "blob", "sha": "b", "size": 100},
                {"path": "tests/test_main.py", "type": "blob", "sha": "c", "size": 100},
                {"path": "README.md", "type": "blob", "sha": "d", "size": 100},
                {"path": "pyproject.toml", "type": "blob", "sha": "e", "size": 100},
            ],
        })
        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        summary = await builder.build_repo_summary("lang/repo")
        assert ".py" in summary
        assert "src" in summary
        assert "tests" in summary
        assert "Key files" in summary
        assert "README.md" in summary

    @pytest.mark.asyncio
    async def test_build_repo_summary_no_extensions(self, code_context_db):
        """build_repo_summary handles files without extensions."""
        client = AsyncMock()
        client.get_file_tree = AsyncMock(return_value={
            "sha": "no_ext_sha",
            "tree": [
                {"path": "Makefile", "type": "blob", "sha": "a", "size": 100},
                {"path": "Dockerfile", "type": "blob", "sha": "b", "size": 100},
            ],
        })
        builder = CodeContextBuilder(
            client=client,
            db=code_context_db,
            tree_refresh_minutes=0,
            include_patterns=["*"],  # Accept everything
        )
        summary = await builder.build_repo_summary("noext/repo")
        assert "noext/repo" in summary
        assert "Key files" in summary

    @pytest.mark.asyncio
    async def test_get_cached_summary(self, code_context_db, mock_github_client):
        """get_cached_summary returns cached summary."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        # Build first to populate cache
        await builder.build_repo_summary("moltbook/api")

        cached = await builder.get_cached_summary("moltbook/api")
        assert "moltbook/api" in cached

    @pytest.mark.asyncio
    async def test_get_cached_summary_returns_empty_when_no_cache(self, code_context_db):
        """get_cached_summary returns empty string when nothing cached."""
        builder = CodeContextBuilder(
            client=AsyncMock(),
            db=code_context_db,
        )
        cached = await builder.get_cached_summary("nonexistent/repo")
        assert cached == ""

    @pytest.mark.asyncio
    async def test_refresh_tree_handles_bad_timestamp(self, code_context_db, mock_github_client):
        """refresh_tree handles invalid timestamps in meta gracefully."""
        builder = CodeContextBuilder(
            client=mock_github_client,
            db=code_context_db,
            tree_refresh_minutes=0,
        )
        # First refresh to populate meta
        await builder.refresh_tree("moltbook/api")
        # Manually corrupt the timestamp
        await code_context_db._db.execute(
            "UPDATE repo_tree_meta SET last_refreshed = 'bad-timestamp' WHERE repo = ?",
            ("moltbook/api",),
        )
        # Should handle gracefully and proceed to refresh
        refreshed = await builder.refresh_tree("moltbook/api")
        # Will try to fetch and sha matches, so returns False (unchanged)
        assert refreshed is False
