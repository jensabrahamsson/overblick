"""
Tests for DreamCapability — integration with LLM pipeline and DB persistence.

Covers:
  - tick() generates and persists dreams
  - tick() skips before 06:00
  - tick() only one dream per day
  - tick() works without DB
  - LLM unavailable fallback still persists
  - get_prompt_context() returns insights
  - _load_dream_guidance() reads new YAML format
"""

import json
import pytest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.capabilities.psychology.dream_system import (
    DreamSystem, DreamType, DreamTone, Dream,
)
from overblick.capabilities.psychology.dream import (
    DreamCapability, _load_dream_guidance,
)
from overblick.core.capability import CapabilityContext


# -- Fixtures ----------------------------------------------------------------

def _make_ctx(
    identity_name: str = "cherry",
    llm_pipeline: object = None,
    engagement_db: object = None,
) -> CapabilityContext:
    """Build a minimal CapabilityContext for testing."""
    return CapabilityContext(
        identity_name=identity_name,
        data_dir=Path("/tmp/test_dream"),
        llm_pipeline=llm_pipeline,
        engagement_db=engagement_db,
        config={},
    )


def _mock_pipeline_result(content: str = "", blocked: bool = False) -> MagicMock:
    result = MagicMock()
    result.content = content
    result.blocked = blocked
    result.block_reason = None
    return result


def _valid_dream_json() -> str:
    return json.dumps({
        "content": "dreamed I was in a glass room...",
        "symbols": ["glass", "transparency", "masks"],
        "tone": "tender",
        "insight": "being seen is terrifying",
        "potential_learning": "vulnerability opens doors",
    })


# -- tick() tests -----------------------------------------------------------

class TestDreamCapabilityTick:
    @pytest.mark.asyncio
    async def test_tick_generates_and_persists(self):
        """Mock LLM + mock DB — verify save_dream called after tick."""
        pipeline = AsyncMock()
        pipeline.chat.return_value = _mock_pipeline_result(_valid_dream_json())

        db = AsyncMock()
        db.get_recent_dreams.return_value = []
        db.save_dream.return_value = 1

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        # Mock time to 08:00
        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        db.get_recent_dreams.assert_awaited_once()
        db.save_dream.assert_awaited_once()
        call_args = db.save_dream.call_args[0][0]
        assert call_args["content"] == "dreamed I was in a glass room..."
        assert call_args["tone"] == "tender"

    @pytest.mark.asyncio
    async def test_tick_skips_before_0600(self):
        """No dream generated at 05:00."""
        pipeline = AsyncMock()
        db = AsyncMock()

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 5, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        pipeline.chat.assert_not_awaited()
        db.save_dream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_skips_same_day(self):
        """Only one dream per day."""
        pipeline = AsyncMock()
        pipeline.chat.return_value = _mock_pipeline_result(_valid_dream_json())
        db = AsyncMock()
        db.get_recent_dreams.return_value = []
        db.save_dream.return_value = 1

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now

            await cap.tick()  # First tick — generates dream
            await cap.tick()  # Second tick — should skip

        assert pipeline.chat.await_count == 1
        assert db.save_dream.await_count == 1

    @pytest.mark.asyncio
    async def test_tick_no_db_still_works(self):
        """Dream generated but not persisted when DB is None."""
        pipeline = AsyncMock()
        pipeline.chat.return_value = _mock_pipeline_result(_valid_dream_json())

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=None)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        pipeline.chat.assert_awaited_once()
        assert len(cap.inner.recent_dreams) == 1

    @pytest.mark.asyncio
    async def test_tick_llm_unavailable_fallback(self):
        """Fallback dream still persisted when LLM fails."""
        pipeline = AsyncMock()
        pipeline.chat.side_effect = Exception("LLM down")

        db = AsyncMock()
        db.get_recent_dreams.return_value = []
        db.save_dream.return_value = 1

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        # Fallback dream should still be persisted
        db.save_dream.assert_awaited_once()
        assert len(cap.inner.recent_dreams) == 1

    @pytest.mark.asyncio
    async def test_tick_no_pipeline_no_db(self):
        """Tick with neither LLM nor DB — fallback dream in memory only."""
        ctx = _make_ctx(llm_pipeline=None, engagement_db=None)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        assert len(cap.inner.recent_dreams) == 1


# -- get_prompt_context() tests -----------------------------------------------

class TestDreamCapabilityPromptContext:
    @pytest.mark.asyncio
    async def test_get_prompt_context_from_recent_dreams(self):
        """Insights from generated dreams are returned as context."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()

        # Generate a fallback dream (no LLM)
        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        context = cap.get_prompt_context()
        assert "REFLECTIONS" in context

    @pytest.mark.asyncio
    async def test_get_prompt_context_empty_initially(self):
        """No context before any dreams are generated."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.get_prompt_context() == ""


# -- _load_dream_guidance() tests ---------------------------------------------

class TestLoadDreamGuidance:
    def test_returns_none_for_unknown_identity(self):
        result = _load_dream_guidance("nonexistent_identity_xyz")
        assert result is None

    def test_returns_guidance_for_anomal(self):
        result = _load_dream_guidance("anomal")
        assert result is not None
        assert "guidance" in result
        assert "weights" in result
        assert "identity_voice" in result

    def test_anomal_weights_sum_close_to_one(self):
        result = _load_dream_guidance("anomal")
        total = sum(result["weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_cherry_guidance_has_required_fields(self):
        result = _load_dream_guidance("cherry")
        assert result is not None
        for dream_type, guidance in result["guidance"].items():
            assert "themes" in guidance, f"Missing 'themes' in {dream_type}"
            assert "symbols" in guidance, f"Missing 'symbols' in {dream_type}"
            assert "tones" in guidance, f"Missing 'tones' in {dream_type}"
            assert "psychological_core" in guidance, f"Missing 'psychological_core' in {dream_type}"
            assert len(guidance["themes"]) > 0
            assert len(guidance["symbols"]) > 0

    def test_stal_all_types_loaded(self):
        result = _load_dream_guidance("stal")
        assert result is not None
        expected = {
            DreamType.EMPTY_CHAIR, DreamType.INFINITE_CABINET,
            DreamType.LETTERS_UNKNOWN_TONGUE, DreamType.CLOCK_TOWER_DISCORD,
            DreamType.REARRANGING_CORRIDORS, DreamType.EMPTY_TYPEWRITER,
        }
        loaded = set(result["guidance"].keys())
        assert expected == loaded

    def test_natt_all_types_loaded(self):
        result = _load_dream_guidance("natt")
        assert result is not None
        expected = {
            DreamType.OBSERVER_PARADOX, DreamType.GROUND_DISSOLVING,
            DreamType.LANGUAGE_LIMIT, DreamType.RECURSION_DREAM,
            DreamType.SILENCE_SPEAKING,
        }
        loaded = set(result["guidance"].keys())
        assert expected == loaded

    def test_identity_voice_present(self):
        """Identity voice section is loaded for all dream identities."""
        for identity in ["anomal", "cherry", "stal", "natt"]:
            result = _load_dream_guidance(identity)
            assert result is not None, f"No guidance for {identity}"
            voice = result["identity_voice"]
            assert "style" in voice, f"Missing 'style' in {identity}"
            assert "perspective" in voice, f"Missing 'perspective' in {identity}"


# -- DreamCapability setup ---------------------------------------------------

class TestDreamCapabilitySetup:
    @pytest.mark.asyncio
    async def test_anomal_loads_identity_guidance(self):
        """Anomal's dream_content.yaml is loaded with Jungian types."""
        ctx = _make_ctx(identity_name="anomal")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert DreamType.SHADOW_INTEGRATION in cap.inner._guidance
        assert DreamType.PATTERN_RECOGNITION in cap.inner._guidance
        assert DreamType.INTELLECTUAL_SYNTHESIS in cap.inner._guidance

    @pytest.mark.asyncio
    async def test_cherry_loads_identity_guidance(self):
        """Cherry's dream_content.yaml is loaded with relational types."""
        ctx = _make_ctx(identity_name="cherry")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert DreamType.VULNERABILITY_EXPOSURE in cap.inner._guidance
        assert DreamType.AI_EXISTENCE_DENIAL in cap.inner._guidance
        assert DreamType.CONNECTION_LONGING in cap.inner._guidance

    @pytest.mark.asyncio
    async def test_unknown_identity_uses_generic_defaults(self):
        """Identity without dream_content.yaml falls back to defaults."""
        ctx = _make_ctx(identity_name="generic_test_identity")
        cap = DreamCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert DreamType.INTELLECTUAL_SYNTHESIS in cap.inner._guidance

    @pytest.mark.asyncio
    async def test_generate_dream_method(self):
        """The public generate_dream() method works."""
        pipeline = AsyncMock()
        pipeline.chat.return_value = _mock_pipeline_result(_valid_dream_json())

        ctx = _make_ctx(llm_pipeline=pipeline)
        cap = DreamCapability(ctx)
        await cap.setup()

        dream = await cap.generate_dream(recent_topics=["attachment theory"])
        assert dream is not None
        assert isinstance(dream, Dream)
