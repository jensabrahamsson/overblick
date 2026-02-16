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


class TestDiscoverConsultants:
    """Test auto-discovery of identity consultants."""

    @pytest.mark.asyncio
    async def test_discover_consultants_returns_keywords(self):
        """discover_consultants() returns dict of identity → keywords."""
        ctx = make_ctx(identity_name="stal")
        cap = PersonalityConsultantCapability(ctx)

        mock_anomal = _mock_personality("anomal")
        mock_anomal.interest_keywords = ["crypto", "ai", "tech"]
        mock_cherry = _mock_personality("cherry")
        mock_cherry.interest_keywords = ["dating", "relationships"]

        def _load(name):
            return {"anomal": mock_anomal, "cherry": mock_cherry}.get(name)

        with patch(
            "overblick.capabilities.consulting.personality_consultant.PersonalityConsultantCapability._load_identity",
            side_effect=_load,
        ), patch(
            "overblick.identities.list_identities",
            return_value=["anomal", "cherry", "stal"],
        ):
            result = cap.discover_consultants()

        assert "anomal" in result
        assert result["anomal"] == ["crypto", "ai", "tech"]
        assert "cherry" in result
        assert result["cherry"] == ["dating", "relationships"]

    @pytest.mark.asyncio
    async def test_discover_consultants_excludes_self(self):
        """discover_consultants() excludes the capability owner's identity."""
        ctx = make_ctx(identity_name="stal")
        cap = PersonalityConsultantCapability(ctx)

        mock_anomal = _mock_personality("anomal")
        mock_anomal.interest_keywords = ["crypto"]
        mock_stal = _mock_personality("stal")
        mock_stal.interest_keywords = ["email", "protocol"]

        def _load(name):
            return {"anomal": mock_anomal, "stal": mock_stal}.get(name)

        with patch(
            "overblick.capabilities.consulting.personality_consultant.PersonalityConsultantCapability._load_identity",
            side_effect=_load,
        ), patch(
            "overblick.identities.list_identities",
            return_value=["anomal", "stal"],
        ):
            result = cap.discover_consultants()

        assert "stal" not in result
        assert "anomal" in result

    @pytest.mark.asyncio
    async def test_discover_consultants_excludes_specified(self):
        """discover_consultants() excludes identities from the exclude set."""
        ctx = make_ctx(identity_name="stal")
        cap = PersonalityConsultantCapability(ctx)

        mock_anomal = _mock_personality("anomal")
        mock_anomal.interest_keywords = ["crypto"]
        mock_supervisor = _mock_personality("supervisor")
        mock_supervisor.interest_keywords = ["management"]

        def _load(name):
            return {"anomal": mock_anomal, "supervisor": mock_supervisor}.get(name)

        with patch(
            "overblick.capabilities.consulting.personality_consultant.PersonalityConsultantCapability._load_identity",
            side_effect=_load,
        ), patch(
            "overblick.identities.list_identities",
            return_value=["anomal", "supervisor"],
        ):
            result = cap.discover_consultants(exclude={"supervisor"})

        assert "supervisor" not in result
        assert "anomal" in result

    @pytest.mark.asyncio
    async def test_discover_consultants_skips_no_keywords(self):
        """discover_consultants() skips identities without interest_keywords."""
        ctx = make_ctx(identity_name="stal")
        cap = PersonalityConsultantCapability(ctx)

        mock_anomal = _mock_personality("anomal")
        mock_anomal.interest_keywords = ["crypto"]
        mock_empty = _mock_personality("empty")
        mock_empty.interest_keywords = []

        def _load(name):
            return {"anomal": mock_anomal, "empty": mock_empty}.get(name)

        with patch(
            "overblick.capabilities.consulting.personality_consultant.PersonalityConsultantCapability._load_identity",
            side_effect=_load,
        ), patch(
            "overblick.identities.list_identities",
            return_value=["anomal", "empty"],
        ):
            result = cap.discover_consultants()

        assert "anomal" in result
        assert "empty" not in result

    def test_score_match_counts_keywords(self):
        """score_match() counts keyword occurrences in text."""
        assert PersonalityConsultantCapability.score_match(
            "Bitcoin and blockchain technology", ["bitcoin", "blockchain", "defi"],
        ) == 2

        assert PersonalityConsultantCapability.score_match(
            "A nice day for a picnic", ["bitcoin", "blockchain"],
        ) == 0

        # Case-insensitive
        assert PersonalityConsultantCapability.score_match(
            "BITCOIN and BLOCKCHAIN", ["bitcoin", "blockchain"],
        ) == 2
