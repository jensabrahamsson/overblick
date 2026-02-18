"""Tests for shared onboarding chat logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.shared.onboarding_chat import (
    build_onboarding_prompt,
    chat_with_identity,
    test_llm_connection as check_llm_connection,
)


class TestBuildOnboardingPrompt:
    """Test prompt construction for onboarding chat."""

    @patch("overblick.identities.build_system_prompt")
    @patch("overblick.identities.load_identity")
    def test_builds_prompt_with_onboarding_context(self, mock_load, mock_build):
        """Prompt includes both base identity prompt and onboarding context."""
        mock_load.return_value = MagicMock()
        mock_build.return_value = "You are Anomal."

        result = build_onboarding_prompt("anomal")

        assert result is not None
        assert "You are Anomal." in result
        assert "ONBOARDING CONTEXT" in result
        assert "Överblick" in result  # noqa: RUF001
        mock_load.assert_called_once_with("anomal")
        mock_build.assert_called_once_with(mock_load.return_value, platform="onboarding")

    @patch("overblick.identities.load_identity", side_effect=FileNotFoundError)
    def test_returns_none_for_unknown_identity(self, mock_load):
        """Returns None when identity cannot be loaded."""
        result = build_onboarding_prompt("nonexistent")
        assert result is None

    @patch("overblick.identities.build_system_prompt")
    @patch("overblick.identities.load_identity")
    def test_prompt_instructs_concise_responses(self, mock_load, mock_build):
        """Onboarding prompt asks for concise (2-3 sentence) responses."""
        mock_load.return_value = MagicMock()
        mock_build.return_value = "Base prompt."

        result = build_onboarding_prompt("anomal")
        assert "2-3 sentences" in result


class TestChatWithIdentity:
    """Test the chat_with_identity function."""

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    @patch("overblick.shared.onboarding_chat.build_onboarding_prompt")
    async def test_successful_chat(self, mock_prompt, mock_llm):
        """Successful chat returns response with success=True."""
        mock_prompt.return_value = "System prompt"
        mock_llm.return_value = "Hello from Anomal!"

        result = await chat_with_identity(
            "anomal", "Hi there!", {"model": "qwen3:8b", "temperature": 0.7}
        )

        assert result["success"] is True
        assert result["response"] == "Hello from Anomal!"
        assert result["identity"] == "anomal"

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat.build_onboarding_prompt")
    async def test_unknown_identity(self, mock_prompt):
        """Returns error when identity prompt cannot be built."""
        mock_prompt.return_value = None

        result = await chat_with_identity("nonexistent", "Hello")

        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    @patch("overblick.shared.onboarding_chat.build_onboarding_prompt")
    async def test_llm_failure(self, mock_prompt, mock_llm):
        """Returns error when LLM call fails."""
        mock_prompt.return_value = "System prompt"
        mock_llm.side_effect = ConnectionError("Ollama is down")

        result = await chat_with_identity("anomal", "Hello")

        assert result["success"] is False
        assert "Ollama is down" in result["error"]

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    @patch("overblick.shared.onboarding_chat.build_onboarding_prompt")
    async def test_llm_returns_none(self, mock_prompt, mock_llm):
        """Returns error when LLM returns empty response."""
        mock_prompt.return_value = "System prompt"
        mock_llm.return_value = None

        result = await chat_with_identity("anomal", "Hello")

        assert result["success"] is False
        assert "No LLM backend" in result["error"]

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    @patch("overblick.shared.onboarding_chat.build_onboarding_prompt")
    async def test_defaults_for_empty_llm_config(self, mock_prompt, mock_llm):
        """Uses default LLM config when none provided."""
        mock_prompt.return_value = "System prompt"
        mock_llm.return_value = "Response"

        await chat_with_identity("anomal", "Hello", None)

        # Should have called with default model
        call_args = mock_llm.call_args
        assert call_args[0][1] == "qwen3:8b"  # default model
        assert call_args[0][2] == 0.7  # default temperature


class TestCheckLLMConnection:
    """Test the test_llm_connection function."""

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    async def test_successful_connection(self, mock_llm):
        """Returns success when LLM responds."""
        mock_llm.return_value = "Hello!"

        result = await check_llm_connection({"model": "qwen3:8b"})

        assert result["success"] is True
        assert result["provider"] == "llm"

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    async def test_failed_connection(self, mock_llm):
        """Returns error when LLM is unreachable."""
        mock_llm.side_effect = ConnectionError("Cannot connect")

        result = await check_llm_connection({"model": "qwen3:8b"})

        assert result["success"] is False
        assert "Cannot connect" in result["error"]

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat._call_llm", new_callable=AsyncMock)
    async def test_empty_response(self, mock_llm):
        """Returns error when LLM returns empty."""
        mock_llm.return_value = None

        result = await check_llm_connection({"model": "qwen3:8b"})

        assert result["success"] is False
        assert "No LLM backend" in result["error"]
