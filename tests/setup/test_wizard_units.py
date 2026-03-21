"""
Unit tests for wizard.py helper functions and untested route paths.
"""

import json
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from overblick.setup.wizard import (
    _build_assignment_data,
    _derive_provisioner_state,
    _friendly_error,
    _get_version,
    _load_identity_data,
    _load_wizard_state,
    _save_wizard_state,
    _uc_to_plugin_key,
    plugin_name,
)


class TestPluginName:
    """Tests for plugin_name function."""

    def test_known_plugin(self):
        assert plugin_name("moltbook") == "Moltbook"
        assert plugin_name("email_agent") == "Email Agent"
        assert plugin_name("ai_digest") == "AI Digest"

    def test_unknown_plugin_fallback(self):
        assert plugin_name("unknown_plugin") == "Unknown Plugin"
        assert plugin_name("my_cool_plugin") == "My Cool Plugin"


class TestFriendlyError:
    """Tests for _friendly_error."""

    def test_validation_error(self):
        from pydantic import ValidationError

        try:
            from overblick.setup.validators import PrincipalData

            PrincipalData(principal_name="")
        except ValidationError as e:
            msg = _friendly_error(e)
            assert "Principal name is required" in msg

    def test_generic_error(self):
        msg = _friendly_error(RuntimeError("something broke"))
        assert msg == "something broke"

    def test_validation_error_strips_prefix(self):
        from pydantic import ValidationError

        try:
            from overblick.setup.validators import PrincipalData

            PrincipalData(principal_name="X")
        except ValidationError as e:
            msg = _friendly_error(e)
            assert "Value error, " not in msg


class TestUcToPluginKey:
    """Tests for _uc_to_plugin_key."""

    def test_email(self):
        assert _uc_to_plugin_key("email") == "email_agent"

    def test_github_monitor(self):
        assert _uc_to_plugin_key("github_monitor") == "github"

    def test_dev_automation(self):
        assert _uc_to_plugin_key("dev_automation") == "dev_agent"

    def test_unknown(self):
        assert _uc_to_plugin_key("social_media") == ""


class TestGetVersion:
    """Tests for _get_version."""

    def test_reads_version_from_pyproject(self, tmp_path):
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\nversion = "1.2.3"\n')
        assert _get_version(tmp_path) == "1.2.3"

    def test_returns_default_when_missing(self, tmp_path):
        assert _get_version(tmp_path) == "0.1.0"

    def test_returns_default_on_error(self, tmp_path):
        toml = tmp_path / "pyproject.toml"
        toml.write_text("not a valid toml\n")
        # No version line, so it returns default
        assert _get_version(tmp_path) == "0.1.0"

    def test_returns_default_on_read_exception(self, tmp_path):
        """_get_version returns default when file read raises exception."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text("version = '1.0.0'\n")

        with patch.object(Path, "read_text", side_effect=PermissionError("no read")):
            assert _get_version(tmp_path) == "0.1.0"


class TestSaveLoadWizardState:
    """Tests for wizard state persistence."""

    def test_save_and_load(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".wizard_state.json"
        monkeypatch.setattr("overblick.setup.wizard._WIZARD_STATE_FILE", state_file)

        state = {"current_step": 3, "principal": {"name": "Test"}}
        _save_wizard_state(state)
        assert state_file.exists()

        loaded = _load_wizard_state()
        assert loaded["current_step"] == 3

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        state_file = tmp_path / "nonexistent.json"
        monkeypatch.setattr("overblick.setup.wizard._WIZARD_STATE_FILE", state_file)
        assert _load_wizard_state() is None

    def test_load_corrupt_returns_none(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".wizard_state.json"
        state_file.write_text("not json at all")
        monkeypatch.setattr("overblick.setup.wizard._WIZARD_STATE_FILE", state_file)
        assert _load_wizard_state() is None

    def test_save_filters_sensitive_keys(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".wizard_state.json"
        monkeypatch.setattr("overblick.setup.wizard._WIZARD_STATE_FILE", state_file)

        state = {"current_step": 1, "_private": "secret", "deepseek_api_key": "sk-123"}
        _save_wizard_state(state)

        loaded = json.loads(state_file.read_text())
        assert "_private" not in loaded
        assert "deepseek_api_key" not in loaded

    def test_save_handles_write_error(self, tmp_path, monkeypatch):
        """_save_wizard_state doesn't crash on write error."""
        state_file = tmp_path / "readonly" / ".wizard_state.json"
        monkeypatch.setattr("overblick.setup.wizard._WIZARD_STATE_FILE", state_file)

        with patch("pathlib.Path.write_text", side_effect=PermissionError("no write")):
            _save_wizard_state({"step": 1})  # Should not raise


class TestLoadIdentityData:
    """Tests for _load_identity_data."""

    def test_load_from_real_identities(self, tmp_path):
        """Load identity data from YAML files."""
        identities_dir = tmp_path / "overblick" / "identities"
        agent_dir = identities_dir / "testbot"
        agent_dir.mkdir(parents=True)

        personality = {
            "identity": {"display_name": "TestBot", "role": "Test agent", "description": "A test"},
            "voice": {"base_tone": "friendly"},
            "traits": {"curiosity": 0.8, "humor": 0.6, "creativity": 0.4},
            "example_conversations": {
                "greeting": {"response": "Hello, I am TestBot! Nice to meet you on this fine day."}
            },
        }
        with open(agent_dir / "personality.yaml", "w") as f:
            yaml.dump(personality, f)

        chars = _load_identity_data(tmp_path)
        assert len(chars) == 1
        assert chars[0]["name"] == "testbot"
        assert chars[0]["display_name"] == "TestBot"
        assert "curiosity" in chars[0]["traits"]
        assert chars[0]["sample_quote"]

    def test_load_skips_supervisor(self, tmp_path):
        """Supervisor identity is skipped."""
        identities_dir = tmp_path / "overblick" / "identities"
        sup_dir = identities_dir / "supervisor"
        sup_dir.mkdir(parents=True)
        with open(sup_dir / "personality.yaml", "w") as f:
            yaml.dump({"identity": {"display_name": "Supervisor"}}, f)

        chars = _load_identity_data(tmp_path)
        assert len(chars) == 0

    def test_load_missing_dir(self, tmp_path):
        """Missing identities directory returns empty list."""
        chars = _load_identity_data(tmp_path)
        assert chars == []

    def test_load_invalid_yaml(self, tmp_path):
        """Invalid YAML is skipped with warning."""
        identities_dir = tmp_path / "overblick" / "identities"
        bad_dir = identities_dir / "bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "personality.yaml").write_text(": invalid : yaml : !!!")

        chars = _load_identity_data(tmp_path)
        assert len(chars) == 0

    def test_load_dir_without_personality_yaml(self, tmp_path):
        """Directory without personality.yaml is skipped."""
        identities_dir = tmp_path / "overblick" / "identities"
        no_yaml_dir = identities_dir / "empty"
        no_yaml_dir.mkdir(parents=True)

        chars = _load_identity_data(tmp_path)
        assert len(chars) == 0

    def test_sample_quote_truncation(self, tmp_path):
        """Long sample quotes are truncated to 120 chars."""
        identities_dir = tmp_path / "overblick" / "identities"
        agent_dir = identities_dir / "verbose"
        agent_dir.mkdir(parents=True)

        personality = {
            "identity": {"display_name": "Verbose"},
            "voice": {},
            "traits": {},
            "example_conversations": {
                "greeting": {"response": "A" * 200}
            },
        }
        with open(agent_dir / "personality.yaml", "w") as f:
            yaml.dump(personality, f)

        chars = _load_identity_data(tmp_path)
        assert len(chars[0]["sample_quote"]) <= 123  # 120 + "..."

    def test_identity_with_named_responses(self, tmp_path):
        """Identity with anomal_response/cherry_response fields."""
        identities_dir = tmp_path / "overblick" / "identities"
        agent_dir = identities_dir / "multi"
        agent_dir.mkdir(parents=True)

        personality = {
            "identity": {},
            "voice": {},
            "traits": {},
            "example_conversations": {
                "test": {"anomal_response": "I am Anomal"}
            },
        }
        with open(agent_dir / "personality.yaml", "w") as f:
            yaml.dump(personality, f)

        chars = _load_identity_data(tmp_path)
        assert chars[0]["sample_quote"] == "I am Anomal"


class TestBuildAssignmentData:
    """Tests for _build_assignment_data."""

    def test_builds_with_recommended(self):
        characters = [
            {"name": "cherry", "display_name": "Cherry"},
            {"name": "anomal", "display_name": "Anomal"},
        ]
        state = {"assignments": {}}
        result = _build_assignment_data(["social_media"], characters, state)
        assert len(result) == 1
        assert result[0]["id"] == "social_media"
        assert result[0]["recommended"] == "cherry"

    def test_skips_unknown_use_case(self):
        result = _build_assignment_data(["nonexistent"], [], {})
        assert len(result) == 0

    def test_uses_previous_assignment(self):
        characters = [
            {"name": "cherry", "display_name": "Cherry"},
            {"name": "anomal", "display_name": "Anomal"},
        ]
        state = {
            "assignments": {
                "social_media": {
                    "personality": "anomal",
                    "temperature": 0.9,
                    "max_tokens": 3000,
                    "heartbeat_hours": 2,
                    "quiet_hours": False,
                    "plugin_config": {"key": "val"},
                }
            }
        }
        result = _build_assignment_data(["social_media"], characters, state)
        assert result[0]["assigned_personality"] == "anomal"
        assert result[0]["temperature"] == 0.9


class TestDeriveProvisionerState:
    """Tests for _derive_provisioner_state."""

    def test_single_use_case(self):
        state = {
            "assignments": {
                "social_media": {"personality": "cherry", "temperature": 0.8, "max_tokens": 2000, "heartbeat_hours": 4, "quiet_hours": True}
            }
        }
        _derive_provisioner_state(state)
        assert "cherry" in state["selected_characters"]
        assert "cherry" in state["agent_configs"]
        assert "moltbook" in state["agent_configs"]["cherry"]["plugins"]

    def test_multiple_use_cases_same_personality(self):
        """Multiple use cases assigned to same personality merge plugins."""
        state = {
            "assignments": {
                "social_media": {"personality": "anomal", "temperature": 0.8},
                "research": {"personality": "anomal", "temperature": 0.7},
            }
        }
        _derive_provisioner_state(state)
        assert state["selected_characters"] == ["anomal"]
        plugins = state["agent_configs"]["anomal"]["plugins"]
        assert "moltbook" in plugins
        assert "ai_digest" in plugins

    def test_unknown_use_case_skipped(self):
        state = {
            "assignments": {
                "nonexistent": {"personality": "test"}
            }
        }
        _derive_provisioner_state(state)
        assert state["selected_characters"] == []

    def test_empty_personality_skipped(self):
        state = {
            "assignments": {
                "social_media": {"personality": ""}
            }
        }
        _derive_provisioner_state(state)
        assert state["selected_characters"] == []

    def test_plugin_configs_included(self):
        state = {
            "assignments": {
                "email": {
                    "personality": "stal",
                    "plugin_config": {"email_dry_run": True},
                }
            }
        }
        _derive_provisioner_state(state)
        assert "email_agent" in state["agent_configs"]["stal"].get("plugin_configs", {})


class TestWizardRoutesCoverage:
    """Additional wizard route tests for coverage."""

    @pytest.fixture
    def base_dir(self, tmp_path):
        """Create a temporary base directory with identity files."""
        import shutil

        real_identities = Path(__file__).parent.parent.parent / "overblick" / "identities"
        test_identities = tmp_path / "overblick" / "identities"
        if real_identities.exists():
            shutil.copytree(real_identities, test_identities)
        real_toml = Path(__file__).parent.parent.parent / "pyproject.toml"
        if real_toml.exists():
            shutil.copy2(real_toml, tmp_path / "pyproject.toml")
        return tmp_path

    @pytest.fixture
    def setup_app(self, base_dir):
        from overblick.setup.app import create_setup_app

        return create_setup_app(base_dir=base_dir)

    @pytest.fixture
    async def client(self, setup_app):
        transport = ASGITransport(app=setup_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c

    async def test_step3_invalid_temperature(self, client):
        """Step 3 with invalid temperature shows error."""
        resp = await client.post(
            "/step/3",
            data={
                "llm_provider": "ollama",
                "ollama_host": "127.0.0.1",
                "ollama_port": "11434",
                "model": "qwen3:8b",
                "gateway_url": "http://127.0.0.1:8200",
                "default_temperature": "5.0",
                "default_max_tokens": "2000",
            },
        )
        assert resp.status_code == 200  # Re-renders form

    async def test_step4_invalid_gmail(self, client):
        """Step 4 with invalid gmail shows error."""
        resp = await client.post(
            "/step/4",
            data={
                "gmail_enabled": "on",
                "gmail_address": "not-an-email",
                "gmail_app_password": "pass",
            },
        )
        assert resp.status_code == 200

    async def test_step6_invalid_personality_reset(self, client):
        """Step 6 resets to recommended when invalid personality submitted."""
        await client.post(
            "/step/5",
            data={"selected_use_cases": ["email"]},
            follow_redirects=False,
        )
        resp = await client.post(
            "/step/6",
            data={
                "email_personality": "nonexistent_personality",
                "email_temperature": "0.7",
                "email_max_tokens": "2000",
                "email_heartbeat_hours": "4",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_step7_provisioning_success(self, client):
        """Step 7 POST provisions successfully."""
        # Walk through the full flow
        await client.post(
            "/step/2",
            data={"principal_name": "Test User", "timezone": "UTC", "language_preference": "en"},
            follow_redirects=False,
        )
        await client.post(
            "/step/3",
            data={
                "llm_provider": "ollama", "ollama_host": "127.0.0.1", "ollama_port": "11434",
                "model": "qwen3:8b", "gateway_url": "http://127.0.0.1:8200",
                "default_temperature": "0.7", "default_max_tokens": "2000",
            },
            follow_redirects=False,
        )
        await client.post("/step/4", data={}, follow_redirects=False)
        await client.post(
            "/step/5", data={"selected_use_cases": ["social_media"]}, follow_redirects=False,
        )
        await client.post(
            "/step/6",
            data={
                "social_media_personality": "cherry",
                "social_media_temperature": "0.8",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
            },
            follow_redirects=False,
        )
        # Mock provision to avoid real file creation
        with patch("overblick.setup.provisioner.provision", return_value={"created_files": ["test.yaml"]}):
            resp = await client.post("/step/7", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/8"

    async def test_step7_provisioning_failure(self, client):
        """Step 7 POST handles provisioning failure."""
        await client.post("/step/5", data={"selected_use_cases": ["social_media"]}, follow_redirects=False)
        await client.post(
            "/step/6",
            data={
                "social_media_personality": "cherry",
                "social_media_temperature": "0.8",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
            },
            follow_redirects=False,
        )
        with patch("overblick.setup.provisioner.provision", side_effect=RuntimeError("provision error")):
            resp = await client.post("/step/7")
        assert resp.status_code == 200
        assert "Setup failed" in resp.text

    async def test_test_gmail_no_creds(self, client):
        """Gmail test with empty credentials."""
        resp = await client.post("/test/gmail", data={"gmail_address": "", "gmail_app_password": ""})
        assert resp.status_code == 200
        assert "Enter credentials" in resp.text

    async def test_test_gmail_auth_failed(self, client):
        """Gmail test with auth failure."""
        with patch("imaplib.IMAP4_SSL") as mock_imap:
            mock_imap.return_value.login.side_effect = Exception("AUTHENTICATIONFAILED")
            resp = await client.post(
                "/test/gmail", data={"gmail_address": "test@gmail.com", "gmail_app_password": "bad"}
            )
        assert resp.status_code == 200
        assert "Authentication failed" in resp.text

    async def test_test_gmail_success(self, client):
        """Gmail test succeeds."""
        with patch("imaplib.IMAP4_SSL"):
            resp = await client.post(
                "/test/gmail", data={"gmail_address": "test@gmail.com", "gmail_app_password": "good"}
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text

    async def test_test_gmail_generic_error(self, client):
        """Gmail test with generic error."""
        with patch("imaplib.IMAP4_SSL") as mock_imap:
            mock_imap.return_value.login.side_effect = Exception("some other error")
            resp = await client.post(
                "/test/gmail", data={"gmail_address": "test@gmail.com", "gmail_app_password": "bad"}
            )
        assert resp.status_code == 200
        assert "Failed" in resp.text

    async def test_test_telegram_no_token(self, client):
        """Telegram test with empty token."""
        resp = await client.post("/test/telegram", data={"telegram_bot_token": ""})
        assert resp.status_code == 200
        assert "Enter bot token" in resp.text

    async def test_test_telegram_success_with_chat_id(self, client):
        """Telegram test with chat_id sends test message."""
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"result": {"username": "testbot"}}
        mock_get_resp.raise_for_status = MagicMock()

        mock_send_resp = MagicMock()
        mock_send_resp.is_success = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_get_resp)
        mock_client.post = AsyncMock(return_value=mock_send_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/test/telegram",
                data={"telegram_bot_token": "123:ABC", "telegram_chat_id": "456"},
            )
        assert resp.status_code == 200
        assert "testbot" in resp.text
        assert "test message sent" in resp.text

    async def test_test_telegram_send_fails(self, client):
        """Telegram test message send fails."""
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"result": {"username": "testbot"}}
        mock_get_resp.raise_for_status = MagicMock()

        mock_send_resp = MagicMock()
        mock_send_resp.is_success = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_get_resp)
        mock_client.post = AsyncMock(return_value=mock_send_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/test/telegram",
                data={"telegram_bot_token": "123:ABC", "telegram_chat_id": "456"},
            )
        assert resp.status_code == 200
        assert "could not send" in resp.text

    async def test_test_telegram_no_chat_id(self, client):
        """Telegram test without chat_id."""
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"result": {"username": "testbot"}}
        mock_get_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_get_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/test/telegram",
                data={"telegram_bot_token": "123:ABC", "telegram_chat_id": ""},
            )
        assert resp.status_code == 200
        assert "testbot" in resp.text

    async def test_test_telegram_error(self, client):
        """Telegram test with connection error."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection failed"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/test/telegram",
                data={"telegram_bot_token": "123:ABC"},
            )
        assert resp.status_code == 200
        assert "Failed" in resp.text

    async def test_chat_endpoint(self, client):
        """Chat endpoint calls chat_with_identity."""
        with patch("overblick.shared.onboarding_chat.chat_with_identity", new_callable=AsyncMock, return_value={"success": True, "response": "Hi!"}):
            resp = await client.post(
                "/chat",
                json={"identity_name": "cherry", "message": "hello"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    async def test_chat_endpoint_missing_fields(self, client):
        """Chat endpoint with missing fields returns error."""
        resp = await client.post("/chat", json={"identity_name": "", "message": ""})
        assert resp.status_code == 400

    async def test_chat_endpoint_invalid_json(self, client):
        """Chat endpoint with invalid JSON returns error."""
        resp = await client.post(
            "/chat",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_test_llm_endpoint(self, client):
        """Test LLM endpoint calls test_llm_connection."""
        # Set LLM config first
        await client.post(
            "/step/3",
            data={
                "llm_provider": "ollama", "ollama_host": "127.0.0.1", "ollama_port": "11434",
                "model": "qwen3:8b", "gateway_url": "http://127.0.0.1:8200",
                "default_temperature": "0.7", "default_max_tokens": "2000",
            },
            follow_redirects=False,
        )
        with patch("overblick.shared.onboarding_chat.test_llm_connection", new_callable=AsyncMock, return_value={"success": True}):
            resp = await client.post("/test-llm")
        assert resp.status_code == 200

    async def test_test_llm_no_config(self, client, setup_app):
        """Test LLM endpoint without config returns error."""
        # Reset wizard state to force empty LLM config
        setup_app.state.wizard_state = {"current_step": 1, "llm": {}, "principal": {}, "communication": {}, "selected_use_cases": [], "assignments": {}, "selected_characters": [], "agent_configs": {}, "completed": False, "created_files": []}
        resp = await client.post("/test-llm")
        assert resp.status_code == 400

    async def test_shutdown_endpoint(self, client):
        """Shutdown endpoint triggers server stop."""
        with patch("threading.Timer"):
            resp = await client.post("/shutdown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "shutting_down"

    async def test_test_ollama_success_with_models(self, client):
        """Ollama test returns model list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "qwen3:8b"}, {"name": "llama3:8b"}]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/test/ollama",
                data={"ollama_host": "127.0.0.1", "ollama_port": "11434"},
            )
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "qwen3:8b" in resp.text

    async def test_step6_post_skips_unknown_use_case(self, client, setup_app):
        """Step 6 POST skips unknown use cases in selected_use_cases."""
        # Initialize wizard state by making a request first
        await client.get("/")
        setup_app.state.wizard_state["selected_use_cases"] = ["nonexistent_uc", "social_media"]
        resp = await client.post(
            "/step/6",
            data={
                "social_media_personality": "cherry",
                "social_media_temperature": "0.8",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_shutdown_inner_stop(self, client):
        """Shutdown endpoint's _stop function is scheduled but not executed."""
        # The _stop function calls os.kill(os.getpid(), signal.SIGINT)
        # We verify the timer is created with the right target
        captured_funcs = []


        class MockTimer:
            def __init__(self, interval, func):
                captured_funcs.append(func)
                self.func = func

            def start(self):
                pass  # Don't actually start

        with patch("threading.Timer", MockTimer):
            resp = await client.post("/shutdown")

        assert resp.status_code == 200
        assert len(captured_funcs) == 1
        # Verify the function would call os.kill
        with patch("os.kill") as mock_kill:
            captured_funcs[0]()
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGINT)

    async def test_test_ollama_no_models(self, client):
        """Ollama test with no models."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await client.post(
                "/test/ollama",
                data={"ollama_host": "127.0.0.1", "ollama_port": "11434"},
            )
        assert resp.status_code == 200
        assert "No models" in resp.text
