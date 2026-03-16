"""Tests for ComponentFactory — dependency injection for Orchestrator components."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from overblick.core.component_factory import ComponentFactory
from overblick.identities import Identity, LLMSettings, SecuritySettings


def _make_identity(**overrides):
    """Create a minimal Identity for testing."""
    defaults = {
        "name": "testbot",
        "display_name": "TestBot",
        "version": "2.0",
        "plugins": (),
        "quiet_hours": MagicMock(),
        "llm": LLMSettings(),
        "security": SecuritySettings(),
        "deflections": {},
        "personality": {},
        "raw_config": {},
    }
    defaults.update(overrides)
    identity = MagicMock(spec=Identity)
    for k, v in defaults.items():
        setattr(identity, k, v)
    return identity


class TestComponentFactoryInit:
    def test_should_store_identity_name_and_base_dir(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        assert factory._identity_name == "anomal"
        assert factory._base_dir == Path("/tmp/test")
        assert factory._identity is None
        assert factory._paths is None


class TestLoadIdentity:
    async def test_should_load_identity_and_cache(self):
        mock_identity = _make_identity()
        with patch("overblick.core.component_factory.load_identity", return_value=mock_identity) as mock_load:
            factory = ComponentFactory("anomal", Path("/tmp/test"))
            result = await factory.load_identity()
            assert result is mock_identity
            mock_load.assert_called_once_with("anomal")

            # Second call uses cache
            result2 = await factory.load_identity()
            assert result2 is mock_identity
            mock_load.assert_called_once()  # Still only one call


class TestGetPaths:
    def test_should_create_dirs_and_return_paths(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        paths = factory.get_paths()

        assert paths["data_dir"] == tmp_path / "data" / "anomal"
        assert paths["log_dir"] == tmp_path / "logs" / "anomal"
        assert paths["secrets_dir"] == tmp_path / "config" / "secrets"
        assert paths["data_dir"].exists()
        assert paths["log_dir"].exists()

    def test_should_cache_paths(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        paths1 = factory.get_paths()
        paths2 = factory.get_paths()
        assert paths1 is paths2


class TestCreateSecretsManager:
    def test_should_create_secrets_manager(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        with patch("overblick.core.component_factory.SecretsManager") as mock_cls:
            sm = factory.create_secrets_manager()
            mock_cls.assert_called_once_with(tmp_path / "config" / "secrets")
            assert sm is mock_cls.return_value


class TestCreateAuditLog:
    def test_should_create_audit_log(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        with patch("overblick.core.component_factory.AuditLog") as mock_cls:
            al = factory.create_audit_log()
            mock_cls.assert_called_once_with(
                tmp_path / "data" / "anomal" / "audit.db", "anomal"
            )
            assert al is mock_cls.return_value


class TestCreateEngagementDB:
    async def test_should_return_none_when_no_moltbook_plugin(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        identity = _make_identity(plugins=("telegram",))
        result = await factory.create_engagement_db(identity)
        assert result is None

    async def test_should_return_none_when_plugins_is_none(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        identity = _make_identity(plugins=None)
        result = await factory.create_engagement_db(identity)
        assert result is None

    async def test_should_return_none_when_plugins_is_empty(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        identity = _make_identity(plugins=())
        result = await factory.create_engagement_db(identity)
        assert result is None

    async def test_should_create_engagement_db_when_moltbook_plugin(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        identity = _make_identity(plugins=("moltbook", "telegram"))

        mock_backend = AsyncMock()
        mock_eng_db = AsyncMock()

        with (
            patch("overblick.core.component_factory.DatabaseConfig") as mock_db_config,
            patch("overblick.core.component_factory.SQLiteBackend", return_value=mock_backend) as mock_sqlite,
            patch("overblick.core.component_factory.EngagementDB", return_value=mock_eng_db) as mock_eng_cls,
        ):
            result = await factory.create_engagement_db(identity)

            mock_db_config.assert_called_once_with(
                sqlite_path=str(tmp_path / "data" / "anomal" / "engagement.db")
            )
            mock_sqlite.assert_called_once_with(mock_db_config.return_value, identity="anomal")
            mock_backend.connect.assert_awaited_once()
            mock_eng_cls.assert_called_once_with(mock_backend, identity="anomal")
            mock_eng_db.setup.assert_awaited_once()
            assert result is mock_eng_db


class TestCreateQuietHoursChecker:
    def test_should_create_quiet_hours_checker(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity()
        with patch("overblick.core.component_factory.QuietHoursChecker") as mock_cls:
            result = factory.create_quiet_hours_checker(identity)
            mock_cls.assert_called_once_with(identity.quiet_hours)
            assert result is mock_cls.return_value


class TestCreateLLMClient:
    async def test_should_create_gateway_client_healthy(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity()

        mock_client = AsyncMock()
        mock_client.health_check.return_value = True

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = Mock(return_value=None)
        mock_ctx.__exit__ = Mock(return_value=False)

        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_cls.return_value = mock_client
            mock_cls._instantiation_allowed.return_value = mock_ctx
            result = await factory.create_llm_client(identity)

            assert result is mock_client
            mock_client.health_check.assert_awaited_once()

    async def test_should_warn_when_gateway_not_reachable(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity()

        mock_client = AsyncMock()
        mock_client.health_check.return_value = False

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = Mock(return_value=None)
        mock_ctx.__exit__ = Mock(return_value=False)

        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_cls.return_value = mock_client
            mock_cls._instantiation_allowed.return_value = mock_ctx
            result = await factory.create_llm_client(identity)

            assert result is mock_client

    async def test_should_use_custom_gateway_url(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            llm=LLMSettings(gateway_url="http://custom:9999")
        )

        mock_client = AsyncMock()
        mock_client.health_check.return_value = True

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = Mock(return_value=None)
        mock_ctx.__exit__ = Mock(return_value=False)

        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_cls.return_value = mock_client
            mock_cls._instantiation_allowed.return_value = mock_ctx
            await factory.create_llm_client(identity)

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "http://custom:9999"

    async def test_should_use_default_gateway_url_when_none(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        llm_settings = MagicMock()
        llm_settings.gateway_url = None
        llm_settings.model = "qwen3:8b"
        llm_settings.max_tokens = 2000
        llm_settings.temperature = 0.7
        llm_settings.top_p = 0.9
        llm_settings.timeout_seconds = 180
        identity = _make_identity(llm=llm_settings)

        mock_client = AsyncMock()
        mock_client.health_check.return_value = True

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = Mock(return_value=None)
        mock_ctx.__exit__ = Mock(return_value=False)

        with patch("overblick.core.llm.gateway_client.GatewayClient") as mock_cls:
            mock_cls.return_value = mock_client
            mock_cls._instantiation_allowed.return_value = mock_ctx
            await factory.create_llm_client(identity)

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "http://127.0.0.1:8200"


class TestCreatePreflightChecker:
    def test_should_return_none_when_disabled(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_preflight=False)
        )
        result = factory.create_preflight_checker(identity, Mock())
        assert result is None

    def test_should_create_preflight_checker(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(
                enable_preflight=True,
                admin_user_ids=("admin1", "admin2"),
            ),
            deflections={"jailbreak": ["Nice try!"]},
        )
        with patch("overblick.core.component_factory.PreflightChecker") as mock_cls:
            llm = Mock()
            result = factory.create_preflight_checker(identity, llm)
            mock_cls.assert_called_once_with(
                llm_client=llm,
                admin_user_ids={"admin1", "admin2"},
                deflections={"jailbreak": ["Nice try!"]},
            )
            assert result is mock_cls.return_value

    def test_should_use_empty_dict_when_deflections_not_dict(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_preflight=True),
            deflections=["some", "list"],
        )
        with patch("overblick.core.component_factory.PreflightChecker") as mock_cls:
            factory.create_preflight_checker(identity, Mock())
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["deflections"] == {}


class TestCreateOutputSafety:
    def test_should_return_none_when_disabled(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_output_safety=False)
        )
        result = factory.create_output_safety(identity)
        assert result is None

    def test_should_create_output_safety_with_personality(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            personality={
                "vocabulary": {
                    "banned_words": ["yolo", "bruh"],
                    "slang_replacements": {"gonna": "going to"},
                }
            },
            deflections=["Sorry, I can't do that."],
        )
        with patch("overblick.core.component_factory.OutputSafety") as mock_cls:
            result = factory.create_output_safety(identity)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["identity_name"] == "anomal"
            assert call_kwargs["banned_slang_patterns"] == [r"\byolo\b", r"\bbruh\b"]
            assert call_kwargs["slang_replacements"] == {"gonna": "going to"}
            assert call_kwargs["deflections"] == ["Sorry, I can't do that."]
            assert result is mock_cls.return_value

    def test_should_handle_no_personality(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            personality=None,
            deflections={},
        )
        with patch("overblick.core.component_factory.OutputSafety") as mock_cls:
            factory.create_output_safety(identity)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["banned_slang_patterns"] == []
            assert call_kwargs["slang_replacements"] == {}
            assert call_kwargs["deflections"] is None

    def test_should_handle_personality_without_vocabulary(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            personality={"some_other": "stuff"},
            deflections=["deflect"],
        )
        with patch("overblick.core.component_factory.OutputSafety") as mock_cls:
            factory.create_output_safety(identity)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["banned_slang_patterns"] == []
            assert call_kwargs["slang_replacements"] == {}

    def test_should_pass_none_deflections_when_empty_list(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            personality={},
            deflections=[],
        )
        with patch("overblick.core.component_factory.OutputSafety") as mock_cls:
            factory.create_output_safety(identity)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["deflections"] is None

    def test_should_handle_deflections_as_dict_not_list(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(enable_output_safety=True),
            personality={},
            deflections={"key": "value"},
        )
        with patch("overblick.core.component_factory.OutputSafety") as mock_cls:
            factory.create_output_safety(identity)
            call_kwargs = mock_cls.call_args.kwargs
            # dict is not a list, so deflection_list is []
            assert call_kwargs["deflections"] is None


class TestCreateRateLimiter:
    def test_should_create_rate_limiter(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(
            security=SecuritySettings(
                rate_limiter_max_tokens=20.0,
                rate_limiter_refill_rate=1.0,
            )
        )
        with patch("overblick.core.component_factory.RateLimiter") as mock_cls:
            result = factory.create_rate_limiter(identity)
            mock_cls.assert_called_once_with(max_tokens=20.0, refill_rate=1.0)
            assert result is mock_cls.return_value


class TestCreateSafeLLMPipeline:
    def test_should_create_pipeline(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        llm = Mock()
        audit = Mock()
        preflight = Mock()
        output = Mock()
        rate = Mock()
        identity = _make_identity()

        with patch("overblick.core.component_factory.SafeLLMPipeline") as mock_cls:
            result = factory.create_safe_llm_pipeline(
                llm, audit, preflight, output, rate, identity
            )
            mock_cls.assert_called_once_with(
                llm_client=llm,
                audit_log=audit,
                preflight_checker=preflight,
                output_safety=output,
                rate_limiter=rate,
                identity_name="anomal",
                strict=True,
            )
            assert result is mock_cls.return_value

    def test_should_accept_none_preflight_and_output_safety(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        with patch("overblick.core.component_factory.SafeLLMPipeline") as mock_cls:
            factory.create_safe_llm_pipeline(
                Mock(), Mock(), None, None, Mock(), _make_identity()
            )
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["preflight_checker"] is None
            assert call_kwargs["output_safety"] is None


class TestCreatePermissionChecker:
    def test_should_create_permission_checker(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity()
        with patch("overblick.core.component_factory.PermissionChecker") as mock_cls:
            result = factory.create_permission_checker(identity)
            mock_cls.from_identity.assert_called_once_with(identity)
            assert result is mock_cls.from_identity.return_value


class TestCreatePluginCapabilityChecker:
    def test_should_create_plugin_capability_checker(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        identity = _make_identity(raw_config={"some": "config"})
        with patch("overblick.core.component_factory.PluginCapabilityChecker") as mock_cls:
            result = factory.create_plugin_capability_checker(identity)
            mock_cls.assert_called_once_with(
                identity_name="anomal",
                raw_config={"some": "config"},
            )
            assert result is mock_cls.return_value


class TestCreateIPCClient:
    def test_should_return_none_when_no_token_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)
        factory = ComponentFactory("anomal", tmp_path)
        result = factory.create_ipc_client()
        assert result is None

    def test_should_find_token_in_env_dir(self, tmp_path, monkeypatch):
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "overblick-supervisor.token").write_text("secret")
        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(ipc_dir))

        factory = ComponentFactory("anomal", tmp_path)
        with patch("overblick.supervisor.ipc.IPCClient") as mock_cls:
            result = factory.create_ipc_client()
            mock_cls.assert_called_once_with(
                token_path=ipc_dir / "overblick-supervisor.token",
                identity="anomal",
            )
            assert result is mock_cls.return_value

    def test_should_find_token_in_data_ipc_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)
        ipc_dir = tmp_path / "data" / "ipc"
        ipc_dir.mkdir(parents=True)
        (ipc_dir / "overblick-supervisor.token").write_text("secret")

        factory = ComponentFactory("anomal", tmp_path)
        with patch("overblick.supervisor.ipc.IPCClient") as mock_cls:
            result = factory.create_ipc_client()
            mock_cls.assert_called_once()
            assert result is mock_cls.return_value

    def test_should_find_token_in_tempdir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERBLICK_IPC_DIR", raising=False)
        # Make sure data/ipc doesn't have the token
        with patch("tempfile.gettempdir", return_value=str(tmp_path / "temp")):
            temp_ipc = tmp_path / "temp" / "overblick"
            temp_ipc.mkdir(parents=True)
            (temp_ipc / "overblick-supervisor.token").write_text("secret")

            factory = ComponentFactory("anomal", tmp_path)
            with patch("overblick.supervisor.ipc.IPCClient") as mock_cls:
                result = factory.create_ipc_client()
                mock_cls.assert_called_once()
                assert result is mock_cls.return_value

    def test_should_prefer_env_dir_over_data_dir(self, tmp_path, monkeypatch):
        # Both env dir and data/ipc have tokens
        env_dir = tmp_path / "env_ipc"
        env_dir.mkdir()
        (env_dir / "overblick-supervisor.token").write_text("env-secret")

        data_dir = tmp_path / "data" / "ipc"
        data_dir.mkdir(parents=True)
        (data_dir / "overblick-supervisor.token").write_text("data-secret")

        monkeypatch.setenv("OVERBLICK_IPC_DIR", str(env_dir))

        factory = ComponentFactory("anomal", tmp_path)
        with patch("overblick.supervisor.ipc.IPCClient") as mock_cls:
            factory.create_ipc_client()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["token_path"] == env_dir / "overblick-supervisor.token"


class TestCreateEventBus:
    def test_should_create_event_bus(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        with patch("overblick.core.component_factory.EventBus") as mock_cls:
            result = factory.create_event_bus()
            mock_cls.assert_called_once()
            assert result is mock_cls.return_value


class TestCreateScheduler:
    def test_should_create_scheduler(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        with patch("overblick.core.component_factory.Scheduler") as mock_cls:
            result = factory.create_scheduler()
            mock_cls.assert_called_once()
            assert result is mock_cls.return_value


class TestCreatePluginRegistry:
    def test_should_create_and_register_local_plugins(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        with (
            patch("overblick.core.component_factory.PluginRegistry") as mock_cls,
            patch.object(factory, "_register_local_plugins") as mock_register,
        ):
            result = factory.create_plugin_registry()
            mock_cls.assert_called_once()
            mock_register.assert_called_once_with(mock_cls.return_value)
            assert result is mock_cls.return_value


class TestCreateCapabilityRegistry:
    def test_should_create_capability_registry(self):
        factory = ComponentFactory("anomal", Path("/tmp/test"))
        with patch("overblick.core.component_factory.CapabilityRegistry") as mock_cls:
            result = factory.create_capability_registry()
            mock_cls.assert_called_once()
            assert result is mock_cls.return_value


class TestRegisterLocalPlugins:
    def test_should_skip_when_local_dir_missing(self, tmp_path):
        factory = ComponentFactory("anomal", tmp_path)
        registry = Mock()
        factory._register_local_plugins(registry)
        registry.register_local.assert_not_called()

    def test_should_register_plugin_dirs(self, tmp_path):
        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        local_dir.mkdir(parents=True)
        (local_dir / "my_plugin").mkdir()
        (local_dir / "another_plugin").mkdir()

        factory = ComponentFactory("anomal", tmp_path)
        registry = Mock()
        factory._register_local_plugins(registry)

        assert registry.register_local.call_count == 2
        call_args_list = [c.args for c in registry.register_local.call_args_list]
        assert ("my_plugin", "overblick.plugins._local.my_plugin.plugin") in call_args_list
        assert ("another_plugin", "overblick.plugins._local.another_plugin.plugin") in call_args_list

    def test_should_skip_non_directory_entries(self, tmp_path):
        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        local_dir.mkdir(parents=True)
        (local_dir / "not_a_dir.txt").write_text("file")
        (local_dir / "real_plugin").mkdir()

        factory = ComponentFactory("anomal", tmp_path)
        registry = Mock()
        factory._register_local_plugins(registry)

        registry.register_local.assert_called_once_with(
            "real_plugin", "overblick.plugins._local.real_plugin.plugin"
        )

    def test_should_handle_registration_failure(self, tmp_path):
        local_dir = tmp_path / "overblick" / "plugins" / "_local"
        local_dir.mkdir(parents=True)
        (local_dir / "bad_plugin").mkdir()

        factory = ComponentFactory("anomal", tmp_path)
        registry = Mock()
        registry.register_local.side_effect = Exception("Import failed")
        factory._register_local_plugins(registry)
        # Should not raise — just log warning
        registry.register_local.assert_called_once()
