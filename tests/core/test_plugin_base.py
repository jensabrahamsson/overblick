"""Tests for plugin base and context."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.exceptions import SecurityError
from overblick.core.plugin_base import (
    AgenticPluginContext,
    CommunicationPluginContext,
    ContentPluginContext,
    DefaultPluginContext,
    MonitoringPluginContext,
    PluginBase,
    PluginContext,
    PluginHealthReport,
)


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
        PluginContext(identity_name="test", data_dir=data, log_dir=logs)
        assert data.exists()
        assert logs.exists()

    def test_get_capability(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            capabilities={"email": "mock_email_cap"},
        )
        assert ctx.get_capability("email") == "mock_email_cap"
        assert ctx.get_capability("nonexistent") is None

    def test_llm_client_raises_security_error_when_raw_forbidden(self, tmp_path):
        """Accessing llm_client raises SecurityError when OVERBLICK_RAW_LLM is not set."""
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        ctx._llm_client = MagicMock()
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "0"}):
            with pytest.raises(SecurityError, match="FORBIDDEN"):
                _ = ctx.llm_client

    def test_llm_client_returns_client_when_raw_allowed(self, tmp_path):
        """Accessing llm_client succeeds when OVERBLICK_RAW_LLM=1."""
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_client = MagicMock()
        ctx._llm_client = mock_client
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "1"}):
            result = ctx.llm_client
            assert result is mock_client

    def test_llm_client_setter(self, tmp_path):
        """llm_client setter stores the client."""
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_client = MagicMock()
        ctx.llm_client = mock_client
        assert ctx._llm_client is mock_client

    def test_load_identity(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        with patch("overblick.identities.load_identity") as mock_load:
            mock_load.return_value = "mock_identity"
            result = ctx.load_identity("anomal")
            mock_load.assert_called_once_with("anomal")
            assert result == "mock_identity"

    def test_build_system_prompt(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        ctx._secrets_getter = lambda k: "secret_val"
        mock_identity = MagicMock()
        with patch("overblick.identities.build_system_prompt") as mock_build:
            mock_build.return_value = "System prompt text"
            result = ctx.build_system_prompt(
                mock_identity, platform="Telegram", model_slug="qwen3"
            )
            mock_build.assert_called_once()
            assert result == "System prompt text"

    @pytest.mark.asyncio
    async def test_send_to_agent_no_ipc(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        result = await ctx.send_to_agent("smed", "bug_report", {"data": "val"})
        assert result is None

    @pytest.mark.asyncio
    async def test_send_to_agent_with_ipc(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_ipc = AsyncMock()
        mock_response = MagicMock()
        mock_response.payload = {"success": True, "message_id": "123"}
        mock_ipc.send = AsyncMock(return_value=mock_response)
        ctx.ipc_client = mock_ipc

        result = await ctx.send_to_agent("smed", "bug_report", {"data": "val"})
        assert result == {"success": True, "message_id": "123"}

    @pytest.mark.asyncio
    async def test_send_to_agent_null_response(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=None)
        ctx.ipc_client = mock_ipc

        result = await ctx.send_to_agent("smed", "bug_report")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_ipc_message_no_ipc(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        result = await ctx.send_ipc_message("test_type")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_ipc_message_with_ipc(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_ipc = AsyncMock()
        mock_response = MagicMock()
        mock_ipc.send = AsyncMock(return_value=mock_response)
        ctx.ipc_client = mock_ipc

        result = await ctx.send_ipc_message("test_type", payload={"key": "val"})
        assert result is mock_response

    @pytest.mark.asyncio
    async def test_collect_messages_no_ipc(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        result = await ctx.collect_messages()
        assert result == []

    @pytest.mark.asyncio
    async def test_collect_messages_with_ipc(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_ipc = AsyncMock()
        mock_response = MagicMock()
        mock_response.payload = {"messages": [{"id": "1", "type": "alert"}]}
        mock_ipc.send = AsyncMock(return_value=mock_response)
        ctx.ipc_client = mock_ipc

        result = await ctx.collect_messages()
        assert len(result) == 1
        assert result[0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_collect_messages_null_response(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=None)
        ctx.ipc_client = mock_ipc

        result = await ctx.collect_messages()
        assert result == []


class TestRoleSpecificContexts:
    def test_agentic_context(self, tmp_path):
        ctx = AgenticPluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.ROLE == "agentic"

    def test_communication_context(self, tmp_path):
        ctx = CommunicationPluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.ROLE == "communication"

    def test_content_context(self, tmp_path):
        ctx = ContentPluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.ROLE == "content"

    def test_monitoring_context(self, tmp_path):
        ctx = MonitoringPluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.ROLE == "monitoring"

    def test_default_context(self, tmp_path):
        ctx = DefaultPluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.ROLE == "default"


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

    @pytest.mark.asyncio
    async def test_check_health_default(self, tmp_path):
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
        report = await plugin.check_health()
        assert isinstance(report, PluginHealthReport)
        assert report.status == "unknown"
        assert report.error_count == 0

    def test_cannot_instantiate_abstract(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        with pytest.raises(TypeError):
            PluginBase(ctx)
