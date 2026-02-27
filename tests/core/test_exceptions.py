"""Tests for the framework exception hierarchy."""

import pytest

from overblick.core.exceptions import (
    ConfigError,
    DatabaseError,
    LLMConnectionError,
    LLMError,
    LLMTimeoutError,
    OverblickError,
    PluginError,
    SecurityError,
)


class TestExceptionHierarchy:
    """Verify the exception inheritance chain."""

    def test_base_exception_inherits_from_exception(self):
        assert issubclass(OverblickError, Exception)

    def test_config_error_inherits_from_base(self):
        assert issubclass(ConfigError, OverblickError)

    def test_security_error_inherits_from_base(self):
        assert issubclass(SecurityError, OverblickError)

    def test_plugin_error_inherits_from_base(self):
        assert issubclass(PluginError, OverblickError)

    def test_llm_error_inherits_from_base(self):
        assert issubclass(LLMError, OverblickError)

    def test_llm_timeout_inherits_from_llm_error(self):
        assert issubclass(LLMTimeoutError, LLMError)
        assert issubclass(LLMTimeoutError, OverblickError)

    def test_llm_connection_inherits_from_llm_error(self):
        assert issubclass(LLMConnectionError, LLMError)
        assert issubclass(LLMConnectionError, OverblickError)

    def test_database_error_inherits_from_base(self):
        assert issubclass(DatabaseError, OverblickError)


class TestExceptionUsage:
    """Verify exceptions can be raised and caught correctly."""

    def test_catch_broad(self):
        with pytest.raises(OverblickError):
            raise ConfigError("bad config")

    def test_catch_narrow(self):
        with pytest.raises(ConfigError):
            raise ConfigError("bad config")

    def test_catch_llm_subtypes_via_parent(self):
        with pytest.raises(LLMError):
            raise LLMTimeoutError("timed out")
        with pytest.raises(LLMError):
            raise LLMConnectionError("refused")

    def test_message_preserved(self):
        try:
            raise SecurityError("injection detected")
        except SecurityError as e:
            assert str(e) == "injection detected"

    def test_all_exceptions_are_distinct(self):
        classes = {
            OverblickError, ConfigError, SecurityError,
            PluginError, LLMError, LLMTimeoutError,
            LLMConnectionError, DatabaseError,
        }
        assert len(classes) == 8
