"""
Tests for settings wizard pre-population from existing config/overblick.yaml.

Verifies that when an existing config exists, the wizard state is pre-populated
with the current values, and the welcome page shows the "Reconfiguring" badge.
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.routes.settings import _config_to_wizard_state, _load_existing_config


class TestConfigToWizardState:
    """Unit tests for the config→wizard state mapper."""

    def test_maps_new_format_backends(self):
        """New backends-format config maps to wizard state correctly."""
        cfg = {
            "principal": {"timezone": "Europe/London", "language": "en"},
            "llm": {
                "gateway_url": "http://127.0.0.1:8200",
                "default_backend": "local",
                "temperature": 0.5,
                "max_tokens": 1500,
                "backends": {
                    "local": {
                        "enabled": True,
                        "type": "ollama",
                        "host": "192.168.1.100",
                        "port": 11434,
                        "model": "llama3:8b",
                    },
                    "cloud": {"enabled": False},
                    "openai": {"enabled": False},
                },
            },
        }
        state = _config_to_wizard_state(cfg)
        assert state["llm"]["local"]["host"] == "192.168.1.100"
        assert state["llm"]["local"]["model"] == "llama3:8b"
        assert state["llm"]["default_temperature"] == 0.5
        assert state["llm"]["default_backend"] == "local"
        assert state["principal"]["timezone"] == "Europe/London"

    def test_maps_old_ollama_provider(self):
        """Legacy ollama provider migrates to backends format."""
        cfg = {
            "llm": {"provider": "ollama", "host": "127.0.0.1", "port": 11434, "model": "qwen3:8b"},
        }
        state = _config_to_wizard_state(cfg)
        assert state["llm"]["local"]["enabled"] is True
        assert state["llm"]["local"]["backend_type"] == "ollama"
        assert state["llm"]["default_backend"] == "local"

    def test_maps_old_lmstudio_provider(self):
        """Legacy lmstudio provider migrates correctly."""
        cfg = {
            "llm": {"provider": "lmstudio", "host": "127.0.0.1", "port": 1234, "model": "phi3"},
        }
        state = _config_to_wizard_state(cfg)
        assert state["llm"]["local"]["backend_type"] == "lmstudio"
        assert state["llm"]["local"]["port"] == 1234

    def test_maps_old_cloud_provider(self):
        """Legacy cloud provider migrates to openai backend."""
        cfg = {
            "llm": {
                "provider": "cloud",
                "cloud_api_url": "https://api.openai.com/v1",
                "cloud_model": "gpt-4o",
            },
        }
        state = _config_to_wizard_state(cfg)
        assert state["llm"]["openai"]["enabled"] is True
        assert state["llm"]["default_backend"] == "openai"

    def test_detects_new_plugin_use_cases(self, tmp_path):
        """Active plugins for new use cases map to correct use_case IDs."""
        identities_dir = tmp_path / "overblick" / "identities" / "anomal"
        identities_dir.mkdir(parents=True)
        (identities_dir / "personality.yaml").write_text(yaml.dump({
            "plugins": ["irc", "kontrast", "spegel", "skuggspel", "compass", "dev_agent"],
        }))
        state = _config_to_wizard_state({}, base_dir=tmp_path)
        selected = state.get("selected_use_cases", [])
        assert "irc_conversations" in selected
        assert "multi_perspective" in selected
        assert "psychological_mirror" in selected
        assert "shadow_work" in selected
        assert "identity_drift" in selected
        assert "dev_automation" in selected

    def test_empty_config_returns_empty(self):
        state = _config_to_wizard_state({})
        assert state == {}

    def test_principal_prefilled_from_global_config(self):
        """Principal name and email are global config — should be pre-filled."""
        cfg = {"principal": {"name": "Test User", "email": "test@example.com", "timezone": "UTC", "language": "sv"}}
        state = _config_to_wizard_state(cfg)
        assert state["principal"]["principal_name"] == "Test User"
        assert state["principal"]["principal_email"] == "test@example.com"
        assert state["principal"]["timezone"] == "UTC"
        assert state["principal"]["language_preference"] == "sv"


class TestLoadExistingConfig:
    """Tests for _load_existing_config."""

    def test_returns_empty_when_no_file(self, tmp_path):
        cfg = _load_existing_config(tmp_path)
        assert cfg == {}

    def test_loads_valid_yaml(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "overblick.yaml"
        config_file.write_text(yaml.dump({
            "framework": {"name": "Överblick"},
            "llm": {"provider": "ollama", "model": "qwen3:8b"},
        }))
        cfg = _load_existing_config(tmp_path)
        assert cfg["llm"]["provider"] == "ollama"

    def test_returns_empty_on_invalid_yaml(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "overblick.yaml"
        config_file.write_text("not: valid: yaml: [[[")
        cfg = _load_existing_config(tmp_path)
        assert cfg == {}


class TestPrePopulationInWizard:
    """Integration tests: wizard routes show correct pre-populated vs. fresh state."""

    @pytest.mark.asyncio
    async def test_step1_shows_first_time_badge_when_no_config(self, client, session_cookie, app, tmp_path):
        # Ensure no config file exists
        app.state.config.base_dir = str(tmp_path)
        app.state.setup_needed = True

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/1",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "First-time setup" in resp.text or "Set Up" in resp.text

    @pytest.mark.asyncio
    async def test_step1_shows_reconfigure_badge_when_config_exists(
        self, client, session_cookie, app, tmp_path
    ):
        # Create a fake config file
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(yaml.dump({
            "llm": {"provider": "ollama", "model": "qwen3:8b"},
            "principal": {"timezone": "Europe/Stockholm"},
        }))
        app.state.config.base_dir = str(tmp_path)
        app.state.setup_needed = False

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/1",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Reconfiguring" in resp.text or "existing" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_wizard_state_pre_populated_from_config(self, client, session_cookie, app, tmp_path):
        """After visiting step 1, wizard state should be populated with existing config values."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(yaml.dump({
            "llm": {
                "gateway_url": "http://127.0.0.1:8200",
                "default_backend": "local",
                "temperature": 0.7,
                "max_tokens": 2000,
                "backends": {
                    "local": {"enabled": True, "type": "lmstudio", "host": "192.168.1.5", "port": 1234, "model": "phi3"},
                    "cloud": {"enabled": False},
                    "openai": {"enabled": False},
                },
            },
            "principal": {"name": "Test User", "email": "test@test.com", "timezone": "America/New_York", "language": "en"},
        }))
        app.state.config.base_dir = str(tmp_path)

        # Clear any previous wizard state
        if hasattr(app.state, "wizard_state"):
            del app.state.wizard_state

        cookie_value, _ = session_cookie
        await client.get(
            "/settings/step/1",
            cookies={SESSION_COOKIE: cookie_value},
        )

        # Check the wizard state was pre-populated
        from overblick.setup.wizard import _get_state
        state = _get_state(app)
        llm = state.get("llm", {})
        assert llm.get("local", {}).get("backend_type") == "lmstudio"
        principal = state.get("principal", {})
        assert principal.get("timezone") == "America/New_York"
        assert principal.get("principal_name") == "Test User"
        assert principal.get("principal_email") == "test@test.com"

    @pytest.mark.asyncio
    async def test_step1_always_refreshes_from_disk(self, client, session_cookie, app, tmp_path):
        """Step 1 always reloads from disk config to ensure fresh values."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(yaml.dump({
            "llm": {"provider": "ollama", "model": "qwen3:8b"},
            "principal": {"name": "Original", "timezone": "Europe/Stockholm"},
        }))
        app.state.config.base_dir = str(tmp_path)

        if hasattr(app.state, "wizard_state"):
            del app.state.wizard_state

        cookie_value, csrf_token = session_cookie

        # Visit step 1 — triggers pre-population
        await client.get("/settings/step/1", cookies={SESSION_COOKIE: cookie_value})

        from overblick.setup.wizard import _get_state
        state = _get_state(app)
        assert state["principal"]["principal_name"] == "Original"

        # Update config on disk
        (config_dir / "overblick.yaml").write_text(yaml.dump({
            "llm": {"provider": "ollama", "model": "qwen3:8b"},
            "principal": {"name": "Updated", "timezone": "Europe/Stockholm"},
        }))

        # Visit step 1 again — should pick up the updated config
        await client.get("/settings/step/1", cookies={SESSION_COOKIE: cookie_value})

        state_after = _get_state(app)
        assert state_after["principal"]["principal_name"] == "Updated"
