"""Tests for identity loading."""

import pytest
from pydantic import ValidationError
from blick.core.identity import (
    Identity, LLMSettings, QuietHoursSettings, ScheduleSettings,
    SecuritySettings, load_identity, list_identities, _load_yaml,
)


class TestLLMSettings:
    def test_defaults(self):
        s = LLMSettings()
        assert s.model == "qwen3:8b"
        assert s.temperature == 0.7
        assert s.max_tokens == 2000

    def test_frozen(self):
        s = LLMSettings()
        with pytest.raises(ValidationError):
            s.model = "other"


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
        identity = load_identity("anomal")
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
        identity = load_identity("cherry")
        assert identity.name == "cherry"
        assert identity.display_name == "Cherry"
        assert identity.llm.temperature == 0.8
        assert identity.llm.max_tokens == 1500
        assert identity.quiet_hours.start_hour == 23
        assert identity.engagement_threshold == 25

    def test_load_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            load_identity("does_not_exist")

    def test_list_identities(self):
        identities = list_identities()
        assert "anomal" in identities
        assert "cherry" in identities

    def test_raw_config_present(self):
        identity = load_identity("anomal")
        assert isinstance(identity.raw_config, dict)
        assert "interest_keywords" in identity.raw_config

    def test_identity_dir(self):
        identity = load_identity("anomal")
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
