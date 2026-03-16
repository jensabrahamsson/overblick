"""Tests for capability base class."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from overblick.core.capability import (
    CapabilityBase,
    CapabilityContext,
    CapabilityRegistry,
    build_capability_configs,
)
from overblick.core.plugin_base import PluginContext


class ConcreteCapability(CapabilityBase):
    """Test capability implementation."""

    name = "test_cap"

    async def setup(self) -> None:
        self.setup_called = True

    async def tick(self) -> None:
        self.tick_called = True

    async def on_event(self, event: str, **kwargs) -> None:
        self.last_event = event
        self.last_event_kwargs = kwargs


class MinimalCapability(CapabilityBase):
    """Minimal capability — only setup is abstract."""

    name = "minimal"

    async def setup(self) -> None:
        pass


class TestCapabilityContext:
    def test_creation(self, tmp_path):
        ctx = CapabilityContext(
            identity_name="test",
            data_dir=tmp_path,
        )
        assert ctx.identity_name == "test"
        assert ctx.data_dir == tmp_path
        assert ctx.llm_client is None
        assert ctx.config == {}

    def test_creation_with_config(self, tmp_path):
        ctx = CapabilityContext(
            identity_name="test",
            data_dir=tmp_path,
            config={"dream_frequency": 2},
        )
        assert ctx.config["dream_frequency"] == 2

    def test_from_plugin_context(self, tmp_path):
        plugin_ctx = PluginContext(
            identity_name="anomal",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            event_bus="mock_bus",
            audit_log="mock_audit",
        )
        plugin_ctx.llm_client = "mock_llm"  # llm_client is a property, must use setter
        cap_ctx = CapabilityContext.from_plugin_context(
            plugin_ctx,
            config={"key": "val"},
        )
        assert cap_ctx.identity_name == "anomal"
        assert cap_ctx.llm_client == "mock_llm"
        assert cap_ctx.event_bus == "mock_bus"
        assert cap_ctx.audit_log == "mock_audit"
        assert cap_ctx.config == {"key": "val"}

    def test_from_plugin_context_defaults(self, tmp_path):
        plugin_ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        cap_ctx = CapabilityContext.from_plugin_context(plugin_ctx)
        assert cap_ctx.config == {}
        assert cap_ctx.llm_pipeline is None


class TestCapabilityBase:
    def test_cannot_instantiate_abstract(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        with pytest.raises(TypeError):
            CapabilityBase(ctx)

    def test_concrete_creation(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        assert cap.name == "test_cap"
        assert cap.enabled is True

    def test_enable_disable(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        assert cap.enabled is True
        cap.enabled = False
        assert cap.enabled is False
        cap.enabled = True
        assert cap.enabled is True

    @pytest.mark.asyncio
    async def test_setup(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        await cap.setup()
        assert cap.setup_called is True

    @pytest.mark.asyncio
    async def test_tick(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        await cap.tick()
        assert cap.tick_called is True

    @pytest.mark.asyncio
    async def test_on_event(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        await cap.on_event("post_created", post_id="123", score=42.0)
        assert cap.last_event == "post_created"
        assert cap.last_event_kwargs == {"post_id": "123", "score": 42.0}

    @pytest.mark.asyncio
    async def test_default_tick_is_noop(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = MinimalCapability(ctx)
        await cap.tick()  # Should not raise

    @pytest.mark.asyncio
    async def test_default_on_event_is_noop(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = MinimalCapability(ctx)
        await cap.on_event("anything")  # Should not raise

    @pytest.mark.asyncio
    async def test_default_teardown_is_noop(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = MinimalCapability(ctx)
        await cap.teardown()  # Should not raise

    def test_repr(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        r = repr(cap)
        assert "ConcreteCapability" in r
        assert "test_cap" in r
        assert "enabled" in r

    def test_repr_disabled(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = ConcreteCapability(ctx)
        cap.enabled = False
        r = repr(cap)
        assert "disabled" in r

    def test_get_prompt_context_default_returns_empty(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        cap = MinimalCapability(ctx)
        assert cap.get_prompt_context() == ""


class TestCapabilityContextGetSecret:
    def test_should_raise_when_no_getter(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        with pytest.raises(KeyError, match="Secrets not available"):
            ctx.get_secret("api_key")

    def test_should_return_value_when_getter_set(self, tmp_path):
        ctx = CapabilityContext(identity_name="test", data_dir=tmp_path)
        ctx._secrets_getter = lambda k: f"val_{k}"
        assert ctx.get_secret("api_key") == "val_api_key"


class TestCapabilityRegistry:
    def test_register_and_create(self, tmp_path):
        registry = CapabilityRegistry()
        registry.register("test_cap", ConcreteCapability)

        plugin_ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        cap = registry.create("test_cap", plugin_ctx, config={"key": "val"})
        assert cap is not None
        assert cap.name == "test_cap"

    def test_create_unknown_returns_none(self, tmp_path):
        registry = CapabilityRegistry()
        plugin_ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        cap = registry.create("nonexistent", plugin_ctx)
        assert cap is None

    def test_register_bundle_and_resolve(self):
        registry = CapabilityRegistry()
        registry.register("dream", ConcreteCapability)
        registry.register("therapy", MinimalCapability)
        registry.register_bundle("psychology", ["dream", "therapy"])

        resolved = registry.resolve(["psychology"])
        assert resolved == ["dream", "therapy"]

    def test_resolve_deduplicates(self):
        registry = CapabilityRegistry()
        registry.register("dream", ConcreteCapability)
        registry.register("therapy", MinimalCapability)
        registry.register_bundle("psychology", ["dream", "therapy"])

        resolved = registry.resolve(["psychology", "dream"])
        assert resolved == ["dream", "therapy"]

    def test_resolve_unknown_skipped(self):
        registry = CapabilityRegistry()
        resolved = registry.resolve(["unknown_cap"])
        assert resolved == []

    def test_create_all(self, tmp_path):
        registry = CapabilityRegistry()
        registry.register("test_cap", ConcreteCapability)
        registry.register("minimal", MinimalCapability)
        registry.register_bundle("all", ["test_cap", "minimal"])

        plugin_ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        caps = registry.create_all(
            ["all"],
            plugin_ctx,
            configs={"test_cap": {"key": "val"}},
        )
        assert len(caps) == 2

    def test_create_all_skips_unknown(self, tmp_path):
        registry = CapabilityRegistry()
        registry.register("test_cap", ConcreteCapability)

        plugin_ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        caps = registry.create_all(["test_cap", "nonexistent"], plugin_ctx)
        assert len(caps) == 1


class TestCapabilityRegistryDefault:
    def test_default_loads_builtins(self):
        registry = CapabilityRegistry.default()
        # Should have registered capabilities and bundles
        assert len(registry._registry) > 0


class TestBuildCapabilityConfigs:
    def test_builds_configs_from_identity(self):
        identity = MagicMock()
        identity.raw_config = {
            "dream_templates": ["template1"],
            "therapy_day": 5,
            "therapy_model": "llm",
            "emotional_model": "nuanced",
            "agent_name": "TestAgent",
            "mood_cycle": {"period": 7},
        }
        identity.interest_keywords = ["cats", "dogs"]
        identity.engagement_threshold = 0.8
        identity.name = "test"
        identity.llm.temperature = 0.7
        identity.llm.max_tokens = 2000

        configs = build_capability_configs(identity, "system prompt text")

        assert configs["dream_system"]["dream_templates"] == ["template1"]
        assert configs["therapy_system"]["therapy_day"] == 5
        assert configs["therapy_system"]["system_prompt"] == "system prompt text"
        assert configs["emotional_state"]["emotional_model"] == "nuanced"
        assert configs["analyzer"]["interest_keywords"] == ["cats", "dogs"]
        assert configs["analyzer"]["agent_name"] == "TestAgent"
        assert configs["composer"]["temperature"] == 0.7
        assert configs["mood_cycle"] == {"period": 7}
