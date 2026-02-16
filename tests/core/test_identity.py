"""Tests for identity loading."""

import pytest
from pydantic import ValidationError
from overblick.identities import (
    Identity, LLMSettings, QuietHoursSettings, ScheduleSettings,
    SecuritySettings, load_personality, list_personalities, _load_yaml,
)


class TestLLMSettings:
    def test_defaults(self):
        s = LLMSettings()
        assert s.model == "qwen3:8b"
        assert s.temperature == 0.7
        assert s.max_tokens == 2000
        assert s.provider == "ollama"

    def test_frozen(self):
        s = LLMSettings()
        with pytest.raises(ValidationError):
            s.model = "other"

    def test_provider_cloud(self):
        s = LLMSettings(
            provider="cloud",
            cloud_api_url="https://api.openai.com/v1",
            cloud_model="gpt-4o",
            cloud_secret_key="my_api_key",
        )
        assert s.provider == "cloud"
        assert s.cloud_api_url == "https://api.openai.com/v1"
        assert s.cloud_model == "gpt-4o"
        assert s.cloud_secret_key == "my_api_key"

    def test_provider_gateway(self):
        s = LLMSettings(provider="gateway", gateway_url="http://10.0.0.1:8200")
        assert s.provider == "gateway"
        assert s.gateway_url == "http://10.0.0.1:8200"

    def test_backward_compat_use_gateway(self):
        """use_gateway=True should migrate to provider='gateway'."""
        s = LLMSettings.model_validate({"use_gateway": True})
        assert s.provider == "gateway"

    def test_backward_compat_use_gateway_false(self):
        """use_gateway=False should leave provider as default 'ollama'."""
        s = LLMSettings.model_validate({"use_gateway": False})
        assert s.provider == "ollama"

    def test_explicit_provider_overrides_use_gateway(self):
        """Explicit provider takes precedence over use_gateway."""
        s = LLMSettings.model_validate({"use_gateway": True, "provider": "ollama"})
        assert s.provider == "ollama"


class TestQuietHoursSettings:
    def test_defaults(self):
        s = QuietHoursSettings()
        assert s.start_hour == 21
        assert s.end_hour == 7
        assert s.timezone == "Europe/Stockholm"


class TestIdentity:
    def test_defaults(self):
        i = Identity(name="test")
        assert i.name == "test"
        assert i.display_name == "Test"
        assert i.engagement_threshold == 35

    def test_frozen(self):
        i = Identity(name="test")
        with pytest.raises(ValidationError):
            i.name = "other"

    def test_has_module(self):
        i = Identity(name="test", enabled_modules=("dream_system", "therapy_system"))
        assert i.has_module("dream_system")
        assert not i.has_module("nonexistent")


class TestLoadIdentity:
    def test_load_anomal(self):
        identity = load_personality("anomal")
        assert identity.name == "anomal"
        assert identity.display_name == "Anomal"
        assert identity.llm.temperature == 0.7
        assert identity.llm.max_tokens == 2000
        assert identity.quiet_hours.start_hour == 21
        assert "dream_system" in identity.enabled_modules
        assert len(identity.interest_keywords) > 0
        assert len(identity.personality) > 0
        assert len(identity.knowledge) > 0

    def test_load_cherry(self):
        identity = load_personality("cherry")
        assert identity.name == "cherry"
        assert identity.display_name == "Cherry"
        assert identity.llm.temperature == 0.8
        assert identity.llm.max_tokens == 1500
        assert identity.quiet_hours.start_hour == 23
        assert identity.engagement_threshold == 25

    def test_load_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            load_personality("does_not_exist")

    def test_list_identities(self):
        identities = list_personalities()
        assert "anomal" in identities
        assert "cherry" in identities

    def test_raw_config_present(self):
        identity = load_personality("anomal")
        assert isinstance(identity.raw_config, dict)
        assert "interest_keywords" in identity.raw_config

    def test_identity_dir(self):
        identity = load_personality("anomal")
        assert identity.identity_dir.exists()
        assert identity.identity_dir.name == "anomal"


class TestModelValidate:
    def test_ignores_unknown_keys(self):
        result = LLMSettings.model_validate({"model": "test", "unknown_key": 42})
        assert result.model == "test"

    def test_empty_dict(self):
        result = LLMSettings.model_validate({})
        assert result.model == "qwen3:8b"


class TestLoadYaml:
    def test_missing_file(self, tmp_path):
        result = _load_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        result = _load_yaml(f)
        assert result == {}

    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnested:\n  a: 1")
        result = _load_yaml(f)
        assert result["key"] == "value"
        assert result["nested"]["a"] == 1
