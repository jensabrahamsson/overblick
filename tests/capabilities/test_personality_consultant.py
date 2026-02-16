"""
Tests for PersonalityConsultantCapability — cross-personality LLM consultation.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from overblick.core.capability import CapabilityContext
from overblick.core.llm.pipeline import PipelineResult
from overblick.capabilities.consulting.personality_consultant import (
    PersonalityConsultantCapability,
)


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


def _mock_personality(name: str = "cherry") -> MagicMock:
    """Create a mock Personality object."""
    p = MagicMock()
    p.name = name
    p.display_name = name.capitalize()
    return p


class TestPersonalityConsultantCapability:
    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = PersonalityConsultantCapability(ctx)
        assert cap.name == "personality_consultant"

    @pytest.mark.asyncio
    async def test_setup_defaults(self):
        ctx = make_ctx()
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()
        assert cap._default_consultant == "cherry"
        assert cap._temperature == 0.7
        assert cap._max_tokens == 800

    @pytest.mark.asyncio
    async def test_setup_custom_config(self):
        ctx = make_ctx(config={
            "default_consultant": "anomal",
            "temperature": 0.9,
            "max_tokens": 1200,
        })
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()
        assert cap._default_consultant == "anomal"
        assert cap._temperature == 0.9
        assert cap._max_tokens == 1200

    @pytest.mark.asyncio
    async def test_consult_returns_response(self):
        """Happy path: pipeline returns advice from consultant."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"tone": "warm", "guidance": "The sender sounds stressed."}',
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        # Mock personality loading
        mock_personality = _mock_personality()
        with patch(
            "overblick.identities.load_identity",
            return_value=mock_personality,
        ), patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Cherry, a relationship expert.",
        ):
            result = await cap.consult(
                query="Should this reply be warm?",
                context="Email about a personal matter.",
                consultant_name="cherry",
            )

        assert result is not None
        assert "warm" in result
        pipeline.chat.assert_called_once()

        # Verify system prompt was passed
        call_args = pipeline.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert messages[0]["role"] == "system"
        assert "Cherry" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_consult_uses_default_consultant(self):
        """When no consultant_name given, uses default from config."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="Some advice.",
        ))
        ctx = make_ctx(llm_pipeline=pipeline, config={"default_consultant": "blixt"})
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        mock_personality = _mock_personality("blixt")
        with patch(
            "overblick.identities.load_identity",
            return_value=mock_personality,
        ), patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Blixt.",
        ):
            result = await cap.consult(query="Give me advice.")

        assert result == "Some advice."
        call_args = pipeline.chat.call_args
        assert call_args.kwargs.get("audit_action") == "consult_blixt"

    @pytest.mark.asyncio
    async def test_consult_caches_personality(self):
        """Second call to same personality doesn't reload from disk."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="Advice.",
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        mock_personality = _mock_personality()
        with patch(
            "overblick.identities.load_identity",
            return_value=mock_personality,
        ) as mock_load, patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Cherry.",
        ):
            await cap.consult(query="First question.", consultant_name="cherry")
            await cap.consult(query="Second question.", consultant_name="cherry")

        # load_personality called only once — cached after that
        mock_load.assert_called_once_with("cherry")

    @pytest.mark.asyncio
    async def test_consult_unknown_personality_returns_none(self):
        """If personality YAML doesn't exist, returns None gracefully."""
        pipeline = AsyncMock()
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.identities.load_identity",
            side_effect=FileNotFoundError("No such personality"),
        ):
            result = await cap.consult(
                query="Hello?", consultant_name="nonexistent",
            )

        assert result is None
        pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_consult_blocked_returns_none(self):
        """If pipeline blocks the response, returns None."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            blocked=True, block_reason="Output safety triggered",
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.identities.load_identity",
            return_value=_mock_personality(),
        ), patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Cherry.",
        ):
            result = await cap.consult(query="Test.", consultant_name="cherry")

        assert result is None

    @pytest.mark.asyncio
    async def test_consult_no_pipeline_returns_none(self):
        """Without an LLM pipeline, returns None."""
        ctx = make_ctx()
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        result = await cap.consult(query="Hello?", consultant_name="cherry")
        assert result is None

    @pytest.mark.asyncio
    async def test_consult_pipeline_error_returns_none(self):
        """If pipeline raises an exception, returns None gracefully."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(side_effect=Exception("LLM down"))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.identities.load_identity",
            return_value=_mock_personality(),
        ), patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Cherry.",
        ):
            result = await cap.consult(query="Test.", consultant_name="cherry")

        assert result is None

    @pytest.mark.asyncio
    async def test_consult_includes_context_in_message(self):
        """Context parameter is appended to the user message."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="Got it.",
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.identities.load_identity",
            return_value=_mock_personality(),
        ), patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Cherry.",
        ):
            await cap.consult(
                query="Tone check?",
                context="Email about a wedding invitation.",
                consultant_name="cherry",
            )

        call_args = pipeline.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[1]["content"]
        assert "Tone check?" in user_msg
        assert "wedding invitation" in user_msg

    @pytest.mark.asyncio
    async def test_consult_skip_preflight_and_low_priority(self):
        """Verify internal consultation uses skip_preflight and low priority."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="Advice.",
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = PersonalityConsultantCapability(ctx)
        await cap.setup()

        with patch(
            "overblick.identities.load_identity",
            return_value=_mock_personality(),
        ), patch(
            "overblick.identities.build_system_prompt",
            return_value="You are Cherry.",
        ):
            await cap.consult(query="Test.", consultant_name="cherry")

        call_kwargs = pipeline.chat.call_args.kwargs
        assert call_kwargs.get("skip_preflight") is True
        assert call_kwargs.get("priority") == "low"
        assert call_kwargs.get("audit_action") == "consult_cherry"
