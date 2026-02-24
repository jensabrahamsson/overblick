"""
Tests for capability wrappers and registry.

Verifies that capabilities correctly wrap underlying modules,
provide prompt context, and integrate with the CapabilityRegistry.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from overblick.core.capability import CapabilityBase, CapabilityContext, CapabilityRegistry
from overblick.capabilities import (
    CAPABILITY_REGISTRY,
    CAPABILITY_BUNDLES,
    resolve_capabilities,
    DreamCapability,
    TherapyCapability,
    EmotionalCapability,
    LearningCapability,
    KnowledgeCapability,
    OpeningCapability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(**overrides) -> CapabilityContext:
    """Create a minimal CapabilityContext for testing."""
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "llm_client": AsyncMock(),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestCapabilityRegistry:
    def test_default_registry_has_all_capabilities(self):
        registry = CapabilityRegistry.default()
        assert "dream_system" in registry._registry
        assert "therapy_system" in registry._registry
        assert "emotional_state" in registry._registry
        assert "safe_learning" in registry._registry
        assert "knowledge_loader" in registry._registry
        assert "openings" in registry._registry

    def test_default_registry_has_bundles(self):
        registry = CapabilityRegistry.default()
        assert "psychology" in registry._bundles
        assert "knowledge" in registry._bundles
        assert "social" in registry._bundles

    def test_resolve_individual(self):
        registry = CapabilityRegistry.default()
        assert registry.resolve(["dream_system"]) == ["dream_system"]

    def test_resolve_bundle(self):
        registry = CapabilityRegistry.default()
        resolved = registry.resolve(["psychology"])
        assert "dream_system" in resolved
        assert "therapy_system" in resolved
        assert "emotional_state" in resolved

    def test_resolve_mixed(self):
        registry = CapabilityRegistry.default()
        resolved = registry.resolve(["dream_system", "knowledge"])
        assert "dream_system" in resolved
        assert "safe_learning" in resolved
        assert "knowledge_loader" in resolved

    def test_resolve_no_duplicates(self):
        registry = CapabilityRegistry.default()
        resolved = registry.resolve(["dream_system", "psychology"])
        assert resolved.count("dream_system") == 1

    def test_resolve_unknown_skipped(self):
        registry = CapabilityRegistry.default()
        resolved = registry.resolve(["nonexistent", "dream_system"])
        assert resolved == ["dream_system"]

    def test_create_returns_capability(self):
        registry = CapabilityRegistry.default()
        mock_ctx = MagicMock()
        mock_ctx.identity_name = "test"
        mock_ctx.data_dir = Path("/tmp/test")
        mock_ctx.llm_client = None
        mock_ctx.event_bus = None
        mock_ctx.audit_log = None
        mock_ctx.quiet_hours_checker = None
        mock_ctx.identity = None
        cap = registry.create("dream_system", mock_ctx)
        assert isinstance(cap, DreamCapability)

    def test_create_unknown_returns_none(self):
        registry = CapabilityRegistry.default()
        mock_ctx = MagicMock()
        assert registry.create("nonexistent", mock_ctx) is None


class TestResolveCapabilities:
    def test_bundle_expansion(self):
        assert "dream_system" in resolve_capabilities(["psychology"])

    def test_individual(self):
        assert resolve_capabilities(["openings"]) == ["openings"]

    def test_unknown_ignored(self):
        assert resolve_capabilities(["unknown"]) == []


# ---------------------------------------------------------------------------
# DreamCapability
# ---------------------------------------------------------------------------

class TestDreamCapability:
    @pytest.mark.asyncio
    async def test_setup_creates_dream_system(self):
        ctx = make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_generate_dream(self):
        ctx = make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()
        dream = await cap.generate_dream(recent_topics=["AI"])
        assert dream is not None
        assert dream.content

    @pytest.mark.asyncio
    async def test_get_prompt_context_empty_initially(self):
        ctx = make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_get_prompt_context_after_dream(self):
        ctx = make_ctx()
        cap = DreamCapability(ctx)
        await cap.setup()
        await cap.generate_dream()
        context = cap.get_prompt_context()
        assert "RECENT REFLECTIONS" in context

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = DreamCapability(ctx)
        assert cap.name == "dream_system"


# ---------------------------------------------------------------------------
# TherapyCapability
# ---------------------------------------------------------------------------

class TestTherapyCapability:
    @pytest.mark.asyncio
    async def test_setup_creates_therapy_system(self):
        ctx = make_ctx(config={"system_prompt": "Test prompt"})
        cap = TherapyCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_is_therapy_day(self):
        ctx = make_ctx(config={"therapy_day": 6})
        cap = TherapyCapability(ctx)
        await cap.setup()
        result = cap.is_therapy_day()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_run_session_empty(self):
        ctx = make_ctx(config={"system_prompt": "Test"})
        cap = TherapyCapability(ctx)
        await cap.setup()
        session = await cap.run_session()
        assert session is not None
        assert session.week_number == 1

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = TherapyCapability(ctx)
        assert cap.name == "therapy_system"


# ---------------------------------------------------------------------------
# EmotionalCapability
# ---------------------------------------------------------------------------

class TestEmotionalCapability:
    @pytest.mark.asyncio
    async def test_setup_creates_state(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_record_positive(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.record_positive()
        assert cap.inner.positive_interactions == 1

    @pytest.mark.asyncio
    async def test_record_negative(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.record_negative()
        assert cap.inner.negative_interactions == 1

    @pytest.mark.asyncio
    async def test_event_handling(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.setup()
        await cap.on_event("interaction_positive")
        assert cap.inner.positive_interactions == 1
        await cap.on_event("interaction_negative")
        assert cap.inner.negative_interactions == 1

    @pytest.mark.asyncio
    async def test_prompt_context_neutral(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.setup()
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_prompt_context_after_positive(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        await cap.setup()
        cap.record_positive()
        context = cap.get_prompt_context()
        assert "mood" in context.lower() or "Current" in context

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = EmotionalCapability(ctx)
        assert cap.name == "emotional_state"


# ---------------------------------------------------------------------------
# LearningCapability
# ---------------------------------------------------------------------------

class TestLearningCapability:
    @pytest.mark.asyncio
    async def test_setup_creates_module(self):
        ctx = make_ctx(config={"ethos_text": "Be ethical"})
        cap = LearningCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_propose_learning(self):
        from overblick.plugins.moltbook.safe_learning import LearningCategory
        ctx = make_ctx(config={"ethos_text": "Be ethical"})
        cap = LearningCapability(ctx)
        await cap.setup()
        result = cap.propose_learning(
            content="AI can reason",
            category=LearningCategory.FACTUAL,
            source_context="conversation about AI",
            source_agent="TestBot",
        )
        assert result is not None
        assert len(cap.pending_learnings) == 1

    @pytest.mark.asyncio
    async def test_extract_potential_learnings(self):
        learnings = LearningCapability.extract_potential_learnings(
            "Did you know that AI can learn from text?",
            "Interesting!",
            "TeachBot",
        )
        assert len(learnings) > 0

    @pytest.mark.asyncio
    async def test_pending_learnings_empty(self):
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        await cap.setup()
        assert cap.pending_learnings == []

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        assert cap.name == "safe_learning"


# ---------------------------------------------------------------------------
# KnowledgeCapability
# ---------------------------------------------------------------------------

class TestKnowledgeCapability:
    @pytest.mark.asyncio
    async def test_setup_no_dir(self):
        ctx = make_ctx(
            identity_name="nonexistent_identity_xyz",
            config={"knowledge_dir": "/tmp/nonexistent_xyz_dir"},
        )
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.inner is None

    @pytest.mark.asyncio
    async def test_setup_with_identity_dir(self):
        ctx = make_ctx(identity_name="anomal")
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        # anomal identity dir should exist and have knowledge files
        if cap.inner:
            assert cap.inner.total_items > 0

    @pytest.mark.asyncio
    async def test_prompt_context_empty_when_no_loader(self):
        ctx = make_ctx(
            identity_name="nonexistent_xyz",
            config={"knowledge_dir": "/tmp/nonexistent_xyz_dir"},
        )
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = KnowledgeCapability(ctx)
        assert cap.name == "knowledge_loader"


# ---------------------------------------------------------------------------
# OpeningCapability
# ---------------------------------------------------------------------------

class TestOpeningCapability:
    @pytest.mark.asyncio
    async def test_setup_creates_selector(self):
        ctx = make_ctx()
        cap = OpeningCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_select_returns_string(self):
        ctx = make_ctx(config={"opening_phrases": ["Hello", "Hey"]})
        cap = OpeningCapability(ctx)
        await cap.setup()
        result = cap.select()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_select_with_custom_phrases(self):
        phrases = ["Right,", "Interesting.", "Well then."]
        ctx = make_ctx(config={"opening_phrases": phrases})
        cap = OpeningCapability(ctx)
        await cap.setup()
        result = cap.select()
        assert result in phrases

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = OpeningCapability(ctx)
        assert cap.name == "openings"


# ---------------------------------------------------------------------------
# Integration: Plugin uses capabilities
# ---------------------------------------------------------------------------

class TestPluginCapabilityIntegration:
    """Verify that MoltbookPlugin correctly uses CapabilityRegistry."""

    @pytest.mark.asyncio
    async def test_plugin_has_capabilities(self, setup_anomal_plugin):
        """Anomal plugin (with dream/therapy/safe_learning) has capabilities loaded."""
        plugin, ctx, client = setup_anomal_plugin
        assert plugin._dream_system is not None
        assert plugin._therapy_system is not None
        assert plugin._safe_learning is not None

    @pytest.mark.asyncio
    async def test_plugin_capabilities_dict(self, setup_anomal_plugin):
        """Capabilities dict is populated."""
        plugin, ctx, client = setup_anomal_plugin
        assert "dream_system" in plugin._capabilities
        assert "therapy_system" in plugin._capabilities
        assert "safe_learning" in plugin._capabilities

    @pytest.mark.asyncio
    async def test_get_capability(self, setup_anomal_plugin):
        """get_capability() returns correct capability."""
        plugin, ctx, client = setup_anomal_plugin
        dream = plugin.get_capability("dream_system")
        assert isinstance(dream, DreamCapability)

    @pytest.mark.asyncio
    async def test_get_capability_unknown(self, setup_anomal_plugin):
        """get_capability() returns None for unknown."""
        plugin, ctx, client = setup_anomal_plugin
        assert plugin.get_capability("nonexistent") is None

    @pytest.mark.asyncio
    async def test_cherry_no_capabilities(self, setup_cherry_plugin):
        """Cherry (no enabled_modules) has empty capabilities."""
        plugin, ctx, client = setup_cherry_plugin
        assert plugin._dream_system is None
        assert plugin._therapy_system is None
        assert plugin._safe_learning is None
        assert len(plugin._capabilities) == 0

    @pytest.mark.asyncio
    async def test_gather_context(self, setup_anomal_plugin):
        """_gather_capability_context() collects from all enabled capabilities."""
        plugin, ctx, client = setup_anomal_plugin
        # Initially empty (no dreams generated)
        context = plugin._gather_capability_context()
        assert isinstance(context, str)
