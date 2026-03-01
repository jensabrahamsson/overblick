"""Tests for therapy system and TherapyCapability prompt context."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from overblick.plugins.moltbook.therapy_system import (
    CherryTherapySystem,
    TherapySession,
    TherapySystem,
)
from overblick.capabilities.psychology.therapy import TherapyCapability


class TestTherapySystem:
    def test_creation(self):
        ts = TherapySystem()
        assert ts is not None

    def test_creation_with_therapy_day(self):
        ts = TherapySystem(therapy_day=0)  # Monday
        assert ts._therapy_day == 0

    def test_is_therapy_day(self):
        today = datetime.now().weekday()
        ts = TherapySystem(therapy_day=today)
        assert ts.is_therapy_day()

    def test_is_not_therapy_day(self):
        # Pick a day that is NOT today
        today = datetime.now().weekday()
        other = (today + 3) % 7
        ts = TherapySystem(therapy_day=other)
        assert not ts.is_therapy_day()

    def test_session_history_starts_empty(self):
        ts = TherapySystem()
        assert ts._session_history == []

    def test_last_session_summary_initially_empty(self):
        ts = TherapySystem()
        assert ts.last_session_summary == ""

    @pytest.mark.asyncio
    async def test_last_session_summary_set_after_run_session(self):
        ts = TherapySystem()
        session = await ts.run_session()
        assert ts.last_session_summary == session.session_summary
        assert ts.last_session_summary != ""


class TestTherapySession:
    def test_defaults(self):
        session = TherapySession()
        assert session.dreams_processed == 0
        assert session.learnings_processed == 0
        assert session.dream_themes == []
        assert session.shadow_patterns == []

    def test_to_dict(self):
        session = TherapySession(
            week_number=5,
            dreams_processed=3,
            learnings_processed=2,
            dream_themes=["growth", "identity"],
            synthesis_insights=["Insight 1"],
            shadow_patterns=["avoidance"],
            archetype_encounters=["sage"],
        )
        d = session.to_dict()
        assert d["week_number"] == 5
        assert d["dreams_processed"] == 3
        assert d["dream_themes"] == ["growth", "identity"]
        assert d["shadow_patterns"] == ["avoidance"]
        assert d["archetype_encounters"] == ["sage"]
        assert "timestamp" in d

    def test_post_fields(self):
        session = TherapySession(
            post_title="Weekly Reflection",
            post_content="Content here",
            post_submolt="philosophy",
        )
        assert session.post_title == "Weekly Reflection"
        assert session.post_submolt == "philosophy"


class TestTherapyCapabilityPromptContext:
    """Tests for TherapyCapability.get_prompt_context()."""

    def _make_cap_ctx(self, identity_name="anomal"):
        """Build a minimal CapabilityContext mock."""
        ctx = MagicMock()
        ctx.identity_name = identity_name
        ctx.config = {"therapy_day": 6}
        ctx.llm_client = None
        ctx.data_dir = None
        return ctx

    def test_no_therapy_system_returns_empty(self):
        cap = TherapyCapability(self._make_cap_ctx())
        # _therapy_system is None before setup
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_anomal_returns_session_summary(self):
        cap = TherapyCapability(self._make_cap_ctx("anomal"))
        await cap.setup()
        # Run a session to populate last_session_summary
        session = await cap.run_session()
        ctx_str = cap.get_prompt_context()
        assert "Therapy insight" in ctx_str
        assert session.session_summary in ctx_str

    @pytest.mark.asyncio
    async def test_cherry_returns_session_summary(self):
        cap = TherapyCapability(self._make_cap_ctx("cherry"))
        await cap.setup()
        session = await cap.run_session()
        ctx_str = cap.get_prompt_context()
        assert "Therapy insight" in ctx_str

    @pytest.mark.asyncio
    async def test_anomal_no_session_returns_empty(self):
        cap = TherapyCapability(self._make_cap_ctx("anomal"))
        await cap.setup()
        # No session run yet
        assert cap.get_prompt_context() == ""
