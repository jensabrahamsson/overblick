"""Tests for plugin base and context."""

import pytest
from pathlib import Path
from blick.core.plugin_base import PluginBase, PluginContext


class TestPluginContext:
    def test_creation(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.identity_name == "test"
        assert ctx.data_dir.exists()
        assert ctx.log_dir.exists()

    def test_get_secret_with_getter(self, tmp_path):
        secrets = {"api_key": "sk-123"}
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        ctx._secrets_getter = lambda k: secrets.get(k)
        assert ctx.get_secret("api_key") == "sk-123"
        assert ctx.get_secret("missing") is None

    def test_get_secret_no_getter(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.get_secret("anything") is None

    def test_dirs_created_automatically(self, tmp_path):
        data = tmp_path / "deep" / "data"
        logs = tmp_path / "deep" / "logs"
        ctx = PluginContext(identity_name="test", data_dir=data, log_dir=logs)
        assert data.exists()
        assert logs.exists()


class TestPluginBase:
    def test_concrete_plugin(self, tmp_path):
        class TestPlugin(PluginBase):
            async def setup(self):
                pass

            async def tick(self):
                pass

        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        plugin = TestPlugin(ctx)
        assert plugin.name == "TestPlugin"
        assert "test" in repr(plugin)

    @pytest.mark.asyncio
    async def test_teardown_default_is_noop(self, tmp_path):
        class TestPlugin(PluginBase):
            async def setup(self):
                pass

            async def tick(self):
                pass

        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        plugin = TestPlugin(ctx)
        await plugin.teardown()  # Should not raise

    def test_cannot_instantiate_abstract(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        with pytest.raises(TypeError):
            PluginBase(ctx)
