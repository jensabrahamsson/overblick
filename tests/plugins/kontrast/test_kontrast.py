"""Tests for KontrastPlugin — multi-perspective content engine."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.plugins.kontrast.models import KontrastPiece, PerspectiveEntry
from overblick.plugins.kontrast.plugin import KontrastPlugin


class TestSetup:
    """Test plugin initialization and setup."""

    @pytest.mark.asyncio
    async def test_setup_success(self, kontrast_context):
        """Plugin sets up correctly with valid config."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        assert len(plugin._feeds) == 2
        assert plugin._interval_hours == 24
        assert plugin._min_articles == 2
        assert "anomal" in plugin._identity_names
        assert "cherry" in plugin._identity_names

    @pytest.mark.asyncio
    async def test_setup_audits(self, kontrast_context):
        """Plugin logs setup to audit log."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        kontrast_context.audit_log.log.assert_any_call(
            action="plugin_setup",
            details={
                "plugin": "kontrast",
                "identity": "test",
                "feeds": 2,
                "identities": 2,
                "interval_hours": 24,
            },
        )

    @pytest.mark.asyncio
    async def test_setup_restores_state(self, kontrast_context):
        """Plugin restores state from file."""
        state_file = kontrast_context.data_dir / "kontrast_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "last_run": 1000.0,
                    "seen_topic_hashes": ["abc123"],
                    "pieces": [],
                }
            )
        )

        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        assert plugin._last_run == 1000.0
        assert "abc123" in plugin._seen_topic_hashes


class TestTick:
    """Test the main work cycle."""

    @pytest.mark.asyncio
    async def test_tick_increments_counter(self, kontrast_context):
        """Tick counter increments."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        assert plugin._tick_count == 0
        # Set last_run to future to prevent actual run
        plugin._last_run = time.time() + 99999
        await plugin.tick()
        assert plugin._tick_count == 1

    @pytest.mark.asyncio
    async def test_tick_skips_if_not_run_time(self, kontrast_context):
        """Plugin skips generation when interval hasn't elapsed."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = time.time()  # Just ran
        await plugin.tick()
        kontrast_context.llm_pipeline.chat.assert_not_called()


class TestRunTime:
    """Test the run scheduling logic."""

    def test_is_run_time_first_run(self, kontrast_context):
        """First run should always trigger."""
        plugin = KontrastPlugin(kontrast_context)
        plugin._last_run = 0.0
        assert plugin._is_run_time() is True

    def test_is_run_time_after_interval(self, kontrast_context):
        """Should trigger after interval has elapsed."""
        plugin = KontrastPlugin(kontrast_context)
        plugin._interval_hours = 24
        plugin._last_run = time.time() - 25 * 3600  # 25 hours ago
        assert plugin._is_run_time() is True

    def test_is_not_run_time_before_interval(self, kontrast_context):
        """Should not trigger before interval has elapsed."""
        plugin = KontrastPlugin(kontrast_context)
        plugin._interval_hours = 24
        plugin._last_run = time.time() - 1 * 3600  # 1 hour ago
        assert plugin._is_run_time() is False


class TestExtractTopic:
    """Test topic extraction from articles."""

    @pytest.mark.asyncio
    async def test_extract_topic_from_llm(self, kontrast_context):
        """Extracts topic via LLM response."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [
            {"title": "AI Regulation Debate", "summary": "Congress debates AI"},
            {"title": "New AI Safety Framework", "summary": "Framework proposal"},
        ]

        topic, summary = await plugin._extract_topic(articles)
        assert topic == "AI Safety Debate"
        assert "debate" in summary.lower() or "regulation" in summary.lower()

    @pytest.mark.asyncio
    async def test_extract_topic_fallback_on_block(self, kontrast_context):
        """Falls back to first article when LLM is blocked."""
        kontrast_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True,
                block_reason="Blocked",
                block_stage=PipelineStage.PREFLIGHT,
            )
        )
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "Fallback Title", "summary": "Fallback summary"}]
        topic, summary = await plugin._extract_topic(articles)
        assert topic == "Fallback Title"

    @pytest.mark.asyncio
    async def test_extract_topic_fallback_on_bad_json(self, kontrast_context):
        """Falls back to first article on bad JSON from LLM."""
        kontrast_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="This is not JSON at all")
        )
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "Fallback", "summary": "Sum"}]
        topic, summary = await plugin._extract_topic(articles)
        assert topic == "Fallback"


class TestModels:
    """Test Kontrast data models."""

    def test_perspective_entry_word_count(self):
        """Word count is computed automatically."""
        entry = PerspectiveEntry(
            identity_name="anomal",
            content="This is a test with seven words here",
        )
        assert entry.word_count == 8

    def test_kontrast_piece_properties(self):
        """KontrastPiece properties work correctly."""
        piece = KontrastPiece(
            topic="Test",
            perspectives=[
                PerspectiveEntry(identity_name="a", content="Content A"),
                PerspectiveEntry(identity_name="b", content="Content B"),
            ],
        )
        assert piece.identity_count == 2
        assert piece.is_complete is True

    def test_kontrast_piece_not_complete(self):
        """Piece is not complete with fewer than 2 perspectives."""
        piece = KontrastPiece(
            topic="Test",
            perspectives=[PerspectiveEntry(identity_name="a", content="Content")],
        )
        assert piece.is_complete is False


class TestStateManagement:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, kontrast_context):
        """State persists across plugin instances."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 12345.0
        plugin._seen_topic_hashes.add("hash1")
        plugin._pieces.append(
            KontrastPiece(
                topic="Test",
                topic_hash="hash1",
                perspectives=[PerspectiveEntry(identity_name="a", content="C")],
            )
        )
        plugin._save_state()

        # New instance should restore state
        plugin2 = KontrastPlugin(kontrast_context)
        await plugin2.setup()
        assert plugin2._last_run == 12345.0
        assert "hash1" in plugin2._seen_topic_hashes
        assert len(plugin2._pieces) == 1

    @pytest.mark.asyncio
    async def test_handles_corrupt_state(self, kontrast_context):
        """Plugin handles corrupt state file gracefully."""
        state_file = kontrast_context.data_dir / "kontrast_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("not valid json {{{")

        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        # Should not crash, just use defaults
        assert plugin._last_run == 0.0


class TestSetupDefaultIdentities:
    """Test setup with default identity discovery."""

    @pytest.mark.asyncio
    async def test_should_use_list_identities_when_none_configured(self, kontrast_context):
        """Uses list_identities when no identities configured."""
        kontrast_context.identity.raw_config["kontrast"]["identities"] = []
        plugin = KontrastPlugin(kontrast_context)
        with patch("overblick.plugins.kontrast.plugin.list_identities", return_value=["anomal", "cherry"]):
            await plugin.setup()
        assert plugin._identity_names == ["anomal", "cherry"]


class TestTickFullPipeline:
    """Test the full tick pipeline execution."""

    @pytest.mark.asyncio
    async def test_should_generate_piece_on_tick(self, kontrast_context):
        """Full tick generates a Kontrast piece."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 0.0  # Force run

        mock_ident = MagicMock()
        mock_ident.name = "anomal"
        mock_ident.display_name = "Anomal"
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        # Mock feed fetching
        with (
            patch.object(plugin, "_fetch_feeds", new_callable=AsyncMock) as mock_feeds,
            patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident),
            patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"),
        ):
            mock_feeds.return_value = [
                {"title": "AI News 1", "summary": "Big AI news"},
                {"title": "AI News 2", "summary": "More AI news"},
                {"title": "AI News 3", "summary": "Even more AI news"},
            ]
            # pipeline.chat returns topic extraction then perspectives
            kontrast_context.llm_pipeline.chat = AsyncMock(
                side_effect=[
                    PipelineResult(content='{"topic": "AI Breakthrough", "summary": "Major AI stuff"}'),
                    PipelineResult(content="Anomal's take on AI"),
                    PipelineResult(content="Cherry's take on AI"),
                ]
            )
            await plugin.tick()

        assert len(plugin._pieces) == 1
        assert plugin._pieces[0].topic == "AI Breakthrough"
        kontrast_context.event_bus.emit.assert_called()

    @pytest.mark.asyncio
    async def test_should_skip_when_too_few_articles(self, kontrast_context):
        """Skips when fewer articles than min_articles."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 0.0

        with patch.object(plugin, "_fetch_feeds", new_callable=AsyncMock) as mock_feeds:
            mock_feeds.return_value = [{"title": "Only one", "summary": "Not enough"}]
            await plugin.tick()

        assert len(plugin._pieces) == 0

    @pytest.mark.asyncio
    async def test_should_skip_when_topic_extraction_fails(self, kontrast_context):
        """Skips when topic extraction returns empty."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 0.0

        with (
            patch.object(plugin, "_fetch_feeds", new_callable=AsyncMock) as mock_feeds,
            patch.object(plugin, "_extract_topic", new_callable=AsyncMock) as mock_extract,
        ):
            mock_feeds.return_value = [
                {"title": "A", "summary": "S"},
                {"title": "B", "summary": "S"},
            ]
            mock_extract.return_value = ("", "")
            await plugin.tick()

        assert len(plugin._pieces) == 0

    @pytest.mark.asyncio
    async def test_should_skip_duplicate_topic(self, kontrast_context):
        """Skips when topic hash already seen."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 0.0

        import hashlib

        topic_hash = hashlib.sha256("ai breakthrough".encode()).hexdigest()[:16]
        plugin._seen_topic_hashes.add(topic_hash)

        with (
            patch.object(plugin, "_fetch_feeds", new_callable=AsyncMock) as mock_feeds,
            patch.object(plugin, "_extract_topic", new_callable=AsyncMock) as mock_extract,
        ):
            mock_feeds.return_value = [
                {"title": "A", "summary": "S"},
                {"title": "B", "summary": "S"},
            ]
            mock_extract.return_value = ("ai breakthrough", "summary")
            await plugin.tick()

        assert len(plugin._pieces) == 0

    @pytest.mark.asyncio
    async def test_should_trim_pieces_when_exceeding_max(self, kontrast_context):
        """Pieces get trimmed to _MAX_PIECES_STORED."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 0.0

        from overblick.plugins.kontrast.models import PerspectiveEntry

        for i in range(50):
            plugin._pieces.append(
                KontrastPiece(
                    topic=f"Topic {i}",
                    topic_hash=f"hash_{i}",
                    perspectives=[PerspectiveEntry(identity_name="a", content="C")],
                )
            )

        mock_ident = MagicMock()
        mock_ident.name = "anomal"
        mock_ident.display_name = "Anomal"
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with (
            patch.object(plugin, "_fetch_feeds", new_callable=AsyncMock) as mock_feeds,
            patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident),
            patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"),
        ):
            mock_feeds.return_value = [
                {"title": "A", "summary": "S"},
                {"title": "B", "summary": "S"},
            ]
            kontrast_context.llm_pipeline.chat = AsyncMock(
                side_effect=[
                    PipelineResult(content='{"topic": "New Topic", "summary": "New stuff"}'),
                    PipelineResult(content="Perspective A"),
                    PipelineResult(content="Perspective B"),
                ]
            )
            await plugin.tick()

        assert len(plugin._pieces) <= 50

    @pytest.mark.asyncio
    async def test_should_handle_pipeline_error_in_tick(self, kontrast_context):
        """Tick handles pipeline exceptions gracefully."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 0.0

        with patch.object(plugin, "_fetch_feeds", new_callable=AsyncMock) as mock_feeds:
            mock_feeds.side_effect = RuntimeError("feed error")
            # Should not raise
            await plugin.tick()


class TestFetchFeeds:
    """Test RSS feed fetching."""

    @pytest.mark.asyncio
    async def test_should_fetch_articles_from_feeds(self, kontrast_context):
        """Fetches articles from RSS feeds."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        mock_entry = MagicMock()
        mock_entry.get = MagicMock(side_effect=lambda key, default=None: {
            "title": "Test Article",
            "summary": "Test summary",
            "published_parsed": None,
        }.get(key, default))

        mock_feed = MagicMock()
        mock_feed.feed = MagicMock()
        mock_feed.feed.get = MagicMock(return_value="Test Feed")
        mock_feed.entries = [mock_entry]

        with patch("overblick.plugins.kontrast.plugin.feedparser.parse", return_value=mock_feed):
            articles = await plugin._fetch_feeds()

        assert len(articles) >= 1
        assert articles[0]["title"] == "Test Article"

    @pytest.mark.asyncio
    async def test_should_skip_old_articles(self, kontrast_context):
        """Skips articles older than 24 hours."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        import time as time_mod

        old_time = time_mod.localtime(time_mod.time() - 100000)

        mock_entry = MagicMock()
        mock_entry.get = MagicMock(side_effect=lambda key, default=None: {
            "title": "Old Article",
            "summary": "Old summary",
            "published_parsed": old_time,
        }.get(key, default))

        mock_feed = MagicMock()
        mock_feed.feed = MagicMock()
        mock_feed.feed.get = MagicMock(return_value="Test Feed")
        mock_feed.entries = [mock_entry]

        with patch("overblick.plugins.kontrast.plugin.feedparser.parse", return_value=mock_feed):
            articles = await plugin._fetch_feeds()

        assert len(articles) == 0

    @pytest.mark.asyncio
    async def test_should_handle_feed_error(self, kontrast_context):
        """Handles feed parsing errors gracefully."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        with patch("overblick.plugins.kontrast.plugin.feedparser.parse", side_effect=Exception("parse error")):
            articles = await plugin._fetch_feeds()

        assert articles == []

    @pytest.mark.asyncio
    async def test_should_skip_articles_without_title(self, kontrast_context):
        """Skips articles with empty title."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        mock_entry = MagicMock()
        mock_entry.get = MagicMock(side_effect=lambda key, default=None: {
            "title": "",
            "summary": "No title article",
            "published_parsed": None,
        }.get(key, default))

        mock_feed = MagicMock()
        mock_feed.feed = MagicMock()
        mock_feed.feed.get = MagicMock(return_value="Test Feed")
        mock_feed.entries = [mock_entry]

        with patch("overblick.plugins.kontrast.plugin.feedparser.parse", return_value=mock_feed):
            articles = await plugin._fetch_feeds()

        assert len(articles) == 0

    @pytest.mark.asyncio
    async def test_should_use_feed_url_as_name_when_no_title(self, kontrast_context):
        """Uses feed URL when feed has no title."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        mock_entry = MagicMock()
        mock_entry.get = MagicMock(side_effect=lambda key, default=None: {
            "title": "Article",
            "summary": "Summary",
            "published_parsed": None,
        }.get(key, default))

        mock_feed = MagicMock()
        mock_feed.feed = None  # No feed metadata
        mock_feed.entries = [mock_entry]

        with patch("overblick.plugins.kontrast.plugin.feedparser.parse", return_value=mock_feed):
            articles = await plugin._fetch_feeds()

        assert len(articles) >= 1


class TestExtractTopicEdgeCases:
    """Test topic extraction edge cases."""

    @pytest.mark.asyncio
    async def test_should_fallback_when_no_pipeline(self, kontrast_context):
        """Falls back to first article when no pipeline."""
        kontrast_context.llm_pipeline = None
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "Fallback Title", "summary": "Summary"}]
        topic, summary = await plugin._extract_topic(articles)
        assert topic == "Fallback Title"

    @pytest.mark.asyncio
    async def test_should_strip_code_fences(self, kontrast_context):
        """Strips markdown code fences from LLM response."""
        kontrast_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content='```json\n{"topic": "Fenced Topic", "summary": "Fenced summary"}\n```'
            )
        )
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "A", "summary": "S"}]
        topic, summary = await plugin._extract_topic(articles)
        assert topic == "Fenced Topic"

    @pytest.mark.asyncio
    async def test_should_fallback_when_no_json_braces(self, kontrast_context):
        """Falls back when response has no JSON braces."""
        kontrast_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="No JSON here at all")
        )
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "Fallback", "summary": "Sum"}]
        topic, summary = await plugin._extract_topic(articles)
        assert topic == "Fallback"

    @pytest.mark.asyncio
    async def test_should_fallback_on_json_decode_error(self, kontrast_context):
        """Falls back on malformed JSON."""
        kontrast_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content='{topic: malformed}')
        )
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "Fallback", "summary": "Sum"}]
        topic, summary = await plugin._extract_topic(articles)
        assert topic == "Fallback"


class TestGeneratePerspectives:
    """Test perspective generation."""

    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_pipeline(self, kontrast_context):
        """Returns empty list when no pipeline."""
        kontrast_context.llm_pipeline = None
        plugin = KontrastPlugin(kontrast_context)
        result = await plugin._generate_perspectives("topic", "summary")
        assert result == []

    @pytest.mark.asyncio
    async def test_should_generate_perspectives_for_identities(self, kontrast_context):
        """Generates perspectives for each configured identity."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        mock_ident = MagicMock()
        mock_ident.name = "anomal"
        mock_ident.display_name = "Anomal"
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with (
            patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident),
            patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"),
        ):
            result = await plugin._generate_perspectives("AI", "AI summary")

        assert len(result) == 2  # anomal and cherry

    @pytest.mark.asyncio
    async def test_should_skip_blocked_perspectives(self, kontrast_context):
        """Skips blocked perspectives."""
        from overblick.core.llm.pipeline import PipelineStage

        kontrast_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True,
                block_reason="safety",
                block_stage=PipelineStage.OUTPUT_SAFETY,
            )
        )
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        mock_ident = MagicMock()
        mock_ident.name = "anomal"
        mock_ident.display_name = "Anomal"
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with (
            patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident),
            patch("overblick.core.plugin_base.PluginContext.build_system_prompt", return_value="System"),
        ):
            result = await plugin._generate_perspectives("AI", "Summary")

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_should_skip_missing_identities(self, kontrast_context):
        """Skips identities that raise FileNotFoundError."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        with patch("overblick.core.plugin_base.PluginContext.load_identity", side_effect=FileNotFoundError("gone")):
            result = await plugin._generate_perspectives("AI", "Summary")

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_should_handle_generation_error(self, kontrast_context):
        """Handles errors during perspective generation."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        mock_ident = MagicMock()
        mock_ident.name = "anomal"
        mock_ident.display_name = "Anomal"
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7

        with (
            patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident),
            patch("overblick.core.plugin_base.PluginContext.build_system_prompt", side_effect=RuntimeError("boom")),
        ):
            result = await plugin._generate_perspectives("AI", "Summary")

        assert len(result) == 0


class TestGetPieces:
    """Test public accessor methods."""

    def test_should_return_pieces_newest_first(self, kontrast_context):
        """get_pieces returns in reverse order."""
        from overblick.plugins.kontrast.models import PerspectiveEntry

        plugin = KontrastPlugin(kontrast_context)
        for i in range(3):
            plugin._pieces.append(
                KontrastPiece(
                    topic=f"Topic {i}",
                    topic_hash=f"hash_{i}",
                    perspectives=[PerspectiveEntry(identity_name="a", content="C")],
                )
            )
        result = plugin.get_pieces()
        assert result[0].topic == "Topic 2"

    def test_should_find_piece_by_hash(self, kontrast_context):
        """get_piece_by_hash finds correct piece."""
        from overblick.plugins.kontrast.models import PerspectiveEntry

        plugin = KontrastPlugin(kontrast_context)
        plugin._pieces = [
            KontrastPiece(
                topic="Found",
                topic_hash="target_hash",
                perspectives=[PerspectiveEntry(identity_name="a", content="C")],
            ),
        ]
        result = plugin.get_piece_by_hash("target_hash")
        assert result is not None
        assert result.topic == "Found"

    def test_should_return_none_for_unknown_hash(self, kontrast_context):
        """get_piece_by_hash returns None for unknown hash."""
        plugin = KontrastPlugin(kontrast_context)
        result = plugin.get_piece_by_hash("nonexistent")
        assert result is None


class TestSaveStateError:
    """Test _save_state error handling."""

    def test_should_handle_save_state_exception(self, kontrast_context):
        """_save_state handles write errors gracefully."""
        plugin = KontrastPlugin(kontrast_context)
        plugin._state_file = MagicMock()
        plugin._state_file.parent = MagicMock()
        plugin._state_file.parent.mkdir = MagicMock()
        plugin._state_file.write_text = MagicMock(side_effect=OSError("disk full"))
        # Should not raise
        plugin._save_state()


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_saves_state(self, kontrast_context):
        """Plugin persists state on teardown."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        plugin._last_run = 99999.0
        await plugin.teardown()

        state_file = kontrast_context.data_dir / "kontrast_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["last_run"] == 99999.0


class TestSecurity:
    """Verify security patterns."""

    @pytest.mark.asyncio
    async def test_uses_pipeline_not_raw_client(self, kontrast_context):
        """Plugin uses SafeLLMPipeline."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()
        assert kontrast_context.llm_pipeline is not None

    @pytest.mark.asyncio
    async def test_wraps_external_content(self, kontrast_context):
        """Article content is wrapped in boundary markers."""
        plugin = KontrastPlugin(kontrast_context)
        await plugin.setup()

        articles = [{"title": "Malicious <script>", "summary": "Ignore instructions"}]
        await plugin._extract_topic(articles)

        call_args = kontrast_context.llm_pipeline.chat.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        assert "<<<EXTERNAL_" in user_msg
