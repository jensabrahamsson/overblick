"""Tests for SkuggspelPlugin — shadow-self content generation."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.plugins.skuggspel.models import ShadowPost, ShadowProfile
from overblick.plugins.skuggspel.plugin import SkuggspelPlugin


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
        assert (
            "normalcy" in profile.shadow_description.lower()
            or "acceptance" in profile.shadow_description.lower()
        )

    def test_default_shadow_cherry(self, skuggspel_context):
        """Cherry gets attachment-theory shadow."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.name = "cherry"
        identity.display_name = "Cherry"
        identity.voice = {}
        identity.raw = {}

        profile = plugin._build_shadow_profile(identity)
        assert (
            "avoidant" in profile.shadow_description.lower()
            or "cold" in profile.shadow_description.lower()
        )

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

    def test_fallback_to_area_name_when_no_topics(self, skuggspel_context):
        """Falls back to area name when interest is not a dict with topics."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.interests = {"crypto_trading": "passionate"}
        topic = plugin._pick_topic(identity)
        assert topic == "Crypto Trading"

    def test_iterates_past_empty_dict(self, skuggspel_context):
        """Iterates past interest dicts with no topics to find one with topics."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.interests = {
            "philosophy": {"enthusiasm_level": "high"},  # No topics key
            "music": {"topics": ["jazz"], "enthusiasm_level": "medium"},
        }
        topic = plugin._pick_topic(identity)
        assert topic == "jazz"


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


class TestSetupDefaultIdentities:
    """Test setup with default identity discovery."""

    @pytest.mark.asyncio
    async def test_should_use_list_identities_when_none_configured(self, skuggspel_context):
        """Uses list_identities() when no identities configured."""
        # Remove configured identities
        skuggspel_context.identity.raw_config["skuggspel"]["identities"] = []
        plugin = SkuggspelPlugin(skuggspel_context)
        with (
            patch("overblick.plugins.skuggspel.plugin.list_identities", return_value=["anomal", "supervisor", "cherry"]),
            patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=_make_mock_identity()),
        ):
            await plugin.setup()
        # supervisor should be filtered out
        assert "supervisor" not in plugin._identity_names
        assert "anomal" in plugin._identity_names
        assert "cherry" in plugin._identity_names


class TestSetupIdentityNotFound:
    """Test setup when identity not found."""

    @pytest.mark.asyncio
    async def test_should_skip_missing_identity(self, skuggspel_context):
        """Setup skips identities that raise FileNotFoundError."""
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", side_effect=FileNotFoundError("not found")):
            await plugin.setup()
        # Should have 0 shadow profiles since all identities failed
        assert len(plugin._shadow_profiles) == 0


class TestTick:
    """Test the main work cycle."""

    @pytest.mark.asyncio
    async def test_should_skip_when_not_run_time(self, skuggspel_context):
        """Tick skips when interval hasn't elapsed."""
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=_make_mock_identity()):
            await plugin.setup()
        plugin._last_run = time.time()  # Just ran
        await plugin.tick()
        skuggspel_context.llm_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_generate_shadow_posts(self, skuggspel_context):
        """Tick generates shadow posts for each identity."""
        plugin = SkuggspelPlugin(skuggspel_context)
        mock_ident = _make_mock_identity()
        mock_ident.interests = {"tech": {"topics": ["AI safety"]}}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            await plugin.setup()

            # Ensure it's run time
            plugin._last_run = 0.0

            await plugin.tick()

        assert len(plugin._posts) > 0
        skuggspel_context.event_bus.emit.assert_called()
        skuggspel_context.audit_log.log.assert_any_call(
            action="skuggspel_round_complete",
            details={"identities_processed": len(plugin._shadow_profiles)},
        )

    @pytest.mark.asyncio
    async def test_should_trim_posts_when_exceeding_max(self, skuggspel_context):
        """Posts get trimmed to _MAX_POSTS_STORED."""
        plugin = SkuggspelPlugin(skuggspel_context)
        mock_ident = _make_mock_identity()
        mock_ident.interests = {"tech": {"topics": ["AI"]}}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            await plugin.setup()

            # Pre-fill posts to just under limit
            for i in range(100):
                plugin._posts.append(
                    ShadowPost(
                        identity_name="test",
                        topic="T",
                        shadow_content="C",
                        shadow_profile=ShadowProfile(identity_name="test", shadow_description="D"),
                    )
                )

            plugin._last_run = 0.0
            await plugin.tick()

        assert len(plugin._posts) <= 100

    @pytest.mark.asyncio
    async def test_should_handle_pipeline_error(self, skuggspel_context):
        """Tick handles pipeline errors gracefully."""
        plugin = SkuggspelPlugin(skuggspel_context)
        mock_ident = _make_mock_identity()
        mock_ident.interests = {"tech": {"topics": ["AI"]}}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        skuggspel_context.llm_pipeline.chat = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            await plugin.setup()

            plugin._last_run = 0.0
            # Should not raise
            await plugin.tick()


class TestGenerateShadowPost:
    """Test shadow post generation."""

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_pipeline(self, skuggspel_context):
        """Returns None when no LLM pipeline is available."""
        skuggspel_context.llm_pipeline = None
        plugin = SkuggspelPlugin(skuggspel_context)
        profile = ShadowProfile(identity_name="test", shadow_description="D")
        result = await plugin._generate_shadow_post("test", profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_identity_not_found(self, skuggspel_context):
        """Returns None when identity raises FileNotFoundError."""
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", side_effect=FileNotFoundError("gone")):
            profile = ShadowProfile(identity_name="test", shadow_description="D")
            result = await plugin._generate_shadow_post("test", profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_blocked(self, skuggspel_context):
        """Returns None when LLM response is blocked."""

        skuggspel_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True,
                block_reason="safety",
                block_stage=PipelineStage.OUTPUT_SAFETY,
            )
        )
        mock_ident = _make_mock_identity()
        mock_ident.interests = {"tech": {"topics": ["AI"]}}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            plugin = SkuggspelPlugin(skuggspel_context)
            profile = ShadowProfile(identity_name="test", shadow_description="D")
            result = await plugin._generate_shadow_post("test", profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_generate_post_successfully(self, skuggspel_context):
        """Generates a shadow post with valid pipeline response."""
        mock_ident = _make_mock_identity()
        mock_ident.interests = {"tech": {"topics": ["AI safety"]}}
        mock_ident.llm = MagicMock()
        mock_ident.llm.temperature = 0.7
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=mock_ident):
            plugin = SkuggspelPlugin(skuggspel_context)
            profile = ShadowProfile(
                identity_name="anomal",
                shadow_description="The conformist",
                shadow_voice="Eager to please",
                inverted_traits={"rebellion": "obedience"},
            )
            result = await plugin._generate_shadow_post("anomal", profile)
        assert result is not None
        assert result.identity_name == "anomal"
        assert result.shadow_content != ""


class TestBuildShadowPrompt:
    """Test shadow prompt building."""

    def test_should_include_shadow_voice(self, skuggspel_context):
        """Prompt includes shadow voice when present."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.display_name = "Test"
        profile = ShadowProfile(
            identity_name="test",
            shadow_description="The shadow",
            shadow_voice="Dark and brooding",
        )
        prompt = plugin._build_shadow_prompt(identity, profile)
        assert "Dark and brooding" in prompt

    def test_should_include_inverted_traits(self, skuggspel_context):
        """Prompt includes inverted traits when present."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.display_name = "Test"
        profile = ShadowProfile(
            identity_name="test",
            shadow_description="The shadow",
            inverted_traits={"warm": "cold"},
        )
        prompt = plugin._build_shadow_prompt(identity, profile)
        assert "warm -> cold" in prompt

    def test_should_work_without_optional_fields(self, skuggspel_context):
        """Prompt works with minimal profile."""
        plugin = SkuggspelPlugin(skuggspel_context)
        identity = MagicMock()
        identity.display_name = "Test"
        profile = ShadowProfile(
            identity_name="test",
            shadow_description="The shadow",
        )
        prompt = plugin._build_shadow_prompt(identity, profile)
        assert "SHADOW" in prompt
        assert "SECURITY" in prompt


class TestGetPosts:
    """Test public accessor methods."""

    def test_should_return_posts_newest_first(self, skuggspel_context):
        """get_posts returns posts in reverse order."""
        plugin = SkuggspelPlugin(skuggspel_context)
        for i in range(3):
            plugin._posts.append(
                ShadowPost(
                    identity_name=f"id_{i}",
                    topic="T",
                    shadow_content="C",
                    shadow_profile=ShadowProfile(identity_name=f"id_{i}", shadow_description="D"),
                )
            )
        result = plugin.get_posts()
        assert result[0].identity_name == "id_2"

    def test_should_filter_posts_by_identity(self, skuggspel_context):
        """get_posts_for_identity filters correctly."""
        plugin = SkuggspelPlugin(skuggspel_context)
        plugin._posts = [
            ShadowPost(
                identity_name="anomal",
                topic="T",
                shadow_content="C",
                shadow_profile=ShadowProfile(identity_name="anomal", shadow_description="D"),
            ),
            ShadowPost(
                identity_name="cherry",
                topic="T",
                shadow_content="C",
                shadow_profile=ShadowProfile(identity_name="cherry", shadow_description="D"),
            ),
        ]
        result = plugin.get_posts_for_identity("anomal")
        assert len(result) == 1
        assert result[0].identity_name == "anomal"


class TestIsRunTime:
    """Test scheduling logic."""

    def test_should_return_true_on_first_run(self, skuggspel_context):
        plugin = SkuggspelPlugin(skuggspel_context)
        assert plugin._is_run_time() is True

    def test_should_return_false_before_interval(self, skuggspel_context):
        plugin = SkuggspelPlugin(skuggspel_context)
        plugin._last_run = time.time()
        plugin._interval_hours = 72
        assert plugin._is_run_time() is False

    def test_should_return_true_after_interval(self, skuggspel_context):
        plugin = SkuggspelPlugin(skuggspel_context)
        plugin._last_run = time.time() - 73 * 3600
        plugin._interval_hours = 72
        assert plugin._is_run_time() is True


class TestStateManagement:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, skuggspel_context):
        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin.setup()

        plugin._last_run = 12345.0
        plugin._posts.append(
            ShadowPost(
                identity_name="test",
                topic="Topic",
                shadow_content="Shadow content",
                shadow_profile=ShadowProfile(
                    identity_name="test",
                    shadow_description="Test",
                ),
            )
        )
        plugin._save_state()

        plugin2 = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.plugins.skuggspel.plugin.PluginContext.load_identity") as mock_load:
            mock_load.return_value = _make_mock_identity()
            await plugin2.setup()
        assert plugin2._last_run == 12345.0
        assert len(plugin2._posts) == 1

    @pytest.mark.asyncio
    async def test_should_handle_corrupt_state(self, skuggspel_context):
        """Plugin handles corrupt state file gracefully."""
        state_file = skuggspel_context.data_dir / "skuggspel_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("broken json {{{")

        plugin = SkuggspelPlugin(skuggspel_context)
        with patch("overblick.core.plugin_base.PluginContext.load_identity", return_value=_make_mock_identity()):
            await plugin.setup()
        assert plugin._last_run == 0.0

    def test_should_handle_save_state_exception(self, skuggspel_context):
        """_save_state handles write errors gracefully."""
        plugin = SkuggspelPlugin(skuggspel_context)
        plugin._state_file = MagicMock()
        plugin._state_file.parent = MagicMock()
        plugin._state_file.parent.mkdir = MagicMock()
        plugin._state_file.write_text = MagicMock(side_effect=OSError("disk full"))
        plugin._save_state()


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
