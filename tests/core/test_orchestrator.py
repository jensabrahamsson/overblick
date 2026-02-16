"""Tests for orchestrator state machine, LLM routing, and IPC client discovery."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from overblick.core.orchestrator import Orchestrator, OrchestratorState
from overblick.identities import Identity, LLMSettings


class TestOrchestratorState:
    def test_enum_values(self):
        assert OrchestratorState.INIT.value == "init"
        assert OrchestratorState.RUNNING.value == "running"
        assert OrchestratorState.STOPPED.value == "stopped"


class TestOrchestratorInit:
    def test_initial_state(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch.state == OrchestratorState.INIT
        assert orch.identity is None

    def test_default_plugins(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        assert orch._plugin_names == ["moltbook"]

    def test_custom_plugins(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path, plugins=["moltbook", "gmail"])
        assert orch._plugin_names == ["moltbook", "gmail"]


class TestOrchestratorStop:
    @pytest.mark.asyncio
    async def test_stop_from_init(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        await orch.stop()
        assert orch.state == OrchestratorState.STOPPED

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        await orch.stop()
        await orch.stop()  # Should not raise
        assert orch.state == OrchestratorState.STOPPED


class TestOrchestratorLLMRouting:
    """Tests for _create_llm_client provider routing."""

    @pytest.mark.asyncio
    async def test_routes_to_ollama(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity = Identity(
            name="test",
            llm=LLMSettings(provider="ollama"),
        )
        with patch("overblick.core.llm.ollama_client.OllamaClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.health_check = AsyncMock(return_value=True)
            mock_cls.return_value = mock_instance
            client = await orch._create_llm_client()
            mock_cls.assert_called_once()
            assert client is mock_instance

    @pytest.mark.asyncio
    async def test_routes_to_gateway(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity = Identity(
            name="test",
            llm=LLMSettings(provider="gateway", gateway_url="http://10.0.0.1:8200"),
        )
        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.health_check = AsyncMock(return_value=True)
            mock_cls.return_value = mock_instance
            client = await orch._create_llm_client()
            mock_cls.assert_called_once()
            assert client is mock_instance

    @pytest.mark.asyncio
    async def test_routes_to_cloud(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity_name = "test"
        orch._identity = Identity(
            name="test",
            llm=LLMSettings(
                provider="cloud",
                cloud_api_url="https://api.openai.com/v1",
                cloud_model="gpt-4o",
            ),
        )
        with patch("overblick.core.llm.cloud_client.CloudLLMClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.health_check = AsyncMock(return_value=True)
            mock_cls.return_value = mock_instance
            client = await orch._create_llm_client()
            mock_cls.assert_called_once()
            assert client is mock_instance

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self, tmp_path):
        orch = Orchestrator("anomal", base_dir=tmp_path)
        orch._identity = Identity(
            name="test",
            llm=LLMSettings.model_validate({"provider": "nonexistent"}),
        )
        with pytest.raises(ValueError, match="Unknown LLM provider: nonexistent"):
            await orch._create_llm_client()


class TestOrchestratorIPCDiscovery:
    """Tests for _create_ipc_client multi-location token search."""

    def test_ipc_client_from_env_var(self, tmp_path, monkeypatch):
        """OVERBLICK_IPC_DIR env var takes highest priority."""
        ipc_dir = tmp_path / "env_ipc"
        ipc_dir.mkdir()
        token_file = ipc_dir / "overblick-supervisor.token"
        token_file.write_text("test-token-env")

        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(ipc_dir))

        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()

        assert client is not None
        assert client._auth_token == "test-token-env"
        assert client._socket_dir == ipc_dir

    def test_ipc_client_from_project_path(self, tmp_path, monkeypatch):
        """Falls back to base_dir/data/ipc/ when no env var."""
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)

        ipc_dir = tmp_path / "data" / "ipc"
        ipc_dir.mkdir(parents=True)
        token_file = ipc_dir / "overblick-supervisor.token"
        token_file.write_text("test-token-project")

        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()

        assert client is not None
        assert client._auth_token == "test-token-project"
        assert client._socket_dir == ipc_dir

    def test_ipc_client_env_var_overrides_project_path(self, tmp_path, monkeypatch):
        """Env var wins over project path when both have tokens."""
        # Set up both locations with tokens
        env_dir = tmp_path / "env_ipc"
        env_dir.mkdir()
        (env_dir / "overblick-supervisor.token").write_text("env-token")

        project_dir = tmp_path / "data" / "ipc"
        project_dir.mkdir(parents=True)
        (project_dir / "overblick-supervisor.token").write_text("project-token")

        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(env_dir))

        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()

        assert client is not None
        assert client._auth_token == "env-token"

    def test_ipc_client_no_token_returns_none(self, tmp_path, monkeypatch):
        """Returns None when no supervisor token found anywhere."""
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)

        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()

        assert client is None

    def test_ipc_client_env_var_dir_without_token(self, tmp_path, monkeypatch):
        """Env var dir exists but has no token — falls through to next."""
        env_dir = tmp_path / "env_ipc"
        env_dir.mkdir()
        # No token file in env_dir

        project_dir = tmp_path / "data" / "ipc"
        project_dir.mkdir(parents=True)
        (project_dir / "overblick-supervisor.token").write_text("project-token")

        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(env_dir))

        orch = Orchestrator("anomal", base_dir=tmp_path)
        client = orch._create_ipc_client()

        assert client is not None
        assert client._auth_token == "project-token"
