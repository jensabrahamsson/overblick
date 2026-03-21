"""Tests for security settings module."""

import os
from unittest.mock import patch

import pytest

from overblick.core.security import settings as settings_mod
from overblick.core.security.settings import (
    _env_bool,
    allow_raw_access,
    enforce_capabilities,
    is_production,
    raw_llm,
    reset_strict_capabilities,
    safe_mode,
    strict_capabilities,
)


class TestEnvBool:
    def test_should_return_default_when_env_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _env_bool("NONEXISTENT_VAR", default=True) is True
            assert _env_bool("NONEXISTENT_VAR", default=False) is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "Yes", "ON"])
    def test_should_return_true_for_truthy_values(self, val):
        with patch.dict(os.environ, {"TEST_VAR": val}):
            assert _env_bool("TEST_VAR", default=False) is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "anything", ""])
    def test_should_return_false_for_non_truthy_values(self, val):
        with patch.dict(os.environ, {"TEST_VAR": val}):
            assert _env_bool("TEST_VAR", default=True) is False


class TestIsProduction:
    def test_should_reflect_safe_mode_constant(self):
        result = is_production()
        assert result == settings_mod.SAFE_MODE


class TestAllowRawAccess:
    def test_should_reflect_raw_llm_constant(self):
        result = allow_raw_access()
        assert result == settings_mod.RAW_LLM


class TestEnforceCapabilities:
    def test_should_read_env_var(self):
        with patch.dict(os.environ, {"OVERBLICK_STRICT_CAPABILITIES": "1"}):
            assert enforce_capabilities() is True

    def test_should_default_false(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OVERBLICK_STRICT_CAPABILITIES", None)
            assert enforce_capabilities() is False


class TestSafeMode:
    def test_should_read_env_each_call(self):
        with patch.dict(os.environ, {"OVERBLICK_SAFE_MODE": "0"}):
            assert safe_mode() is False
        with patch.dict(os.environ, {"OVERBLICK_SAFE_MODE": "1"}):
            assert safe_mode() is True


class TestRawLlm:
    def test_should_read_env_each_call(self):
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "1"}):
            assert raw_llm() is True
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "0"}):
            assert raw_llm() is False


class TestStrictCapabilities:
    def test_should_cache_value(self):
        reset_strict_capabilities()
        with patch.dict(os.environ, {"OVERBLICK_STRICT_CAPABILITIES": "1"}):
            assert strict_capabilities() is True
            # Should remain cached even if env changes
        assert strict_capabilities() is True
        reset_strict_capabilities()

    def test_should_reset_cache(self):
        reset_strict_capabilities()
        with patch.dict(os.environ, {"OVERBLICK_STRICT_CAPABILITIES": "1"}):
            strict_capabilities()
        reset_strict_capabilities()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OVERBLICK_STRICT_CAPABILITIES", None)
            assert strict_capabilities() is False
        reset_strict_capabilities()


class TestRawLlmDetails:
    """Kill mutants in raw_llm() function."""

    def test_should_read_overblick_raw_llm_env_var(self):
        """raw_llm() reads OVERBLICK_RAW_LLM."""
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "1"}):
            assert raw_llm() is True
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "0"}):
            assert raw_llm() is False

    def test_should_default_raw_llm_to_false(self):
        """raw_llm() defaults to False when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OVERBLICK_RAW_LLM", None)
            assert raw_llm() is False

    def test_should_read_env_on_each_call(self):
        """raw_llm() reads env var on each call (not cached)."""
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "1"}):
            assert raw_llm() is True
        with patch.dict(os.environ, {"OVERBLICK_RAW_LLM": "false"}):
            assert raw_llm() is False


class TestEnvBoolEdgeCases:
    """Kill mutants in _env_bool."""

    def test_should_lowercase_before_comparison(self):
        """Values are lowercased before checking."""
        with patch.dict(os.environ, {"TEST_CASE": "TRUE"}):
            assert _env_bool("TEST_CASE", default=False) is True
        with patch.dict(os.environ, {"TEST_CASE": "True"}):
            assert _env_bool("TEST_CASE", default=False) is True

    def test_should_check_exact_truthy_values(self):
        """Only exact truthy values ('1', 'true', 'yes', 'on') return True."""
        for val in ["1", "true", "yes", "on"]:
            with patch.dict(os.environ, {"TEST_EXACT": val}):
                assert _env_bool("TEST_EXACT", default=False) is True, f"Failed for: {val}"

        # These should all return False
        for val in ["2", "t", "y", "enable", "active"]:
            with patch.dict(os.environ, {"TEST_EXACT": val}):
                assert _env_bool("TEST_EXACT", default=True) is False, f"Should be false for: {val}"
