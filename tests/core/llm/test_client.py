"""Tests for abstract LLM client interface."""

import os
from unittest.mock import patch

import pytest

from overblick.core.exceptions import SecurityError
from overblick.core.llm.client import LLMClient


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


class TestInstantiationSecurity:
    def test_should_raise_security_error_when_direct_instantiation_forbidden(self):
        """Direct instantiation raises SecurityError when not allowed."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERBLICK_ALLOW_DIRECT_LLM", None)
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
            with pytest.raises(SecurityError, match="FORBIDDEN"):
                ConcreteLLMClient()
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False

    def test_should_allow_when_env_var_set(self):
        with patch.dict(os.environ, {"OVERBLICK_ALLOW_DIRECT_LLM": "1"}):
            ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
            client = ConcreteLLMClient()
            assert client is not None

    def test_allow_instantiation_sets_flag(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
        ConcreteLLMClient._allow_instantiation()
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is True
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False

    def test_disallow_instantiation_clears_flag(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = True
        ConcreteLLMClient._disallow_instantiation()
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is False

    def test_instantiation_allowed_context_manager(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
        with ConcreteLLMClient._instantiation_allowed():
            assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is True
            client = ConcreteLLMClient()
            assert client is not None
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is False

    def test_instantiation_allowed_restores_on_exception(self):
        ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION = False
        with pytest.raises(ValueError, match="boom"):
            with ConcreteLLMClient._instantiation_allowed():
                raise ValueError("boom")
        assert ConcreteLLMClient._ALLOW_DIRECT_INSTANTIATION is False
