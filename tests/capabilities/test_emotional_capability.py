"""Tests for EmotionalCapability — emotional state tracking wrapper.

Covers uncovered lines 70, 86.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from overblick.capabilities.psychology.emotional import EmotionalCapability
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestEmotionalCapabilityCoverage:
    @pytest.mark.asyncio
    async def test_on_event_not_initialized(self):
        """Cover line 70: on_event returns early when state is None."""
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        # Don't call setup
        await cap.on_event("interaction_positive")  # Should not raise

    @pytest.mark.asyncio
    async def test_get_prompt_context_not_initialized(self):
        """Cover line 86: get_prompt_context returns empty when not initialized."""
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_on_event_positive_with_topic(self):
        """on_event with topic for positive interaction (jungian supports topic)."""
        ctx = make_ctx(config={"emotional_model": "jungian"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.on_event("interaction_positive", topic="ai discussion")
        # Should not raise; mood should be affected

    @pytest.mark.asyncio
    async def test_on_event_negative_with_topic(self):
        """on_event with topic for negative interaction (jungian supports topic)."""
        ctx = make_ctx(config={"emotional_model": "jungian"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.on_event("interaction_negative", topic="spam")

    @pytest.mark.asyncio
    async def test_on_event_positive_no_topic(self):
        """on_event without topic for positive interaction."""
        ctx = make_ctx(config={"emotional_model": "generic"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.on_event("interaction_positive")

    @pytest.mark.asyncio
    async def test_on_event_negative_no_topic(self):
        """on_event without topic for negative interaction."""
        ctx = make_ctx(config={"emotional_model": "generic"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.on_event("interaction_negative")

    @pytest.mark.asyncio
    async def test_on_event_jailbreak_attempt(self):
        """on_event jailbreak_attempt for jungian state."""
        ctx = make_ctx(config={"emotional_model": "jungian"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.on_event("jailbreak_attempt")

    @pytest.mark.asyncio
    async def test_on_event_ai_topic_discussed(self):
        """on_event ai_topic_discussed for jungian state."""
        ctx = make_ctx(config={"emotional_model": "jungian"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.on_event("ai_topic_discussed")

    @pytest.mark.asyncio
    async def test_tick_decays_mood(self):
        """tick() calls decay on the state."""
        ctx = make_ctx(config={"emotional_model": "generic"})
        cap = EmotionalCapability(ctx)
        await cap.setup()

        await cap.tick()  # Should not raise

    @pytest.mark.asyncio
    async def test_tick_not_initialized(self):
        """tick() does nothing when not initialized."""
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.tick()

    @pytest.mark.asyncio
    async def test_record_positive(self):
        """record_positive delegates to state (jungian supports topic)."""
        ctx = make_ctx(config={"emotional_model": "jungian"})
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.record_positive("ai")
        cap.record_positive()

    @pytest.mark.asyncio
    async def test_record_negative(self):
        """record_negative delegates to state (jungian supports topic)."""
        ctx = make_ctx(config={"emotional_model": "jungian"})
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.record_negative("spam")
        cap.record_negative()

    @pytest.mark.asyncio
    async def test_record_positive_not_initialized(self):
        """record_positive does nothing when not initialized."""
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        cap.record_positive()

    @pytest.mark.asyncio
    async def test_record_negative_not_initialized(self):
        """record_negative does nothing when not initialized."""
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        cap.record_negative()

    @pytest.mark.asyncio
    async def test_setup_relational_model(self):
        """Setup with relational emotional model."""
        ctx = make_ctx(config={"emotional_model": "relational"})
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_setup_unknown_model_defaults_to_generic(self):
        """Unknown emotional_model falls back to generic."""
        ctx = make_ctx(config={"emotional_model": "unknown_model"})
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_get_prompt_context_initialized(self):
        """Cover line 87: get_prompt_context when state is set."""
        ctx = make_ctx(config={"emotional_model": "generic"})
        cap = EmotionalCapability(ctx)
        await cap.setup()
        result = cap.get_prompt_context()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_on_event_ai_topic_relational(self):
        """Cover line 81: ai_topic_discussed calls record_ai_topic_discussion (relational)."""
        ctx = make_ctx(config={"emotional_model": "relational"})
        cap = EmotionalCapability(ctx)
        await cap.setup()
        await cap.on_event("ai_topic_discussed")
