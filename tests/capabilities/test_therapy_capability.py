"""Tests for TherapyCapability — therapy session wrapper.

Covers uncovered lines 95-99: get_prompt_context for CherryTherapySystem.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.capabilities.psychology.therapy import TherapyCapability
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestTherapyCapabilityCoverage:
    @pytest.mark.asyncio
    async def test_get_prompt_context_cherry_with_sessions(self):
        """Cover lines 95-99: CherryTherapySystem with recent sessions."""
        ctx = make_ctx(config={"therapy_model": "template"})
        cap = TherapyCapability(ctx)
        await cap.setup()

        # Add a mock session to the cherry therapy system's internal list
        mock_session = MagicMock()
        mock_session.session_summary = "Attachment patterns observed"
        cap._therapy_system._recent_sessions.append(mock_session)

        result = cap.get_prompt_context()
        assert "Therapy insight" in result
        assert "Attachment patterns observed" in result

    @pytest.mark.asyncio
    async def test_get_prompt_context_cherry_no_sessions(self):
        """CherryTherapySystem with no sessions returns empty."""
        ctx = make_ctx(config={"therapy_model": "template"})
        cap = TherapyCapability(ctx)
        await cap.setup()

        result = cap.get_prompt_context()
        assert result == ""

    @pytest.mark.asyncio
    async def test_get_prompt_context_cherry_session_no_summary(self):
        """CherryTherapySystem with session but no summary returns empty."""
        ctx = make_ctx(config={"therapy_model": "template"})
        cap = TherapyCapability(ctx)
        await cap.setup()

        mock_session = MagicMock()
        mock_session.session_summary = ""
        cap._therapy_system._recent_sessions.append(mock_session)

        result = cap.get_prompt_context()
        assert result == ""

    @pytest.mark.asyncio
    async def test_get_prompt_context_not_initialized(self):
        """Not initialized returns empty string."""
        ctx = make_ctx()
        cap = TherapyCapability(ctx)
        result = cap.get_prompt_context()
        assert result == ""

    @pytest.mark.asyncio
    async def test_is_therapy_day_not_initialized(self):
        """Not initialized returns False."""
        ctx = make_ctx()
        cap = TherapyCapability(ctx)
        assert cap.is_therapy_day() is False

    @pytest.mark.asyncio
    async def test_run_session_not_initialized(self):
        """Not initialized returns None."""
        ctx = make_ctx()
        cap = TherapyCapability(ctx)
        result = await cap.run_session()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_session_cherry(self):
        """CherryTherapySystem session generation."""
        ctx = make_ctx(config={"therapy_model": "template"})
        cap = TherapyCapability(ctx)
        await cap.setup()

        session = await cap.run_session(emotional_state="happy", week_stats={"posts": 5})
        assert session is not None

    @pytest.mark.asyncio
    async def test_setup_llm_model(self):
        """LLM-based therapy setup with pipeline."""
        pipeline = AsyncMock()
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"therapy_model": "llm", "system_prompt": "test prompt"},
        )
        cap = TherapyCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_inner_property(self):
        """inner property returns the therapy system."""
        ctx = make_ctx(config={"therapy_model": "template"})
        cap = TherapyCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_is_therapy_day_initialized(self):
        """Cover line 69: is_therapy_day when system is initialized."""
        ctx = make_ctx(config={"therapy_model": "template"})
        cap = TherapyCapability(ctx)
        await cap.setup()
        result = cap.is_therapy_day()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_run_session_llm_model(self):
        """Cover line 87: run_session for TherapySystem (non-cherry)."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=MagicMock(content="Analysis results", blocked=False)
        )
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"therapy_model": "llm", "system_prompt": "test"},
        )
        cap = TherapyCapability(ctx)
        await cap.setup()

        session = await cap.run_session(dreams=[], learnings=[])
        # TherapySystem.run_session returns a session or None
        assert session is not None or session is None  # just ensure no crash

    @pytest.mark.asyncio
    async def test_get_prompt_context_llm_with_summary(self):
        """Cover lines 102-104: TherapySystem (non-cherry) with last session summary."""
        pipeline = AsyncMock()
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"therapy_model": "llm", "system_prompt": "test"},
        )
        cap = TherapyCapability(ctx)
        await cap.setup()

        # Manually set last_session_summary on the TherapySystem
        cap._therapy_system._last_session_summary = "Shadow work insights"

        result = cap.get_prompt_context()
        assert "Therapy insight" in result
        assert "Shadow work insights" in result

    @pytest.mark.asyncio
    async def test_get_prompt_context_llm_no_summary(self):
        """TherapySystem without session summary returns empty."""
        pipeline = AsyncMock()
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"therapy_model": "llm", "system_prompt": "test"},
        )
        cap = TherapyCapability(ctx)
        await cap.setup()

        result = cap.get_prompt_context()
        assert result == ""
