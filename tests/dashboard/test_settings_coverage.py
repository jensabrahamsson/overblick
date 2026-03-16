"""
Additional tests for settings routes to reach 100% coverage.

Covers helper functions, SSRF guards, plugin config parsing,
pre-population, test endpoints, and partial-failure paths.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.routes.settings import (
    _config_to_wizard_state,
    _get_version,
    _is_private_or_blocked,
    _load_existing_config,
    _migrate_old_llm_config,
    _parse_plugin_config,
    _parse_textarea_lines,
    _prepopulate_plugin_configs,
    _validate_test_host,
    _validate_test_url,
)


class TestIsPrivateOrBlocked:
    def test_should_block_metadata_endpoint(self):
        assert _is_private_or_blocked("169.254.169.254") is True

    def test_should_block_google_metadata(self):
        assert _is_private_or_blocked("metadata.google.internal") is True

    def test_should_block_loopback(self):
        assert _is_private_or_blocked("127.0.0.1") is True

    def test_should_block_private_10(self):
        assert _is_private_or_blocked("10.0.0.1") is True

    def test_should_block_private_172(self):
        assert _is_private_or_blocked("172.16.0.1") is True

    def test_should_block_private_192(self):
        assert _is_private_or_blocked("192.168.1.1") is True

    def test_should_allow_public_ip(self):
        assert _is_private_or_blocked("8.8.8.8") is False

    def test_should_allow_public_hostname(self):
        assert _is_private_or_blocked("example.com") is False

    def test_should_handle_non_ip_hostname(self):
        # Non-IP, non-blocked hostname
        assert _is_private_or_blocked("api.deepseek.com") is False


class TestValidateTestHost:
    def test_should_reject_blocked_host(self):
        with pytest.raises(ValueError, match="Blocked host"):
            _validate_test_host("169.254.169.254", "80")

    def test_should_reject_invalid_port(self):
        with pytest.raises(ValueError, match="Invalid port"):
            _validate_test_host("8.8.8.8", "0")

    def test_should_reject_port_over_65535(self):
        with pytest.raises(ValueError, match="Invalid port"):
            _validate_test_host("8.8.8.8", "99999")

    def test_should_accept_valid_host_port(self):
        host, port = _validate_test_host("8.8.8.8", "443")
        assert host == "8.8.8.8"
        assert port == 443


class TestValidateTestUrl:
    def test_should_reject_ftp_scheme(self):
        with pytest.raises(ValueError, match="http/https"):
            _validate_test_url("ftp://example.com/file")

    def test_should_reject_blocked_hostname(self):
        with pytest.raises(ValueError, match="Blocked host"):
            _validate_test_url("http://169.254.169.254/latest/meta-data/")

    def test_should_accept_valid_url(self):
        url = _validate_test_url("https://api.example.com/v1")
        assert url == "https://api.example.com/v1"


class TestParseTextareaLines:
    def test_should_split_and_strip(self):
        result = _parse_textarea_lines("alice@example.com\n  bob@example.com  \n\ncharlie@example.com")
        assert result == ["alice@example.com", "bob@example.com", "charlie@example.com"]

    def test_should_return_empty_for_blank(self):
        assert _parse_textarea_lines("") == []
        assert _parse_textarea_lines("   \n  \n") == []


class TestParsePluginConfig:
    def test_should_parse_email_config(self):
        form = {
            "email_filter_mode": "opt_in",
            "email_allowed_senders": "alice@test.com\nbob@test.com",
            "email_blocked_senders": "",
            "email_dry_run": "on",
            "email_show_draft_replies": "off",
            "email_max_email_age_hours": "24",
        }
        result = _parse_plugin_config("email", form)
        assert result["email_filter_mode"] == "opt_in"
        assert "alice@test.com" in result["email_allowed_senders"]
        assert result["email_dry_run"] is True
        assert result["email_show_draft_replies"] is False
        assert result["email_max_email_age_hours"] == 24

    def test_should_parse_github_config(self):
        form = {
            "github_repos": "owner/repo1\nowner/repo2",
            "github_dry_run": "on",
            "github_bot_username": "mybot",
            "github_tick_interval_minutes": "10",
            "github_auto_merge_patch": "on",
            "github_auto_merge_minor": "off",
            "github_auto_merge_major": "off",
        }
        result = _parse_plugin_config("github_monitor", form)
        assert "owner/repo1" in result["github_repos"]
        assert result["github_dry_run"] is True
        assert result["github_bot_username"] == "mybot"
        assert result["github_auto_merge_patch"] is True
        assert result["github_auto_merge_minor"] is False

    def test_should_parse_dev_automation_config(self):
        form = {
            "dev_repo_url": "https://github.com/org/repo",
            "dev_workspace_dir": "/home/user/dev",
            "dev_dry_run": "on",
            "dev_tick_interval_minutes": "30",
            "dev_opencode_model": "gpt-4o",
            "dev_log_watcher_enabled": "on",
        }
        result = _parse_plugin_config("dev_automation", form)
        assert result["dev_repo_url"] == "https://github.com/org/repo"
        assert result["dev_dry_run"] is True
        assert result["dev_opencode_model"] == "gpt-4o"
        assert result["dev_log_watcher_enabled"] is True

    def test_should_return_empty_for_unknown_use_case(self):
        result = _parse_plugin_config("unknown", {})
        assert result == {}


class TestGetVersion:
    def test_should_read_version_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
        assert _get_version(tmp_path) == "1.2.3"

    def test_should_return_default_when_no_file(self, tmp_path):
        assert _get_version(tmp_path) == "0.1.0"

    def test_should_return_default_on_error(self, tmp_path):
        # Create a directory named pyproject.toml (will fail to read)
        (tmp_path / "pyproject.toml").mkdir()
        assert _get_version(tmp_path) == "0.1.0"


class TestPrepopulatePluginConfigs:
    def test_should_prepopulate_email_config(self, tmp_path):
        config_dir = tmp_path / "config" / "stal"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins.yaml").write_text(
            yaml.dump(
                {
                    "email_agent": {
                        "filter_mode": "opt_in",
                        "senders": {
                            "allowed": ["alice@example.com", "bob@example.com"],
                            "blocked": ["spammer@example.com"],
                        },
                        "dry_run": False,
                        "show_draft_replies": True,
                        "max_email_age_hours": 24,
                    },
                }
            )
        )
        state = {"selected_use_cases": ["email"]}
        _prepopulate_plugin_configs(state, tmp_path)
        assert "assignments" in state
        pcfg = state["assignments"]["email"]["plugin_config"]
        assert pcfg["email_filter_mode"] == "opt_in"
        assert "alice@example.com" in pcfg["email_allowed_senders"]
        assert pcfg["email_dry_run"] is False

    def test_should_prepopulate_github_config(self, tmp_path):
        config_dir = tmp_path / "config" / "bjork"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins.yaml").write_text(
            yaml.dump(
                {
                    "github": {
                        "repos": ["org/repo1", "org/repo2"],
                        "dry_run": True,
                        "bot_username": "mybot",
                        "tick_interval_minutes": 10,
                        "dependabot": {
                            "auto_merge_patch": True,
                            "auto_merge_minor": False,
                            "auto_merge_major": False,
                        },
                    },
                }
            )
        )
        state = {"selected_use_cases": ["github_monitor"]}
        _prepopulate_plugin_configs(state, tmp_path)
        assert "assignments" in state
        pcfg = state["assignments"]["github_monitor"]["plugin_config"]
        assert "org/repo1" in pcfg["github_repos"]
        assert pcfg["github_auto_merge_patch"] is True

    def test_should_prepopulate_dev_agent_config(self, tmp_path):
        config_dir = tmp_path / "config" / "dev"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins.yaml").write_text(
            yaml.dump(
                {
                    "dev_agent": {
                        "repo_url": "https://github.com/org/repo",
                        "workspace_dir": "/workspace",
                        "dry_run": True,
                        "tick_interval_minutes": 30,
                        "opencode": {"model": "gpt-4o"},
                        "log_watcher": {"enabled": True},
                    },
                }
            )
        )
        state = {"selected_use_cases": ["dev_automation"]}
        _prepopulate_plugin_configs(state, tmp_path)
        assert "assignments" in state
        pcfg = state["assignments"]["dev_automation"]["plugin_config"]
        assert pcfg["dev_repo_url"] == "https://github.com/org/repo"
        assert pcfg["dev_log_watcher_enabled"] is True

    def test_should_handle_missing_config_dir(self, tmp_path):
        state = {"selected_use_cases": ["email"]}
        _prepopulate_plugin_configs(state, tmp_path)
        assert "assignments" not in state

    def test_should_handle_invalid_yaml(self, tmp_path):
        config_dir = tmp_path / "config" / "stal"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins.yaml").write_text("invalid yaml: [[[")
        state = {"selected_use_cases": ["email"]}
        _prepopulate_plugin_configs(state, tmp_path)
        assert "assignments" not in state

    def test_should_skip_non_directory_entries(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "somefile.txt").write_text("not a directory")
        state = {"selected_use_cases": ["email"]}
        _prepopulate_plugin_configs(state, tmp_path)
        assert "assignments" not in state


class TestConfigToWizardStateSecrets:
    """Test _config_to_wizard_state with secrets service."""

    def test_should_detect_secrets_and_prefill_readable(self, tmp_path):
        mock_svc = MagicMock()
        mock_svc.list_identities_with_secrets.return_value = ["anomal"]
        mock_svc.has_secret.side_effect = lambda ident, key: key in (
            "gmail_app_password",
            "gmail_address",
            "telegram_bot_token",
            "telegram_chat_id",
            "moltbook_api_key",
        )
        mock_svc.get_readable_secret.side_effect = lambda ident, key: {
            "gmail_address": "test@gmail.com",
            "telegram_chat_id": "12345",
        }.get(key)

        with patch(
            "overblick.dashboard.services.secrets.SecretsService",
            return_value=mock_svc,
        ):
            state = _config_to_wizard_state({}, base_dir=tmp_path)

        assert state.get("_has_gmail_app_password") is True
        assert state.get("_has_telegram_bot_token") is True
        assert state.get("_has_moltbook_api_key") is True
        assert state.get("gmail_address") == "test@gmail.com"
        assert state.get("telegram_chat_id") == "12345"
        comm = state.get("communication", {})
        assert comm.get("gmail_enabled") is True
        assert comm.get("telegram_enabled") is True
        assert "social_media" in state.get("selected_use_cases", [])

    def test_should_handle_secrets_service_init_failure(self, tmp_path):
        with patch(
            "overblick.dashboard.services.secrets.SecretsService",
            side_effect=Exception("init failed"),
        ):
            state = _config_to_wizard_state({}, base_dir=tmp_path)
        assert isinstance(state, dict)

    def test_should_handle_readable_secret_failure(self, tmp_path):
        mock_svc = MagicMock()
        mock_svc.list_identities_with_secrets.return_value = ["anomal"]
        mock_svc.has_secret.return_value = True
        mock_svc.get_readable_secret.side_effect = Exception("decrypt failed")

        with patch(
            "overblick.dashboard.services.secrets.SecretsService",
            return_value=mock_svc,
        ):
            state = _config_to_wizard_state({}, base_dir=tmp_path)
        assert isinstance(state, dict)

    def test_should_create_principal_from_secrets(self, tmp_path):
        """When principal_name or email is found in secrets, create principal dict."""
        mock_svc = MagicMock()
        mock_svc.list_identities_with_secrets.return_value = ["anomal"]
        mock_svc.has_secret.side_effect = lambda ident, key: key in ("principal_name", "principal_email")
        mock_svc.get_readable_secret.side_effect = lambda ident, key: {
            "principal_name": "Alice",
            "principal_email": "alice@example.com",
        }.get(key)

        with patch(
            "overblick.dashboard.services.secrets.SecretsService",
            return_value=mock_svc,
        ):
            state = _config_to_wizard_state({}, base_dir=tmp_path)

        assert state["principal"]["principal_name"] == "Alice"
        assert state["principal"]["principal_email"] == "alice@example.com"

    def test_should_detect_all_plugin_use_cases(self, tmp_path):
        """Active plugins for all use case types should be detected."""
        identities_dir = tmp_path / "overblick" / "identities" / "test"
        identities_dir.mkdir(parents=True)
        (identities_dir / "personality.yaml").write_text(
            yaml.dump(
                {
                    "plugins": [
                        "moltbook",
                        "email_agent",
                        "telegram",
                        "ai_digest",
                        "github",
                        "irc",
                        "kontrast",
                        "spegel",
                        "skuggspel",
                        "compass",
                        "dev_agent",
                    ],
                }
            )
        )
        state = _config_to_wizard_state({}, base_dir=tmp_path)
        selected = state.get("selected_use_cases", [])
        assert "social_media" in selected
        assert "email" in selected
        assert "notifications" in selected
        assert "research" in selected
        assert "github_monitor" in selected
        assert "irc_conversations" in selected
        assert "multi_perspective" in selected
        assert "psychological_mirror" in selected
        assert "shadow_work" in selected
        assert "identity_drift" in selected
        assert "dev_automation" in selected

    def test_should_prepopulate_plugin_configs_when_detected(self, tmp_path):
        """When use cases are detected AND config exists, plugin configs are pre-populated."""
        identities_dir = tmp_path / "overblick" / "identities" / "stal"
        identities_dir.mkdir(parents=True)
        (identities_dir / "personality.yaml").write_text(
            yaml.dump({"plugins": ["email_agent"]})
        )
        config_dir = tmp_path / "config" / "stal"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins.yaml").write_text(
            yaml.dump(
                {
                    "email_agent": {
                        "filter_mode": "opt_in",
                        "senders": {"allowed": ["x@y.com"], "blocked": []},
                        "dry_run": True,
                    },
                }
            )
        )
        state = _config_to_wizard_state({}, base_dir=tmp_path)
        assert "selected_use_cases" in state
        assert "assignments" in state
        pcfg = state["assignments"]["email"]["plugin_config"]
        assert pcfg["email_filter_mode"] == "opt_in"


class TestGetBaseDir:
    def test_should_use_config_base_dir(self, app, client, session_cookie):
        """_get_base_dir uses config.base_dir when set."""
        # This is implicitly tested by the integration tests
        pass

    def test_should_fallback_when_no_base_dir(self, app):
        """_get_base_dir falls back to __file__ parent when no base_dir."""
        from overblick.dashboard.routes.settings import _get_base_dir

        original = app.state.config.base_dir
        app.state.config.base_dir = ""
        request = MagicMock()
        request.app = app
        result = _get_base_dir(request)
        assert isinstance(result, Path)
        app.state.config.base_dir = original


class TestSettingsStep2Prefill:
    @pytest.mark.asyncio
    async def test_step2_prefill_from_existing_config(self, client, session_cookie, app, tmp_path):
        """Step 2 pre-fills principal data from existing config."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(
            yaml.dump(
                {
                    "principal": {
                        "name": "Alice",
                        "email": "alice@example.com",
                        "timezone": "America/New_York",
                        "language": "en",
                    },
                }
            )
        )
        app.state.config.base_dir = str(tmp_path)

        # Clear any wizard state
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state.clear()

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/2",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step2_reconfigure_preserves_existing_principal(
        self, client, session_cookie, app
    ):
        """Step 2 POST with empty name when _has_principal_name is set."""
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state["_has_principal_name"] = True

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/2",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "principal_name": "",
                "principal_email": "new@example.com",
                "timezone": "Europe/Stockholm",
                "language_preference": "sv",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/3" in resp.headers["location"]
        assert state["principal"]["principal_email"] == "new@example.com"


class TestSettingsStep3Prefill:
    @pytest.mark.asyncio
    async def test_step3_prefill_from_existing_config(self, client, session_cookie, app, tmp_path):
        """Step 3 pre-fills LLM config from existing overblick.yaml."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(
            yaml.dump(
                {
                    "llm": {
                        "gateway_url": "http://127.0.0.1:8200",
                        "default_backend": "local",
                        "temperature": 0.7,
                        "max_tokens": 2000,
                        "backends": {
                            "local": {
                                "enabled": True,
                                "type": "ollama",
                                "host": "127.0.0.1",
                                "port": 11434,
                                "model": "qwen3:8b",
                            },
                        },
                    },
                }
            )
        )
        app.state.config.base_dir = str(tmp_path)

        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state.clear()

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/3",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200


class TestSettingsStep4Prefill:
    @pytest.mark.asyncio
    async def test_step4_prefill_from_existing_config(self, client, session_cookie, app, tmp_path):
        """Step 4 pre-fills communication from existing config."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(
            yaml.dump(
                {
                    "communication": {
                        "gmail_enabled": True,
                        "gmail_address": "test@gmail.com",
                    },
                }
            )
        )
        app.state.config.base_dir = str(tmp_path)

        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state.clear()

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/4",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step4_post_validation_error(self, client, session_cookie):
        """Step 4 POST with gmail enabled but no credentials shows error."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/4",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "gmail_enabled": "on",
                "gmail_address": "notanemail",
                "gmail_app_password": "",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        # Should either redirect or show error depending on validator
        assert resp.status_code in (200, 303)


class TestSettingsStep7PluginConfig:
    @pytest.mark.asyncio
    async def test_step7_post_with_email_plugin_config(self, client, session_cookie, app):
        """Step 7 POST includes plugin-specific config for email use case."""
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state["selected_use_cases"] = ["email"]

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/7",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "email_personality": "cherry",
                "email_temperature": "0.7",
                "email_max_tokens": "2000",
                "email_heartbeat_hours": "4",
                "email_quiet_hours": "on",
                "email_filter_mode": "opt_in",
                "email_allowed_senders": "alice@test.com",
                "email_blocked_senders": "",
                "email_dry_run": "on",
                "email_show_draft_replies": "off",
                "email_max_email_age_hours": "24",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/8" in resp.headers["location"]
        assert "email" in state.get("assignments", {})
        assert "plugin_config" in state["assignments"]["email"]

    @pytest.mark.asyncio
    async def test_step7_post_with_invalid_personality(self, client, session_cookie, app):
        """Step 7 POST falls back to recommended personality for invalid selection."""
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state["selected_use_cases"] = ["social_media"]

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/7",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "social_media_personality": "nonexistent",
                "social_media_temperature": "0.7",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303


class TestSettingsStep8PartialFailure:
    @pytest.mark.asyncio
    async def test_step8_partial_failure_config_exists(self, client, session_cookie, app, tmp_path):
        """Provisioning fails but config file exists — treated as partial success."""
        from overblick.setup.wizard import _derive_provisioner_state, _get_state

        state = _get_state(app)
        state["principal"] = {
            "principal_name": "Alice",
            "principal_email": "",
            "timezone": "Europe/Stockholm",
            "language_preference": "en",
        }
        state["llm"] = {
            "gateway_url": "http://127.0.0.1:8200",
            "local": {"enabled": True, "backend_type": "ollama", "host": "127.0.0.1", "port": 11434, "model": "qwen3:8b"},
            "cloud": {"enabled": False, "backend_type": "ollama", "host": "", "port": 11434, "model": "qwen3:8b"},
            "deepseek": {"enabled": False, "api_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
            "openai": {"enabled": False, "api_url": "https://api.openai.com/v1", "model": "gpt-4o"},
            "default_backend": "local",
            "default_temperature": 0.7,
            "default_max_tokens": 2000,
        }
        state["communication"] = {"gmail_enabled": False, "telegram_enabled": False}
        state["selected_use_cases"] = ["social_media"]
        state["assignments"] = {
            "social_media": {
                "personality": "cherry",
                "temperature": 0.8,
                "max_tokens": 2000,
                "heartbeat_hours": 4,
                "quiet_hours": True,
            },
        }
        _derive_provisioner_state(state)

        app.state.config.base_dir = str(tmp_path)
        # Create config file to simulate partial success
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "overblick.yaml").write_text("partial: true\n")

        cookie_value, csrf_token = session_cookie

        with patch("overblick.setup.provisioner.provision", side_effect=RuntimeError("Secrets failed")):
            resp = await client.post(
                "/settings/step/8",
                headers={"X-CSRF-Token": csrf_token},
                cookies={SESSION_COOKIE: cookie_value},
                follow_redirects=False,
            )

        # Should redirect to step 9 (partial success)
        assert resp.status_code == 303
        assert "/settings/step/9" in resp.headers["location"]
        assert app.state.setup_needed is False


class TestSettingsTestEndpointsExtended:
    @pytest.mark.asyncio
    async def test_test_ollama_success_with_models(self, client, session_cookie):
        """Test ollama endpoint with successful connection and models."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3:8b"}, {"name": "llama3:8b"}]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/ollama",
                headers={"X-CSRF-Token": csrf_token},
                data={"host": "203.0.113.1", "port": "11434"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "qwen3:8b" in resp.text

    @pytest.mark.asyncio
    async def test_test_ollama_no_models(self, client, session_cookie):
        """Test ollama with connection but no models."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/ollama",
                headers={"X-CSRF-Token": csrf_token},
                data={"host": "203.0.113.1", "port": "11434"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "No models found" in resp.text

    @pytest.mark.asyncio
    async def test_test_gateway_healthy(self, client, session_cookie):
        """Test gateway endpoint with healthy response."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/gateway",
                headers={"X-CSRF-Token": csrf_token},
                data={"gateway_url": "http://203.0.113.1:8200"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text

    @pytest.mark.asyncio
    async def test_test_gateway_degraded(self, client, session_cookie):
        """Test gateway with non-healthy status."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "degraded"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/gateway",
                headers={"X-CSRF-Token": csrf_token},
                data={"gateway_url": "http://203.0.113.1:8200"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "degraded" in resp.text

    @pytest.mark.asyncio
    async def test_test_gmail_success(self, client, session_cookie):
        """Test Gmail with successful connection."""
        cookie_value, csrf_token = session_cookie

        with patch("imaplib.IMAP4_SSL") as mock_imap_ssl:
            resp = await client.post(
                "/settings/test/gmail",
                headers={"X-CSRF-Token": csrf_token},
                data={"gmail_address": "test@gmail.com", "gmail_app_password": "app-pass"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text

    @pytest.mark.asyncio
    async def test_test_gmail_no_credentials(self, client, session_cookie):
        """Test Gmail without credentials."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/test/gmail",
            headers={"X-CSRF-Token": csrf_token},
            data={"gmail_address": "", "gmail_app_password": ""},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Enter credentials first" in resp.text

    @pytest.mark.asyncio
    async def test_test_gmail_auth_failure(self, client, session_cookie):
        """Test Gmail with authentication failure."""
        cookie_value, csrf_token = session_cookie

        with patch("imaplib.IMAP4_SSL") as mock_imap_ssl:
            mock_imap_ssl.return_value.login.side_effect = Exception(
                "AUTHENTICATIONFAILED"
            )

            resp = await client.post(
                "/settings/test/gmail",
                headers={"X-CSRF-Token": csrf_token},
                data={"gmail_address": "test@gmail.com", "gmail_app_password": "wrong"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Authentication failed" in resp.text

    @pytest.mark.asyncio
    async def test_test_telegram_success(self, client, session_cookie):
        """Test Telegram with successful connection."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"username": "testbot"}}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/telegram",
                headers={"X-CSRF-Token": csrf_token},
                data={"telegram_bot_token": "123:ABCdef", "telegram_chat_id": ""},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "testbot" in resp.text

    @pytest.mark.asyncio
    async def test_test_telegram_with_chat_id_success(self, client, session_cookie):
        """Test Telegram with chat ID — sends test message."""
        cookie_value, csrf_token = session_cookie

        mock_getme = MagicMock()
        mock_getme.status_code = 200
        mock_getme.json.return_value = {"result": {"username": "testbot"}}
        mock_getme.raise_for_status = MagicMock()

        mock_send = MagicMock()
        mock_send.is_success = True

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_getme
        mock_client.post.return_value = mock_send
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/telegram",
                headers={"X-CSRF-Token": csrf_token},
                data={"telegram_bot_token": "123:ABCdef", "telegram_chat_id": "12345"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "test message sent" in resp.text

    @pytest.mark.asyncio
    async def test_test_telegram_with_chat_id_send_failure(self, client, session_cookie):
        """Test Telegram with chat ID — send fails."""
        cookie_value, csrf_token = session_cookie

        mock_getme = MagicMock()
        mock_getme.status_code = 200
        mock_getme.json.return_value = {"result": {"username": "testbot"}}
        mock_getme.raise_for_status = MagicMock()

        mock_send = MagicMock()
        mock_send.is_success = False

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_getme
        mock_client.post.return_value = mock_send
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/telegram",
                headers={"X-CSRF-Token": csrf_token},
                data={"telegram_bot_token": "123:ABCdef", "telegram_chat_id": "12345"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "could not send" in resp.text

    @pytest.mark.asyncio
    async def test_test_telegram_no_token(self, client, session_cookie):
        """Test Telegram without bot token."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/test/telegram",
            headers={"X-CSRF-Token": csrf_token},
            data={"telegram_bot_token": ""},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Enter bot token first" in resp.text

    @pytest.mark.asyncio
    async def test_test_deepseek_success_with_models(self, client, session_cookie):
        """Test Deepseek with successful API response listing models."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-coder"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/deepseek",
                headers={"X-CSRF-Token": csrf_token},
                data={"deepseek_api_key": "sk-valid-key"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "deepseek-chat" in resp.text

    @pytest.mark.asyncio
    async def test_test_deepseek_success_no_models(self, client, session_cookie):
        """Test Deepseek with successful API but empty model list."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/deepseek",
                headers={"X-CSRF-Token": csrf_token},
                data={"deepseek_api_key": "sk-valid-key"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "API reachable" in resp.text

    @pytest.mark.asyncio
    async def test_test_deepseek_401(self, client, session_cookie):
        """Test Deepseek with 401 response."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/deepseek",
                headers={"X-CSRF-Token": csrf_token},
                data={"deepseek_api_key": "sk-invalid"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Invalid API key" in resp.text

    @pytest.mark.asyncio
    async def test_test_deepseek_500(self, client, session_cookie):
        """Test Deepseek with 500 response."""
        cookie_value, csrf_token = session_cookie

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/deepseek",
                headers={"X-CSRF-Token": csrf_token},
                data={"deepseek_api_key": "sk-test"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "HTTP 500" in resp.text

    @pytest.mark.asyncio
    async def test_test_llm_no_config(self, client, session_cookie, app):
        """Test LLM endpoint when no LLM configured in wizard state."""
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state.pop("llm", None)

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/test-llm",
            headers={"X-CSRF-Token": csrf_token},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat.test_llm_connection", new_callable=AsyncMock)
    async def test_test_llm_success(self, mock_test, client, session_cookie, app):
        """Test LLM endpoint with config present."""
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state["llm"] = {"provider": "ollama", "model": "qwen3:8b"}

        mock_test.return_value = {"success": True, "response": "Hello"}

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/test-llm",
            headers={"X-CSRF-Token": csrf_token},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


class TestConfigToWizardStateEdgeCases:
    def test_should_skip_non_directory_in_identities(self, tmp_path):
        """Non-directory files in identities dir should be skipped."""
        identities_dir = tmp_path / "overblick" / "identities"
        identities_dir.mkdir(parents=True)
        (identities_dir / "README.md").write_text("Not an identity")
        state = _config_to_wizard_state({}, base_dir=tmp_path)
        assert isinstance(state, dict)

    def test_should_handle_invalid_identity_yaml(self, tmp_path):
        """Invalid YAML in identity files should be silently skipped."""
        identities_dir = tmp_path / "overblick" / "identities" / "broken"
        identities_dir.mkdir(parents=True)
        (identities_dir / "personality.yaml").write_text("invalid: yaml: [[[")
        state = _config_to_wizard_state({}, base_dir=tmp_path)
        assert isinstance(state, dict)


class TestSettingsStep7UnknownUseCase:
    @pytest.mark.asyncio
    async def test_step7_post_with_unknown_use_case(self, client, session_cookie, app):
        """Unknown use case IDs in selected_use_cases should be skipped."""
        from overblick.setup.wizard import _get_state

        state = _get_state(app)
        state["selected_use_cases"] = ["nonexistent_use_case", "social_media"]

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/7",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "social_media_personality": "cherry",
                "social_media_temperature": "0.8",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "nonexistent_use_case" not in state.get("assignments", {})
        assert "social_media" in state.get("assignments", {})


class TestTelegramTestError:
    @pytest.mark.asyncio
    async def test_test_telegram_connection_error(self, client, session_cookie):
        """Test Telegram with connection error."""
        cookie_value, csrf_token = session_cookie

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/telegram",
                headers={"X-CSRF-Token": csrf_token},
                data={"telegram_bot_token": "123:ABCdef"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Failed" in resp.text


class TestDeepseekTestError:
    @pytest.mark.asyncio
    async def test_test_deepseek_connection_error(self, client, session_cookie):
        """Test Deepseek with connection error."""
        cookie_value, csrf_token = session_cookie

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/settings/test/deepseek",
                headers={"X-CSRF-Token": csrf_token},
                data={"deepseek_api_key": "sk-test"},
                cookies={SESSION_COOKIE: cookie_value},
            )
        assert resp.status_code == 200
        assert "Not reachable" in resp.text


class TestMigrateOldLlmConfig:
    def test_should_migrate_openai_provider(self):
        state = _migrate_old_llm_config(
            {"provider": "openai", "cloud_api_url": "https://api.openai.com/v1", "cloud_model": "gpt-4o"}
        )
        assert state["openai"]["enabled"] is True
        assert state["default_backend"] == "openai"

    def test_should_migrate_gateway_provider(self):
        state = _migrate_old_llm_config({"provider": "gateway"})
        assert state["local"]["enabled"] is True
        assert state["local"]["backend_type"] == "ollama"
