"""
Unit tests for ReplyGenerator — isolated from plugin lifecycle.

Tests cover:
- generate_and_send() — happy path, dry run handled at plugin level, gmail send
- send_draft_notification() — LLM + Telegram, blocked result, notifier=None
- _consult_tone() — capability missing, JSON parse error, warm tone
- _request_research() — capability present and missing
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.reply_generator import ReplyGenerator
from overblick.plugins.email_agent.reputation import ReputationManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_reply_gen(ctx=None, principal_name="Test User", db=None, reputation=None, tmp_path=None):
    """Create a ReplyGenerator with minimal config for unit tests."""
    if ctx is None:
        ctx = MagicMock()
        ctx.llm_pipeline = AsyncMock()

    if reputation is None:
        profiles_dir = (tmp_path or Path("/tmp")) / "sender_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        reputation = ReputationManager(
            db=db or MagicMock(),
            profiles_dir=profiles_dir,
            thresholds={},
        )

    return ReplyGenerator(
        ctx=ctx,
        principal_name=principal_name,
        db=db or MagicMock(),
        reputation=reputation,
    )


def sample_email(**kwargs):
    base = {
        "sender": "colleague@example.com",
        "subject": "Meeting next week?",
        "body": "Can we schedule a meeting?",
        "thread_id": "thread-001",
        "message_id": "msg-001",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# generate_and_send
# ---------------------------------------------------------------------------

class TestGenerateAndSend:
    """Tests for ReplyGenerator.generate_and_send()."""

    @pytest.mark.asyncio
    async def test_sends_reply_via_gmail(self, tmp_path):
        """Happy path: LLM generates reply, Gmail capability sends it."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Dear colleague, happy to meet Tuesday.")
        )
        gmail_cap = AsyncMock()
        gmail_cap.send_reply = AsyncMock(return_value=True)
        ctx.get_capability = MagicMock(side_effect=lambda name: {
            "gmail": gmail_cap,
            "personality_consultant": None,
        }.get(name))

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_reply_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        result = await gen.generate_and_send(sample_email())

        assert result is True
        gmail_cap.send_reply.assert_called_once()
        call_kwargs = gmail_cap.send_reply.call_args.kwargs
        assert call_kwargs["to"] == "colleague@example.com"
        assert call_kwargs["subject"] == "Re: Meeting next week?"
        assert call_kwargs["thread_id"] == "thread-001"
        assert call_kwargs["message_id"] == "msg-001"

    @pytest.mark.asyncio
    async def test_returns_false_when_gmail_unavailable(self, tmp_path):
        """Returns False when Gmail capability is not registered."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Sure, let's meet.")
        )
        ctx.get_capability = MagicMock(return_value=None)

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_reply_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        result = await gen.generate_and_send(sample_email())

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_llm_blocked(self, tmp_path):
        """Returns False when LLM pipeline blocks the reply."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="", blocked=True)
        )
        ctx.get_capability = MagicMock(return_value=None)

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_reply_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        result = await gen.generate_and_send(sample_email())

        assert result is False

    @pytest.mark.asyncio
    async def test_subject_prefixed_with_re(self, tmp_path):
        """Subject is prefixed with 'Re:' if not already prefixed."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Reply text.")
        )
        gmail_cap = AsyncMock()
        gmail_cap.send_reply = AsyncMock(return_value=True)
        ctx.get_capability = MagicMock(side_effect=lambda name: {
            "gmail": gmail_cap,
            "personality_consultant": None,
        }.get(name))

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_reply_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        await gen.generate_and_send(sample_email(subject="Hello"))

        call_kwargs = gmail_cap.send_reply.call_args.kwargs
        assert call_kwargs["subject"] == "Re: Hello"

    @pytest.mark.asyncio
    async def test_existing_re_prefix_not_doubled(self, tmp_path):
        """Subjects already starting with 'Re:' are not double-prefixed."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Reply text.")
        )
        gmail_cap = AsyncMock()
        gmail_cap.send_reply = AsyncMock(return_value=True)
        ctx.get_capability = MagicMock(side_effect=lambda name: {
            "gmail": gmail_cap,
            "personality_consultant": None,
        }.get(name))

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_reply_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        await gen.generate_and_send(sample_email(subject="Re: Previous thread"))

        call_kwargs = gmail_cap.send_reply.call_args.kwargs
        assert call_kwargs["subject"] == "Re: Previous thread"

    @pytest.mark.asyncio
    async def test_returns_false_on_llm_exception(self, tmp_path):
        """Returns False gracefully when LLM raises an exception."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        ctx.get_capability = MagicMock(return_value=None)

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_reply_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        result = await gen.generate_and_send(sample_email())

        assert result is False


# ---------------------------------------------------------------------------
# send_draft_notification
# ---------------------------------------------------------------------------

class TestSendDraftNotification:
    """Tests for ReplyGenerator.send_draft_notification()."""

    @pytest.mark.asyncio
    async def test_sends_draft_via_notifier(self, tmp_path):
        """Happy path: LLM generates draft, notifier sends it."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Dear colleague, I'll check the calendar.")
        )
        notifier = AsyncMock()
        notifier.send_notification = AsyncMock()

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        await gen.send_draft_notification(sample_email(), notifier)

        notifier.send_notification.assert_called_once()
        text = notifier.send_notification.call_args[0][0]
        assert "Suggested reply" in text
        assert "I'll check the calendar" in text

    @pytest.mark.asyncio
    async def test_no_op_when_notifier_is_none(self, tmp_path):
        """Returns immediately without LLM call when notifier is None."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock()

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        await gen.send_draft_notification(sample_email(), notifier=None)

        ctx.llm_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_op_when_llm_blocked(self, tmp_path):
        """Does not call notifier when LLM result is blocked."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="", blocked=True)
        )
        notifier = AsyncMock()

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        await gen.send_draft_notification(sample_email(), notifier)

        notifier.send_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self, tmp_path):
        """Exceptions during draft notification are swallowed (best-effort)."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM error"))
        notifier = AsyncMock()

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        # Should not raise
        await gen.send_draft_notification(sample_email(), notifier)


# ---------------------------------------------------------------------------
# _consult_tone
# ---------------------------------------------------------------------------

class TestConsultTone:
    """Tests for ReplyGenerator._consult_tone()."""

    @pytest.mark.asyncio
    async def test_returns_none_when_capability_missing(self, tmp_path):
        """Returns None when personality_consultant capability is unavailable."""
        ctx = MagicMock()
        ctx.get_capability = MagicMock(return_value=None)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._consult_tone("sender@e.com", "Subject", "Body", "No history")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_guidance_for_warm_tone(self, tmp_path):
        """Returns tone guidance string when consultant advises warm tone."""
        ctx = MagicMock()
        advice_json = json.dumps({"tone": "warm", "guidance": "Use empathetic language."})
        consultant = AsyncMock()
        consultant.consult = AsyncMock(return_value=advice_json)
        ctx.get_capability = MagicMock(return_value=consultant)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._consult_tone("sender@e.com", "Subject", "Body", "No history")

        assert result is not None
        assert "empathetic" in result

    @pytest.mark.asyncio
    async def test_returns_none_for_professional_tone(self, tmp_path):
        """Returns None when consultant advises professional (default) tone."""
        ctx = MagicMock()
        advice_json = json.dumps({"tone": "professional", "guidance": "Be concise."})
        consultant = AsyncMock()
        consultant.consult = AsyncMock(return_value=advice_json)
        ctx.get_capability = MagicMock(return_value=consultant)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._consult_tone("sender@e.com", "Subject", "Body", "No history")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_json_parse_error(self, tmp_path):
        """Returns None gracefully when consultant returns unparseable JSON."""
        ctx = MagicMock()
        consultant = AsyncMock()
        consultant.consult = AsyncMock(return_value="not json at all")
        ctx.get_capability = MagicMock(return_value=consultant)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._consult_tone("sender@e.com", "Subject", "Body", "No history")

        assert result is None


# ---------------------------------------------------------------------------
# _request_research
# ---------------------------------------------------------------------------

class TestRequestResearch:
    """Tests for ReplyGenerator._request_research()."""

    @pytest.mark.asyncio
    async def test_returns_research_when_capability_available(self, tmp_path):
        """Returns research result when boss_request capability is configured."""
        ctx = MagicMock()
        boss_cap = AsyncMock()
        boss_cap.configured = True
        boss_cap.request_research = AsyncMock(return_value="Research summary: 42.")
        ctx.get_capability = MagicMock(return_value=boss_cap)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._request_research("What is the answer?")

        assert result == "Research summary: 42."
        boss_cap.request_research.assert_called_once_with("What is the answer?", "")

    @pytest.mark.asyncio
    async def test_returns_none_when_capability_missing(self, tmp_path):
        """Returns None when boss_request capability is not registered."""
        ctx = MagicMock()
        ctx.get_capability = MagicMock(return_value=None)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._request_research("test query")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_capability_not_configured(self, tmp_path):
        """Returns None when boss_request capability exists but is not configured."""
        ctx = MagicMock()
        boss_cap = AsyncMock()
        boss_cap.configured = False
        ctx.get_capability = MagicMock(return_value=boss_cap)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._request_research("test query")

        assert result is None

    @pytest.mark.asyncio
    async def test_passes_context_string(self, tmp_path):
        """Optional context string is passed through to the capability."""
        ctx = MagicMock()
        boss_cap = AsyncMock()
        boss_cap.configured = True
        boss_cap.request_research = AsyncMock(return_value="Result.")
        ctx.get_capability = MagicMock(return_value=boss_cap)

        gen = make_reply_gen(ctx=ctx, tmp_path=tmp_path)
        await gen._request_research("query", "extra context")

        boss_cap.request_research.assert_called_once_with("query", "extra context")
