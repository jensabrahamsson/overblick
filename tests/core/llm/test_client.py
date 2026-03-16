"""Tests for abstract LLM client interface."""

import os
from unittest.mock import patch

import pytest

from overblick.core.exceptions import SecurityError
from overblick.core.llm.client import _THINK_RE, LLMClient


class ConcreteLLMClient(LLMClient):
    """Minimal concrete implementation for testing."""

    def __init__(self):
        self._check_instantiation_allowed()

    async def chat(self, messages, temperature=None, max_tokens=None,
                   top_p=None, priority="low", complexity=None):
        return {"content": "test"}

    async def health_check(self):
        return True

    async def close(self):
        pass


class TestStripThinkTokens:
    def test_should_strip_think_blocks(self):
        result = LLMClient.strip_think_tokens("<think>reasoning</think>Answer")
        assert result == "Answer"

    def test_should_handle_no_think_tokens(self):
        result = LLMClient.strip_think_tokens("Just a normal response")
        assert result == "Just a normal response"

    def test_should_strip_multiline_think(self):
        text = "<think>\nStep 1\nStep 2\n</think>Final answer"
        result = LLMClient.strip_think_tokens(text)
        assert result == "Final answer"

    def test_should_strip_multiple_blocks(self):
        text = "<think>a</think>Hello <think>b</think>World"
        result = LLMClient.strip_think_tokens(text)
        assert result == "Hello World"

    def test_should_strip_and_trim_whitespace(self):
        text = "  <think>x</think>  Answer  "
        result = LLMClient.strip_think_tokens(text)
        assert result == "Answer"

    def test_should_handle_empty_string(self):
        result = LLMClient.strip_think_tokens("")
        assert result == ""

    def test_should_handle_empty_think_block(self):
        result = LLMClient.strip_think_tokens("<think></think>Result")
        assert result == "Result"


class TestThinkRegex:
    def test_should_match_think_tags(self):
        assert _THINK_RE.search("<think>foo</think>") is not None

    def test_should_use_dotall_flag(self):
        """The regex uses DOTALL to match across lines."""
        assert _THINK_RE.search("<think>\nfoo\n</think>") is not None


class TestInstantiationSecurity:
    def test_should_raise_security_error_when_direct_instantiation_forbidden(self):
        """Direct instantiation raises SecurityError when not allowed."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERBLICK_ALLOW_DIRECT_LLM", None)
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
            with pytest.raises(SecurityError, match="FORBIDDEN"):
                ConcreteLLMClient()
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False

    def test_should_check_env_var_value_exactly(self):
        """Only '1' allows direct instantiation, not other truthy values."""
        with patch.dict(os.environ, {"OVERBLICK_ALLOW_DIRECT_LLM": "true"}):
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
            with pytest.raises(SecurityError):
                ConcreteLLMClient()

    def test_should_allow_when_env_var_is_1(self):
        with patch.dict(os.environ, {"OVERBLICK_ALLOW_DIRECT_LLM": "1"}):
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
            client = ConcreteLLMClient()
            assert client is not None

    def test_should_allow_when_flag_is_true(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = True
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERBLICK_ALLOW_DIRECT_LLM", None)
            client = ConcreteLLMClient()
            assert client is not None
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False

    def test_should_set_flag_with_allow_instantiation(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
        ConcreteLLMClient._allow_instantiation()
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is True
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False

    def test_should_clear_flag_with_disallow_instantiation(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = True
        ConcreteLLMClient._disallow_instantiation()
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is False

    def test_should_restore_flag_in_context_manager(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
        with ConcreteLLMClient._instantiation_allowed():
            assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is True
            client = ConcreteLLMClient()
            assert client is not None
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is False

    def test_should_restore_flag_on_exception(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
        with pytest.raises(ValueError, match="boom"):
            with ConcreteLLMClient._instantiation_allowed():
                raise ValueError("boom")
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is False

    def test_should_have_false_default_class_attr(self):
        assert LLMClient._ALLOW_DIRECT_INSTANTIATION is False

    def test_should_include_plugin_context_in_error_message(self):
        """Security error message mentions PluginContext.llm_pipeline."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERBLICK_ALLOW_DIRECT_LLM", None)
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
            with pytest.raises(SecurityError, match=r"PluginContext\.llm_pipeline"):
                ConcreteLLMClient()
