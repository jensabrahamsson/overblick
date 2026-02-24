"""Tests for dream system (via moltbook backward-compat shim)."""

import pytest
from overblick.plugins.moltbook.dream_system import (
    DreamSystem, DreamType, DreamTone, Dream,
)


class TestDreamSystem:
    @pytest.mark.asyncio
    async def test_generate_dream(self):
        ds = DreamSystem()
        dream = await ds.generate_morning_dream()
        assert isinstance(dream, Dream)
        assert dream.content
        assert dream.insight
        assert isinstance(dream.dream_type, DreamType)

    @pytest.mark.asyncio
    async def test_dream_with_topics(self):
        ds = DreamSystem()
        dream = await ds.generate_morning_dream(recent_topics=["crypto", "AI"])
        assert dream.topics_referenced == ["crypto", "AI"]

    @pytest.mark.asyncio
    async def test_get_dream_insights(self):
        ds = DreamSystem()
        await ds.generate_morning_dream()
        insights = ds.get_dream_insights(days=1)
        assert len(insights) >= 1

    @pytest.mark.asyncio
    async def test_dream_context_for_prompt(self):
        ds = DreamSystem()
        await ds.generate_morning_dream()
        context = ds.get_dream_context_for_prompt()
        assert "REFLECTIONS" in context

    def test_empty_context(self):
        ds = DreamSystem()
        assert ds.get_dream_context_for_prompt() == ""

    @pytest.mark.asyncio
    async def test_custom_guidance(self):
        guidance = {
            DreamType.INTELLECTUAL_SYNTHESIS: {
                "themes": ["custom theme"],
                "symbols": ["custom"],
                "tones": ["clarifying"],
                "psychological_core": "Custom insight",
            },
        }
        ds = DreamSystem(dream_guidance=guidance)
        dream = await ds.generate_morning_dream()
        assert dream.content  # Fallback dream has content


class TestDream:
    def test_to_dict(self):
        dream = Dream(
            dream_type=DreamType.PATTERN_RECOGNITION,
            timestamp="2026-01-01T08:00:00",
            content="Test dream",
            symbols=["test"],
            tone=DreamTone.CONTEMPLATIVE,
            insight="Test insight",
        )
        d = dream.to_dict()
        assert d["dream_type"] == "pattern_recognition"
        assert d["tone"] == "contemplative"
        assert d["potential_learning"] == ""

    def test_from_dict(self):
        data = {
            "dream_type": "shadow_integration",
            "timestamp": "2026-01-01T08:00:00",
            "content": "Test",
            "symbols": ["shadow"],
            "tone": "unsettling",
            "insight": "Insight",
        }
        dream = Dream.from_dict(data)
        assert dream.dream_type == DreamType.SHADOW_INTEGRATION
        assert dream.tone == DreamTone.UNSETTLING
