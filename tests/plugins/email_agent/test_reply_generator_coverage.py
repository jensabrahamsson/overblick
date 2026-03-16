"""
Additional coverage tests for reply_generator module.

Covers uncovered lines:
- generate_and_send: with sender profile that has notes + DB history + tone guidance
- send_draft_notification: with tone guidance
- _consult_tone: consultant returns None
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.models import EmailRecord, SenderProfile
from overblick.plugins.email_agent.reply_generator import ReplyGenerator
from overblick.plugins.email_agent.reputation import ReputationManager


def make_gen(ctx=None, db=None, reputation=None, tmp_path=None):
    if ctx is None:
        ctx = MagicMock()
        ctx.llm_pipeline = AsyncMock()

    if reputation is None:
        profiles_dir = (tmp_path or Path("/tmp")) / "sender_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        reputation = ReputationManager(db=db or MagicMock(), profiles_dir=profiles_dir, thresholds={})

    return ReplyGenerator(ctx=ctx, principal_name="Test", db=db or MagicMock(), reputation=reputation)


def sample_email(**kwargs):
    base = {"sender": "col@ex.com", "subject": "Meeting", "body": "Can we meet?",
            "thread_id": "t1", "message_id": "m1"}
    base.update(kwargs)
    return base


class TestGenerateAndSendWithHistory:
    @pytest.mark.asyncio
    async def test_should_include_sender_notes_in_context(self, tmp_path):
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Sure, let's meet.")
        )
        gmail_cap = AsyncMock()
        gmail_cap.send_reply = AsyncMock(return_value=True)

        # Return tone guidance from consultant
        consultant = AsyncMock()
        consultant.consult = AsyncMock(
            return_value=json.dumps({"tone": "warm", "guidance": "Be empathetic"})
        )
        ctx.get_capability = MagicMock(
            side_effect=lambda name: {
                "gmail": gmail_cap,
                "personality_consultant": consultant,
            }.get(name)
        )

        db = MagicMock()
        db.get_sender_history = AsyncMock(
            return_value=[
                EmailRecord(
                    email_from="col@ex.com",
                    email_subject="Previous",
                    classified_intent="reply",
                    confidence=0.9,
                    reasoning="meeting",
                ),
            ]
        )

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        reputation = ReputationManager(db=db, profiles_dir=profiles_dir, thresholds={})
        profile = SenderProfile(
            email="col@ex.com",
            total_interactions=5,
            preferred_language="en",
            avg_confidence=0.9,
            last_interaction_date="2026-02-20",
            notes="VIP client",
        )
        await reputation.save_sender_profile("col@ex.com", profile)

        gen = ReplyGenerator(ctx=ctx, principal_name="Test", db=db, reputation=reputation)
        result = await gen.generate_and_send(sample_email())

        assert result is True
        # Verify context includes notes and history
        call_kwargs = ctx.llm_pipeline.chat.call_args[1]
        messages = call_kwargs["messages"]
        system_msg = messages[0]["content"]
        assert "VIP client" in system_msg
        assert "Previous" in system_msg


class TestConsultToneReturnsNone:
    @pytest.mark.asyncio
    async def test_should_return_none_when_consultant_returns_none(self, tmp_path):
        ctx = MagicMock()
        consultant = AsyncMock()
        consultant.consult = AsyncMock(return_value=None)
        ctx.get_capability = MagicMock(return_value=consultant)

        gen = make_gen(ctx=ctx, tmp_path=tmp_path)
        result = await gen._consult_tone("s@e.com", "Sub", "Body", "No history")
        assert result is None


class TestSendDraftWithToneGuidance:
    @pytest.mark.asyncio
    async def test_should_include_tone_in_draft_prompt(self, tmp_path):
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Draft reply here.")
        )
        consultant = AsyncMock()
        consultant.consult = AsyncMock(
            return_value=json.dumps({"tone": "warm", "guidance": "Be kind"})
        )
        ctx.get_capability = MagicMock(
            side_effect=lambda name: {
                "personality_consultant": consultant,
            }.get(name)
        )
        notifier = AsyncMock()
        notifier.send_notification_tracked = AsyncMock(return_value=55)

        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        gen = make_gen(ctx=ctx, db=db, tmp_path=tmp_path)
        result = await gen.send_draft_notification(sample_email(), notifier)

        assert result is not None
        tg_id, _body = result
        assert tg_id == 55
