"""
Coverage tests for reply_generator line 176: send_draft_notification with profile notes.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.models import EmailRecord, SenderProfile
from overblick.plugins.email_agent.reply_generator import ReplyGenerator
from overblick.plugins.email_agent.reputation import ReputationManager


class TestSendDraftWithNotes:
    @pytest.mark.asyncio
    async def test_should_include_profile_notes_in_draft(self, tmp_path):
        """Line 176: profile.notes is appended to sender_context in send_draft_notification."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Draft with notes.")
        )
        ctx.get_capability = MagicMock(return_value=None)

        db = MagicMock()
        db.get_sender_history = AsyncMock(
            return_value=[
                EmailRecord(
                    email_from="vip@ex.com",
                    email_subject="Previous meeting",
                    classified_intent="reply",
                    confidence=0.95,
                    reasoning="meeting",
                ),
            ]
        )

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        reputation = ReputationManager(db=db, profiles_dir=profiles_dir, thresholds={})

        profile = SenderProfile(
            email="vip@ex.com",
            total_interactions=10,
            preferred_language="en",
            avg_confidence=0.9,
            last_interaction_date="2026-03-01",
            notes="Key stakeholder, always respond promptly",
        )
        await reputation.save_sender_profile("vip@ex.com", profile)

        notifier = AsyncMock()
        notifier.send_notification_tracked = AsyncMock(return_value=99)

        gen = ReplyGenerator(ctx=ctx, principal_name="Boss", db=db, reputation=reputation)

        email = {
            "sender": "vip@ex.com",
            "subject": "Follow up",
            "body": "Any updates?",
            "thread_id": "t1",
            "message_id": "m1",
        }

        result = await gen.send_draft_notification(email, notifier)

        assert result is not None
        tg_id, body = result
        assert tg_id == 99

        # Verify that notes were included in the LLM prompt
        call_kwargs = ctx.llm_pipeline.chat.call_args[1]
        messages = call_kwargs["messages"]
        system_msg = messages[0]["content"]
        assert "Key stakeholder" in system_msg
