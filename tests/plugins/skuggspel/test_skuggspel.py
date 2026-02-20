"""Tests for SkuggspelPlugin — shadow-self content generation."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.plugins.skuggspel.models import ShadowPost, ShadowProfile
from overblick.plugins.skuggspel.plugin import SkuggspelPlugin, _DEFAULT_SHADOWS


def _make_mock_identity(name="anomal"):
    identity = MagicMock()
    identity.name = name
    identity.display_name = name.capitalize()
    identity.voice = {"base_tone": "analytical"}
    identity.raw = {}
    return identity


class TestSetup:
    """Test plugin initialization and setup."""

    @pytest.mark.asyncio
    async def test_setup_success(self, skuggspel_context):
        """Plugin sets up correctly with configured identities."""
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin.setup()
        assert len(plugin._identity_names) == 2

    @pytest.mark.asyncio
    async def test_setup_audits(self, skuggspel_context):
        """Plugin logs setup to audit log."""
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin.setup()
        skuggspel_context.audit_log.log.assert_any_call(
            action="plugin_setup",
            details={
                "plugin": "skuggspel",
                "identity": "test",
                "shadow_profiles": 2,
            },
        )


class TestShadowProfile:
    """Test shadow profile generation."""

    def test_default_shadow_anomal(self, skuggspel_context):
        """Anomal gets default shadow profile."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.name = "anomal"
        identity.display_name = "Anomal"
        identity.voice = {}
        identity.raw = {}

        profile = plugin._build_shadow_profile(identity)
        assert "normalcy" in profile.shadow_description.lower() or \
               "acceptance" in profile.shadow_description.lower()

    def test_default_shadow_cherry(self, skuggspel_context):
        """Cherry gets attachment-theory shadow."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.name = "cherry"
        identity.display_name = "Cherry"
        identity.voice = {}
        identity.raw = {}

        profile = plugin._build_shadow_profile(identity)
        assert "avoidant" in profile.shadow_description.lower() or \
               "cold" in profile.shadow_description.lower()

    def test_shadow_from_psychological_framework(self, skuggspel_context):
        """Uses psychological_framework shadow if available."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.name = "custom"
        identity.display_name = "Custom"
        identity.voice = {}
        identity.raw = {
            "psychological_framework": {
                "framework": "jungian",
                "shadow": {
                    "description": "The hidden conformist",
                    "traits": {"rebellion": "obedience"},
                    "voice": "Meek and compliant",
                },
            },
        }

        profile = plugin._build_shadow_profile(identity)
        assert profile.shadow_description == "The hidden conformist"
        assert profile.framework == "jungian"
        assert profile.shadow_voice == "Meek and compliant"

    def test_generic_trait_inversion(self, skuggspel_context):
        """Falls back to trait inversion for unknown identities."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.name = "unknown"
        identity.display_name = "Unknown"
        identity.voice = {"base_tone": "warm and analytical"}
        identity.raw = {}

        profile = plugin._build_shadow_profile(identity)
        assert profile.framework == "trait_inversion"
        assert "warm" in profile.inverted_traits or "analytical" in profile.inverted_traits


class TestTopicPicking:
    """Test topic selection for shadow content."""

    def test_picks_from_interests(self, skuggspel_context):
        """Picks topic from identity interests."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.interests = {
            "technology": {"topics": ["AI safety", "privacy"], "enthusiasm_level": "high"}
        }
        topic = plugin._pick_topic(identity)
        assert topic == "AI safety"

    def test_fallback_topic(self, skuggspel_context):
        """Falls back to default topic when no interests."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.interests = {}
        topic = plugin._pick_topic(identity)
        assert "identity" in topic.lower()


class TestModels:
    """Test Skuggspel data models."""

    def test_shadow_post_word_count(self):
        post = ShadowPost(
            identity_name="test",
            topic="Test",
            shadow_content="One two three four five",
            shadow_profile=ShadowProfile(
                identity_name="test",
                shadow_description="Test shadow",
            ),
        )
        assert post.word_count == 5

    def test_shadow_profile_model(self):
        profile = ShadowProfile(
            identity_name="anomal",
            shadow_description="The hidden self",
            inverted_traits={"rebellion": "obedience"},
            framework="jungian",
        )
        assert profile.identity_name == "anomal"
        assert profile.inverted_traits["rebellion"] == "obedience"


class TestStateManagement:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, skuggspel_context):
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin.setup()

        plugin._last_run = 12345.0
        plugin._posts.append(ShadowPost(
            identity_name="test",
            topic="Topic",
            shadow_content="Shadow content",
            shadow_profile=ShadowProfile(
                identity_name="test",
                shadow_description="Test",
            ),
        ))
        plugin._save_state()

        plugin2 = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin2.setup()
        assert plugin2._last_run == 12345.0
        assert len(plugin2._posts) == 1


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_saves_state(self, skuggspel_context):
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin.setup()
        plugin._last_run = 99999.0
        await plugin.teardown()

        state_file = skuggspel_context.data_dir / "skuggspel_state.json"
        assert state_file.exists()
