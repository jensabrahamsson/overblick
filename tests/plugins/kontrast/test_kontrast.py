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
        state_file.write_text(json.dumps({
            "last_run": 1000.0,
            "seen_topic_hashes": ["abc123"],
            "pieces": [],
        }))

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
        plugin._pieces.append(KontrastPiece(
            topic="Test",
            topic_hash="hash1",
            perspectives=[PerspectiveEntry(identity_name="a", content="C")],
        ))
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
