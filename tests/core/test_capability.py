"""Tests for capability base class."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from blick.core.capability import CapabilityBase, CapabilityContext
from blick.core.plugin_base import PluginContext


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
            llm_client="mock_llm",
            event_bus="mock_bus",
            audit_log="mock_audit",
        )
        cap_ctx = CapabilityContext.from_plugin_context(
            plugin_ctx, config={"key": "val"},
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
