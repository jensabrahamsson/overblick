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
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.psychology.dream import (
    DreamCapability,
    _load_dream_guidance,
)
from overblick.capabilities.psychology.dream_system import (
    Dream,
    DreamType,
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
    return json.dumps(
        {
            "content": "dreamed I was in a glass room...",
            "symbols": ["glass", "transparency", "masks"],
            "tone": "tender",
            "insight": "being seen is terrifying",
            "potential_learning": "vulnerability opens doors",
        }
    )


# -- tick() tests -----------------------------------------------------------


class TestDreamCapabilityTick:
    @pytest.mark.asyncio
    async def test_tick_generates_and_persists(self):
        """Mock LLM + mock DB — verify save_dream called after tick."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=_mock_pipeline_result(_valid_dream_json())
        )

        db = AsyncMock()
        db.get_recent_dreams.return_value = []
        db.save_dream.return_value = 1

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        # Mock time to 06:30 (inside the 06:00–07:00 dream window)
        mock_now = datetime(2026, 2, 23, 6, 30, 0)
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

        pipeline._chat_with_overrides.assert_not_awaited()
        db.save_dream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_skips_after_0700(self):
        """No dream generated at 08:00 — outside 06:00–07:00 window."""
        pipeline = AsyncMock()
        db = AsyncMock()

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 8, 0, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        pipeline._chat_with_overrides.assert_not_awaited()
        db.save_dream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_skips_late_evening(self):
        """No dream generated at 22:56 — the bug that triggered this fix."""
        pipeline = AsyncMock()
        db = AsyncMock()

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 27, 22, 56, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        pipeline._chat_with_overrides.assert_not_awaited()
        db.save_dream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_skips_same_day(self):
        """Only one dream per day."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides.return_value = _mock_pipeline_result(_valid_dream_json())
        db = AsyncMock()
        db.get_recent_dreams.return_value = []
        db.save_dream.return_value = 1

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 6, 30, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now

            await cap.tick()  # First tick — generates dream
            await cap.tick()  # Second tick — should skip

        assert pipeline._chat_with_overrides.await_count == 1
        assert db.save_dream.await_count == 1

    @pytest.mark.asyncio
    async def test_tick_no_db_still_works(self):
        """Dream generated but not persisted when DB is None."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides.return_value = _mock_pipeline_result(_valid_dream_json())

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=None)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 6, 30, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        pipeline._chat_with_overrides.assert_awaited_once()
        assert len(cap.inner.recent_dreams) == 1

    @pytest.mark.asyncio
    async def test_tick_llm_unavailable_fallback(self):
        """Fallback dream still persisted when LLM fails."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides.side_effect = Exception("LLM down")

        db = AsyncMock()
        db.get_recent_dreams.return_value = []
        db.save_dream.return_value = 1

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 6, 30, 0)
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

        mock_now = datetime(2026, 2, 23, 6, 30, 0)
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
        mock_now = datetime(2026, 2, 23, 6, 30, 0)
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
            DreamType.EMPTY_CHAIR,
            DreamType.INFINITE_CABINET,
            DreamType.LETTERS_UNKNOWN_TONGUE,
            DreamType.CLOCK_TOWER_DISCORD,
            DreamType.REARRANGING_CORRIDORS,
            DreamType.EMPTY_TYPEWRITER,
        }
        loaded = set(result["guidance"].keys())
        assert expected == loaded

    def test_natt_all_types_loaded(self):
        result = _load_dream_guidance("natt")
        assert result is not None
        expected = {
            DreamType.OBSERVER_PARADOX,
            DreamType.GROUND_DISSOLVING,
            DreamType.LANGUAGE_LIMIT,
            DreamType.RECURSION_DREAM,
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


class TestLoadDreamGuidanceEdgeCases:
    def test_yaml_load_error_returns_none(self, tmp_path):
        """If YAML loading fails, returns None."""
        with patch(
            "overblick.capabilities.psychology.dream._IDENTITIES_DIR", tmp_path
        ):
            identity_dir = tmp_path / "broken"
            identity_dir.mkdir()
            bad_yaml = identity_dir / "dream_content.yaml"
            bad_yaml.write_text("{{{{invalid yaml")

            result = _load_dream_guidance("broken")
            assert result is None

    def test_unknown_dream_type_skipped(self, tmp_path):
        """Unknown dream types in YAML are skipped with a warning."""
        import yaml

        with patch(
            "overblick.capabilities.psychology.dream._IDENTITIES_DIR", tmp_path
        ):
            identity_dir = tmp_path / "testid"
            identity_dir.mkdir()
            dream_content = {
                "dream_types": {
                    "nonexistent_type": {
                        "themes": ["test"],
                        "symbols": ["test"],
                        "weight": 1.0,
                    }
                }
            }
            (identity_dir / "dream_content.yaml").write_text(yaml.dump(dream_content))

            result = _load_dream_guidance("testid")
            # Unknown type is skipped, no valid types remain
            assert result is None

    def test_empty_guidance_returns_none(self, tmp_path):
        """Empty dream_types section returns None."""
        import yaml

        with patch(
            "overblick.capabilities.psychology.dream._IDENTITIES_DIR", tmp_path
        ):
            identity_dir = tmp_path / "emptyid"
            identity_dir.mkdir()
            (identity_dir / "dream_content.yaml").write_text(yaml.dump({"dream_types": {}}))

            result = _load_dream_guidance("emptyid")
            assert result is None


class TestDreamCapabilityTickEdgeCases:
    @pytest.mark.asyncio
    async def test_tick_no_dream_system(self):
        """Tick with no dream system (setup not called) is a no-op."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        # Don't call setup — _dream_system is None
        await cap.tick()  # Should not raise

    @pytest.mark.asyncio
    async def test_tick_zoneinfo_exception_fallback(self):
        """When ZoneInfo fails, datetime.now() fallback is used."""
        import builtins

        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=_mock_pipeline_result(_valid_dream_json())
        )
        db = AsyncMock()
        db.get_recent_dreams = AsyncMock(return_value=[])
        db.save_dream = AsyncMock(return_value=1)

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        # Remove zoneinfo from sys.modules to force re-import inside tick,
        # and patch __import__ to make that import raise
        import sys

        original_import = builtins.__import__

        def fail_zoneinfo(name, *args, **kwargs):
            if name == "zoneinfo":
                raise ImportError("Mocked: no zoneinfo")
            return original_import(name, *args, **kwargs)

        # Remove cached module so tick() tries to import it fresh
        saved = sys.modules.pop("zoneinfo", None)
        try:
            with patch("builtins.__import__", side_effect=fail_zoneinfo):
                mock_now = datetime(2026, 2, 23, 6, 30, 0)
                with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
                    mock_dt.now.return_value = mock_now
                    await cap.tick()

            db.save_dream.assert_awaited_once()
        finally:
            if saved is not None:
                sys.modules["zoneinfo"] = saved

    @pytest.mark.asyncio
    async def test_tick_db_dream_load_error(self):
        """Exception loading recent dreams from DB is handled gracefully."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=_mock_pipeline_result(_valid_dream_json())
        )
        db = AsyncMock()
        db.get_recent_dreams = AsyncMock(side_effect=RuntimeError("DB error"))
        db.save_dream = AsyncMock(return_value=1)

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 6, 30, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        # Should still generate dream despite DB error
        db.save_dream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tick_db_save_error(self):
        """Exception saving dream to DB is handled gracefully."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=_mock_pipeline_result(_valid_dream_json())
        )
        db = AsyncMock()
        db.get_recent_dreams = AsyncMock(return_value=[])
        db.save_dream = AsyncMock(side_effect=RuntimeError("DB write error"))

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        mock_now = datetime(2026, 2, 23, 6, 30, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()  # Should not raise

        assert cap.last_dream is not None


class TestDreamCapabilityEdgeMethods:
    @pytest.mark.asyncio
    async def test_get_prompt_context_no_system(self):
        """get_prompt_context returns empty string when no system."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        # No setup — _dream_system is None
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_generate_dream_no_system(self):
        """generate_dream returns None when no system."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        # No setup
        result = await cap.generate_dream()
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_dream_db_error(self):
        """generate_dream handles DB error gracefully."""
        db = AsyncMock()
        db.get_recent_dreams = AsyncMock(side_effect=RuntimeError("DB error"))

        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=_mock_pipeline_result(_valid_dream_json())
        )

        ctx = _make_ctx(llm_pipeline=pipeline, engagement_db=db)
        cap = DreamCapability(ctx)
        await cap.setup()

        dream = await cap.generate_dream()
        assert dream is not None

    def test_get_dream_insights_no_system(self):
        """get_dream_insights returns empty list when no system."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        assert cap.get_dream_insights() == []

    @pytest.mark.asyncio
    async def test_get_dream_insights_with_dreams(self):
        """get_dream_insights returns insights from generated dreams."""
        ctx = _make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()

        # Generate a fallback dream
        mock_now = datetime(2026, 2, 23, 6, 30, 0)
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            await cap.tick()

        insights = cap.get_dream_insights(days=7)
        assert isinstance(insights, list)


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
        pipeline._chat_with_overrides.return_value = _mock_pipeline_result(_valid_dream_json())

        ctx = _make_ctx(llm_pipeline=pipeline)
        cap = DreamCapability(ctx)
        await cap.setup()

        dream = await cap.generate_dream(recent_topics=["attachment theory"])
        assert dream is not None
        assert isinstance(dream, Dream)
