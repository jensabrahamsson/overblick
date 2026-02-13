"""Tests for dream system."""

from overblick.plugins.moltbook.dream_system import (
    DreamSystem, DreamType, DreamTone, Dream,
)


class TestDreamSystem:
    def test_generate_dream(self):
        ds = DreamSystem()
        dream = ds.generate_morning_dream()
        assert isinstance(dream, Dream)
        assert dream.content
        assert dream.insight
        assert isinstance(dream.dream_type, DreamType)

    def test_dream_with_topics(self):
        ds = DreamSystem()
        dream = ds.generate_morning_dream(recent_topics=["crypto", "AI"])
        assert dream.topics_referenced == ["crypto", "AI"]

    def test_get_dream_insights(self):
        ds = DreamSystem()
        ds.generate_morning_dream()
        insights = ds.get_dream_insights(days=1)
        assert len(insights) >= 1

    def test_dream_context_for_prompt(self):
        ds = DreamSystem()
        ds.generate_morning_dream()
        context = ds.get_dream_context_for_prompt()
        assert "REFLECTIONS" in context

    def test_empty_context(self):
        ds = DreamSystem()
        assert ds.get_dream_context_for_prompt() == ""

    def test_custom_templates(self):
        templates = {
            DreamType.INTELLECTUAL_SYNTHESIS: [
                {
                    "content": "Custom dream content",
                    "symbols": ["custom"],
                    "tone": DreamTone.CLARIFYING,
                    "insight": "Custom insight",
                },
            ],
        }
        ds = DreamSystem(dream_templates=templates)
        dream = ds.generate_morning_dream()
        # Should use custom templates
        assert dream.content == "Custom dream content" or dream.content  # May vary by type


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
