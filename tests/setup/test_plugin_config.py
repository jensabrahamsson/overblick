"""
Tests for the wizard plugin configuration feature.

Covers:
- _deep_merge helper in identities/__init__
- _parse_plugin_config / _parse_textarea_lines in settings routes
- _build_plugin_configs in provisioner
- plugins.yaml provisioning and identity loading with deep merge
- _uc_to_plugin_key mapping
- _derive_provisioner_state with plugin_configs
"""

from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

from overblick.identities import _build_identity, _deep_merge, _load_yaml
from overblick.setup.provisioner import _build_plugin_configs, provision
from overblick.setup.wizard import _USE_CASE_MAP, _derive_provisioner_state, _uc_to_plugin_key

# ---------------------------------------------------------------------------
# _deep_merge tests
# ---------------------------------------------------------------------------


class TestDeepMerge:
    """Tests for _deep_merge helper."""

    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_deeply_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"d": 99}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 1, "d": 99}}}

    def test_list_replaced_not_merged(self):
        base = {"tags": ["a", "b"]}
        override = {"tags": ["c"]}
        result = _deep_merge(base, override)
        assert result == {"tags": ["c"]}

    def test_scalar_replaces_dict(self):
        base = {"a": {"nested": 1}}
        override = {"a": "flat"}
        result = _deep_merge(base, override)
        assert result == {"a": "flat"}

    def test_dict_replaces_scalar(self):
        base = {"a": "flat"}
        override = {"a": {"nested": 1}}
        result = _deep_merge(base, override)
        assert result == {"a": {"nested": 1}}

    def test_no_mutation_of_base(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"x": 1}}

    def test_empty_override(self):
        base = {"a": 1}
        result = _deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# _uc_to_plugin_key tests
# ---------------------------------------------------------------------------


class TestUcToPluginKey:
    """Tests for use-case to plugin key mapping."""

    def test_email(self):
        assert _uc_to_plugin_key("email") == "email_agent"

    def test_github(self):
        assert _uc_to_plugin_key("github_monitor") == "github"

    def test_dev_automation(self):
        assert _uc_to_plugin_key("dev_automation") == "dev_agent"

    def test_unknown_returns_empty(self):
        assert _uc_to_plugin_key("social_media") == ""
        assert _uc_to_plugin_key("nonexistent") == ""


# ---------------------------------------------------------------------------
# _build_plugin_configs tests
# ---------------------------------------------------------------------------


class TestBuildPluginConfigs:
    """Tests for the flat-to-nested plugin config transformer."""

    def test_email_agent_basic(self):
        wizard_cfgs = {
            "email_agent": {
                "email_filter_mode": "opt_in",
                "email_allowed_senders": "a@x.com\nb@x.com",
                "email_blocked_senders": "",
                "email_dry_run": True,
                "email_show_draft_replies": False,
                "email_max_email_age_hours": 48,
            },
        }
        result = _build_plugin_configs(wizard_cfgs)
        assert "email_agent" in result
        cfg = result["email_agent"]
        assert cfg["filter_mode"] == "opt_in"
        assert cfg["senders"]["allowed"] == ["a@x.com", "b@x.com"]
        assert "blocked" not in cfg["senders"]
        assert cfg["dry_run"] is True

    def test_email_agent_opt_out(self):
        wizard_cfgs = {
            "email_agent": {
                "email_filter_mode": "opt_out",
                "email_allowed_senders": "",
                "email_blocked_senders": "spam@x.com",
                "email_dry_run": False,
                "email_show_draft_replies": True,
                "email_max_email_age_hours": 24,
            },
        }
        result = _build_plugin_configs(wizard_cfgs)
        cfg = result["email_agent"]
        assert cfg["filter_mode"] == "opt_out"
        assert cfg["senders"]["blocked"] == ["spam@x.com"]
        assert cfg["dry_run"] is False
        assert cfg["show_draft_replies"] is True
        assert cfg["max_email_age_hours"] == 24

    def test_github_basic(self):
        wizard_cfgs = {
            "github": {
                "github_repos": "org/repo1\norg/repo2",
                "github_dry_run": True,
                "github_bot_username": "my-bot",
                "github_tick_interval_minutes": 15,
                "github_auto_merge_patch": True,
                "github_auto_merge_minor": False,
                "github_auto_merge_major": False,
            },
        }
        result = _build_plugin_configs(wizard_cfgs)
        cfg = result["github"]
        assert cfg["repos"] == ["org/repo1", "org/repo2"]
        assert cfg["dry_run"] is True
        assert cfg["bot_username"] == "my-bot"
        assert cfg["dependabot"]["auto_merge_patch"] is True
        assert "auto_merge_minor" not in cfg["dependabot"]

    def test_dev_agent_basic(self):
        wizard_cfgs = {
            "dev_agent": {
                "dev_repo_url": "https://github.com/org/repo.git",
                "dev_workspace_dir": "/tmp/workspace",
                "dev_dry_run": False,
                "dev_tick_interval_minutes": 30,
                "dev_opencode_model": "qwen3:8b",
                "dev_log_watcher_enabled": True,
            },
        }
        result = _build_plugin_configs(wizard_cfgs)
        cfg = result["dev_agent"]
        assert cfg["repo_url"] == "https://github.com/org/repo.git"
        assert cfg["workspace_dir"] == "/tmp/workspace"
        assert cfg["dry_run"] is False
        assert cfg["opencode"]["model"] == "qwen3:8b"
        assert cfg["log_watcher"]["enabled"] is True

    def test_empty_input(self):
        assert _build_plugin_configs({}) == {}

    def test_omits_default_values(self):
        """Default values (tick intervals) should be omitted from output."""
        wizard_cfgs = {
            "github": {
                "github_repos": "",
                "github_dry_run": True,
                "github_bot_username": "",
                "github_tick_interval_minutes": 15,
                "github_auto_merge_patch": False,
                "github_auto_merge_minor": False,
                "github_auto_merge_major": False,
            },
        }
        result = _build_plugin_configs(wizard_cfgs)
        cfg = result["github"]
        # tick_interval_minutes == 15 (default) should be omitted
        assert "tick_interval_minutes" not in cfg
        # Empty bot_username should be omitted
        assert "bot_username" not in cfg
        # No dependabot flags set
        assert "dependabot" not in cfg


# ---------------------------------------------------------------------------
# _derive_provisioner_state with plugin_configs tests
# ---------------------------------------------------------------------------


class TestDeriveProvisionerStateWithPlugins:
    """Tests that _derive_provisioner_state propagates plugin_configs."""

    def test_plugin_configs_carried_through(self):
        state = {
            "assignments": {
                "email": {
                    "personality": "stal",
                    "temperature": 0.4,
                    "max_tokens": 1500,
                    "heartbeat_hours": 1,
                    "quiet_hours": True,
                    "plugin_config": {
                        "email_filter_mode": "opt_in",
                        "email_dry_run": True,
                    },
                },
                "github_monitor": {
                    "personality": "blixt",
                    "temperature": 0.7,
                    "max_tokens": 2000,
                    "heartbeat_hours": 4,
                    "quiet_hours": True,
                    "plugin_config": {
                        "github_repos": "org/repo",
                        "github_dry_run": True,
                    },
                },
            },
        }
        _derive_provisioner_state(state)

        # Stål should have email_agent plugin config
        stal_cfg = state["agent_configs"]["stal"]
        assert "plugin_configs" in stal_cfg
        assert "email_agent" in stal_cfg["plugin_configs"]
        assert stal_cfg["plugin_configs"]["email_agent"]["email_filter_mode"] == "opt_in"

        # Blixt should have github plugin config
        blixt_cfg = state["agent_configs"]["blixt"]
        assert "github" in blixt_cfg["plugin_configs"]

    def test_no_plugin_config_when_empty(self):
        state = {
            "assignments": {
                "social_media": {
                    "personality": "cherry",
                    "temperature": 0.7,
                    "max_tokens": 2000,
                    "heartbeat_hours": 4,
                    "quiet_hours": True,
                },
            },
        }
        _derive_provisioner_state(state)
        cherry_cfg = state["agent_configs"]["cherry"]
        assert "plugin_configs" not in cherry_cfg


# ---------------------------------------------------------------------------
# Provisioner writes plugins.yaml
# ---------------------------------------------------------------------------


@pytest.fixture
def _seed_master_key(tmp_path: Path):
    """Pre-create a Fernet master key so tests don't depend on macOS Keychain."""
    secrets_dir = tmp_path / "config" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_file = secrets_dir / ".master_key"
    key_file.write_bytes(Fernet.generate_key())
    key_file.chmod(0o600)


class TestProvisionerPluginsYaml:
    """Tests that provision() writes plugins.yaml correctly."""

    @pytest.fixture
    def wizard_state_with_plugins(self) -> dict:
        return {
            "principal": {
                "principal_name": "Test User",
                "principal_email": "test@example.com",
                "timezone": "Europe/Stockholm",
                "language_preference": "en",
            },
            "llm": {
                "llm_provider": "ollama",
                "ollama_host": "127.0.0.1",
                "ollama_port": 11434,
                "model": "qwen3:8b",
                "default_temperature": 0.7,
                "default_max_tokens": 2000,
            },
            "communication": {
                "gmail_enabled": True,
                "gmail_address": "test@gmail.com",
                "gmail_app_password": "test-password",
                "telegram_enabled": False,
            },
            "selected_characters": ["stal"],
            "agent_configs": {
                "stal": {
                    "temperature": 0.4,
                    "max_tokens": 1500,
                    "heartbeat_hours": 1,
                    "quiet_hours": True,
                    "plugins": ["email_agent"],
                    "capabilities": [],
                    "plugin_configs": {
                        "email_agent": {
                            "email_filter_mode": "opt_in",
                            "email_allowed_senders": "boss@example.com\nteam@example.com",
                            "email_blocked_senders": "",
                            "email_dry_run": True,
                            "email_show_draft_replies": False,
                            "email_max_email_age_hours": 48,
                        },
                    },
                },
            },
        }

    def test_plugins_yaml_created(
        self,
        tmp_path: Path,
        wizard_state_with_plugins: dict,
        _seed_master_key,
    ):
        provision(tmp_path, wizard_state_with_plugins)
        plugins_path = tmp_path / "config" / "stal" / "plugins.yaml"
        assert plugins_path.exists()

        with open(plugins_path) as f:
            data = yaml.safe_load(f)

        assert "email_agent" in data
        assert data["email_agent"]["filter_mode"] == "opt_in"
        assert data["email_agent"]["senders"]["allowed"] == [
            "boss@example.com",
            "team@example.com",
        ]
        assert data["email_agent"]["dry_run"] is True

    def test_plugins_yaml_in_created_files(
        self,
        tmp_path: Path,
        wizard_state_with_plugins: dict,
        _seed_master_key,
    ):
        result = provision(tmp_path, wizard_state_with_plugins)
        assert any("plugins.yaml" in f for f in result["created_files"])

    def test_no_plugins_yaml_without_config(
        self,
        tmp_path: Path,
        _seed_master_key,
    ):
        """No plugins.yaml when agent has no plugin_configs."""
        state = {
            "principal": {
                "principal_name": "Test",
                "principal_email": "",
                "timezone": "UTC",
                "language_preference": "en",
            },
            "llm": {
                "llm_provider": "ollama",
                "ollama_host": "127.0.0.1",
                "ollama_port": 11434,
                "model": "qwen3:8b",
                "default_temperature": 0.7,
                "default_max_tokens": 2000,
            },
            "communication": {"gmail_enabled": False, "telegram_enabled": False},
            "selected_characters": ["anomal"],
            "agent_configs": {
                "anomal": {
                    "temperature": 0.7,
                    "max_tokens": 2000,
                    "heartbeat_hours": 4,
                    "quiet_hours": True,
                    "plugins": ["moltbook"],
                    "capabilities": [],
                },
            },
        }
        provision(tmp_path, state)
        assert not (tmp_path / "config" / "anomal" / "plugins.yaml").exists()


# ---------------------------------------------------------------------------
# Identity loader deep-merge
# ---------------------------------------------------------------------------


class TestIdentityLoaderDeepMerge:
    """Tests that identity loading deep-merges plugins.yaml into raw_config."""

    def test_deep_merge_into_raw_config(self, tmp_path: Path):
        """Simulate identity loading with a plugins.yaml overlay."""
        # Create a minimal personality.yaml
        identities_dir = tmp_path / "overblick" / "identities" / "testbot"
        identities_dir.mkdir(parents=True)
        personality = {
            "identity": {"display_name": "TestBot", "role": "Test"},
            "operational": {
                "email_agent": {
                    "reputation": {"decay_rate": 0.1},
                    "filter_mode": "opt_out",
                    "dry_run": False,
                },
                "plugins": ["email_agent"],
            },
        }
        with open(identities_dir / "personality.yaml", "w") as f:
            yaml.dump(personality, f)

        # Create plugins.yaml overlay (only overrides filter_mode and dry_run)
        config_dir = tmp_path / "config" / "testbot"
        config_dir.mkdir(parents=True)
        plugins_overlay = {
            "email_agent": {
                "filter_mode": "opt_in",
                "dry_run": True,
            },
        }
        with open(config_dir / "plugins.yaml", "w") as f:
            yaml.dump(plugins_overlay, f)

        # Monkey-patch the identities dir for the test
        import overblick.identities as ident_mod

        original_dir = ident_mod._IDENTITIES_DIR
        try:
            ident_mod._IDENTITIES_DIR = tmp_path / "overblick" / "identities"
            identity = ident_mod.load_identity("testbot")
        finally:
            ident_mod._IDENTITIES_DIR = original_dir

        # Deep merge should override filter_mode and dry_run
        # but preserve reputation.decay_rate
        email_cfg = identity.raw_config.get("email_agent", {})
        assert email_cfg["filter_mode"] == "opt_in"
        assert email_cfg["dry_run"] is True
        assert email_cfg["reputation"]["decay_rate"] == 0.1
