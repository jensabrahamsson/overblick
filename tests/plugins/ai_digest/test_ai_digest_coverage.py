"""
Additional AiDigestPlugin tests to achieve 100% line coverage.

Covers:
- Secret read exception during setup (lines 103-104)
- Full tick pipeline: mark sent, fetch, rank, generate, send, exception (150-177)
- Feed entry without published_parsed (203)
- Ranking without LLM pipeline (244-245)
- Parse selection exception handler (302-305)
- Generation without LLM pipeline (311-312)
- Successful generation returning content (358)
- _build_digest_prompt FileNotFoundError fallback (441-442)
- _load_state exception on corrupt file (462-463)
- _save_state exception on write failure (477-478)
"""

import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.ai_digest.plugin import AiDigestPlugin, FeedArticle

# ---------------------------------------------------------------------------
# Setup: secret read exception — lines 103-104
# ---------------------------------------------------------------------------


class TestSetupSecretException:
    """Test setup() when get_secret raises an exception."""

    @pytest.mark.asyncio
    async def test_setup_handles_secret_read_exception(self, ai_digest_context):
        """When get_secret throws, falls back to config recipient."""
        ai_digest_context._secrets_getter = MagicMock(side_effect=RuntimeError("Secrets unavailable"))
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()
        # Falls back to config recipient
        assert plugin._recipient == "test@example.com"


# ---------------------------------------------------------------------------
# Full tick pipeline — lines 150-177
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="ZoneInfo('Europe/Stockholm') requires tzdata package on Windows",
)
class TestTickPipeline:
    """Test tick() running the full digest pipeline."""

    @pytest.mark.asyncio
    async def test_tick_runs_full_pipeline(self, ai_digest_context):
        """When it is digest time, tick runs: mark, fetch, rank, generate, send."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        articles = [
            FeedArticle(title=f"Article {i}", link=f"https://example.com/{i}", feed_name="Feed")
            for i in range(3)
        ]

        # Mock _chat_with_overrides for ranking and generation
        ai_digest_context.llm_pipeline._chat_with_overrides = AsyncMock(
            side_effect=[
                PipelineResult(content="[1, 2, 3]"),  # ranking
                PipelineResult(content="<h1>Digest</h1>Good morning!"),  # generation
            ]
        )

        with (
            patch.object(plugin, "_is_digest_time", return_value=True),
            patch.object(
                plugin, "_fetch_all_feeds", new_callable=AsyncMock, return_value=articles
            ),
            patch.object(plugin, "_send_digest", new_callable=AsyncMock) as mock_send,
            patch.object(plugin, "_save_state"),
        ):
            await plugin.tick()
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_no_articles_skips(self, ai_digest_context):
        """When no articles fetched, digest is not generated."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        with (
            patch.object(plugin, "_is_digest_time", return_value=True),
            patch.object(
                plugin, "_fetch_all_feeds", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(plugin, "_save_state"),
        ):
            await plugin.tick()
            ai_digest_context.llm_pipeline._chat_with_overrides.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_empty_ranking_skips(self, ai_digest_context):
        """When ranking returns empty, digest is not generated."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        articles = [FeedArticle(title="Art", link="https://x.com/1")]

        with (
            patch.object(plugin, "_is_digest_time", return_value=True),
            patch.object(
                plugin, "_fetch_all_feeds", new_callable=AsyncMock, return_value=articles
            ),
            patch.object(plugin, "_rank_articles", new_callable=AsyncMock, return_value=[]),
            patch.object(plugin, "_save_state"),
        ):
            await plugin.tick()

    @pytest.mark.asyncio
    async def test_tick_empty_generation_skips(self, ai_digest_context):
        """When generation returns None, digest is not sent."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        articles = [FeedArticle(title="Art", link="https://x.com/1")]

        with (
            patch.object(plugin, "_is_digest_time", return_value=True),
            patch.object(
                plugin, "_fetch_all_feeds", new_callable=AsyncMock, return_value=articles
            ),
            patch.object(
                plugin, "_rank_articles", new_callable=AsyncMock, return_value=articles
            ),
            patch.object(
                plugin, "_generate_digest", new_callable=AsyncMock, return_value=None
            ),
            patch.object(plugin, "_send_digest", new_callable=AsyncMock) as mock_send,
            patch.object(plugin, "_save_state"),
        ):
            await plugin.tick()
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_pipeline_exception_caught(self, ai_digest_context):
        """Pipeline exceptions are caught and logged, not raised."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        with (
            patch.object(plugin, "_is_digest_time", return_value=True),
            patch.object(
                plugin,
                "_fetch_all_feeds",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Feed crash"),
            ),
            patch.object(plugin, "_save_state"),
        ):
            # Should not raise
            await plugin.tick()


# ---------------------------------------------------------------------------
# Feed entry without published_parsed — line 203
# ---------------------------------------------------------------------------


class TestFeedNoParsedDate:
    """Test feed entries without published_parsed field."""

    @pytest.mark.asyncio
    async def test_entry_without_published_parsed_uses_now(self, ai_digest_context):
        """Articles without published_parsed use current time."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        mock_entry = MagicMock()
        mock_entry.get = lambda k, d=None: {
            "title": "No Date Article",
            "link": "https://example.com/nodate",
            "summary": "Article without date",
            "published_parsed": None,
        }.get(k, d)

        mock_feed = MagicMock()
        mock_feed.feed.get = lambda k, d=None: {"title": "Test Feed"}.get(k, d)
        mock_feed.entries = [mock_entry]

        with patch("overblick.plugins.ai_digest.plugin.feedparser.parse", return_value=mock_feed):
            articles = await plugin._fetch_all_feeds()
            assert len(articles) == 2  # 2 feeds, 1 entry each
            for a in articles:
                # Timestamp should be close to now (within last few seconds)
                assert time.time() - a.timestamp < 10


# ---------------------------------------------------------------------------
# Ranking without LLM pipeline — lines 244-245
# ---------------------------------------------------------------------------


class TestRankingNoLLM:
    """Test _rank_articles without LLM pipeline."""

    @pytest.mark.asyncio
    async def test_rank_without_pipeline_returns_first_n(self, ai_digest_context):
        """Falls back to first top_n articles when no pipeline."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()
        plugin.ctx.llm_pipeline = None

        articles = [
            FeedArticle(title=f"Article {i}", link=f"https://example.com/{i}") for i in range(10)
        ]
        result = await plugin._rank_articles(articles)
        assert len(result) == 5  # top_n = 5
        assert result[0].title == "Article 0"


# ---------------------------------------------------------------------------
# Parse selection exception — lines 302-305
# ---------------------------------------------------------------------------


class TestParseSelectionException:
    """Test _rank_articles when _parse_selection raises an exception."""

    @pytest.mark.asyncio
    async def test_rank_parse_exception_falls_back(self, ai_digest_context):
        """When _parse_selection raises, falls back to first top_n."""
        ai_digest_context.llm_pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(content="not valid json at all")
        )
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        articles = [
            FeedArticle(title=f"Article {i}", link=f"https://example.com/{i}") for i in range(10)
        ]
        result = await plugin._rank_articles(articles)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_rank_empty_parse_falls_back(self, ai_digest_context):
        """When _parse_selection returns empty list, falls back to first top_n."""
        # Return valid JSON with all out-of-range indices
        ai_digest_context.llm_pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(content="[0, -1, 999]")
        )
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        articles = [
            FeedArticle(title=f"Article {i}", link=f"https://example.com/{i}") for i in range(3)
        ]
        result = await plugin._rank_articles(articles)
        assert len(result) == 3  # top_n=5 but only 3 articles


# ---------------------------------------------------------------------------
# Generation without LLM pipeline — lines 311-312
# ---------------------------------------------------------------------------


class TestGenerationNoLLM:
    """Test _generate_digest without LLM pipeline."""

    @pytest.mark.asyncio
    async def test_generate_without_pipeline_returns_none(self, ai_digest_context):
        """Returns None when no pipeline available."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()
        plugin.ctx.llm_pipeline = None

        articles = [FeedArticle(title="Test", link="https://example.com/1")]
        result = await plugin._generate_digest(articles)
        assert result is None


# ---------------------------------------------------------------------------
# Successful generation returning content — line 358
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="ZoneInfo('Europe/Stockholm') requires tzdata package on Windows",
)
class TestSuccessfulGeneration:
    """Test _generate_digest returns content on success."""

    @pytest.mark.asyncio
    async def test_generate_returns_content(self, ai_digest_context):
        """Returns generated content on success."""
        ai_digest_context.llm_pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(content="<h1>Daily Digest</h1>\nGreat news today!")
        )
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()

        articles = [
            FeedArticle(
                title="Test Article",
                link="https://example.com/1",
                summary="A great article",
                feed_name="Test Feed",
            )
        ]
        result = await plugin._generate_digest(articles)
        assert result == "<h1>Daily Digest</h1>\nGreat news today!"


# ---------------------------------------------------------------------------
# _build_digest_prompt FileNotFoundError — lines 441-442
# ---------------------------------------------------------------------------


class TestDigestPromptFallback:
    """Test _build_digest_prompt when personality file is missing."""

    @pytest.mark.asyncio
    async def test_prompt_fallback_on_missing_personality(self, ai_digest_context):
        """Returns fallback prompt when load_identity raises FileNotFoundError."""
        plugin = AiDigestPlugin(ai_digest_context)
        with patch(
            "overblick.identities.load_identity", side_effect=FileNotFoundError("not found")
        ):
            result = plugin._build_digest_prompt("nonexistent_personality")
        assert "AI news curator" in result
        assert "digest" in result.lower()

    @pytest.mark.asyncio
    async def test_prompt_success_path(self, ai_digest_context):
        """Returns composed prompt when load_identity succeeds."""
        plugin = AiDigestPlugin(ai_digest_context)
        mock_personality = MagicMock()
        with (
            patch(
                "overblick.identities.load_identity", return_value=mock_personality
            ),
            patch(
                "overblick.identities.build_system_prompt",
                return_value="You are Anomal, a tech journalist.",
            ),
        ):
            result = plugin._build_digest_prompt("anomal")
        assert "Anomal" in result
        assert "AI news digest" in result


# ---------------------------------------------------------------------------
# _load_state exception — lines 462-463
# ---------------------------------------------------------------------------


class TestLoadStateException:
    """Test _load_state with corrupt state file."""

    @pytest.mark.asyncio
    async def test_load_state_handles_corrupt_file(self, ai_digest_context):
        """Corrupt state file is handled gracefully (state stays None)."""
        state_file = ai_digest_context.data_dir / "ai_digest_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("not valid json {{{")

        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()
        # Should not crash, and last_digest_date should be None
        assert plugin._last_digest_date is None


# ---------------------------------------------------------------------------
# _save_state exception — lines 477-478
# ---------------------------------------------------------------------------


class TestSaveStateException:
    """Test _save_state when write fails."""

    @pytest.mark.asyncio
    async def test_save_state_handles_write_failure(self, ai_digest_context):
        """Write failure during _save_state is handled gracefully."""
        plugin = AiDigestPlugin(ai_digest_context)
        await plugin.setup()
        plugin._last_digest_date = "2026-03-15"

        # Make the state file a directory to cause write failure
        state_file = ai_digest_context.data_dir / "ai_digest_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.mkdir(exist_ok=True)

        # Should not raise
        plugin._save_state()
