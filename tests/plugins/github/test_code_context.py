"""
Tests for the code context builder — file tree caching and targeted file fetch.
"""

import pytest
from unittest.mock import AsyncMock

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
            client=AsyncMock(), db=AsyncMock(),
        )
        assert builder._should_include("src/main.py") is True

    def test_should_include_yaml(self):
        """YAML files are included by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(), db=AsyncMock(),
        )
        assert builder._should_include("config/settings.yaml") is True

    def test_should_exclude_lock(self):
        """Lock files are excluded by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(), db=AsyncMock(),
        )
        assert builder._should_include("poetry.lock") is False

    def test_should_exclude_pycache(self):
        """__pycache__ is excluded by default."""
        builder = CodeContextBuilder(
            client=AsyncMock(), db=AsyncMock(),
        )
        assert builder._should_include("__pycache__/module.cpython-313.pyc") is False

    def test_should_exclude_non_matching(self):
        """Files not matching any include pattern are excluded."""
        builder = CodeContextBuilder(
            client=AsyncMock(), db=AsyncMock(),
        )
        assert builder._should_include("image.png") is False

    def test_custom_patterns(self):
        """Custom include/exclude patterns override defaults."""
        builder = CodeContextBuilder(
            client=AsyncMock(), db=AsyncMock(),
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
                CachedFile(repo="test/repo", path="utils.py", sha="def", content="def helper(): pass"),
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
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='["src/main.py", "src/utils.py"]',
        ))

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
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='["src/main.py", "nonexistent.py"]',
        ))

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
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='["src/main.py"]',
        ))

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
