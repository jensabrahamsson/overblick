"""
Additional coverage tests for email classifier.

Covers uncovered lines:
- classify: sender history from DB, no DB
- _parse: ValueError/KeyError exception path
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.classifier import EmailClassifier
from overblick.plugins.email_agent.models import AgentState, EmailIntent, EmailRecord


def make_classifier(ctx=None, db=None, **kwargs):
    if ctx is None:
        ctx = MagicMock()
        ctx.llm_pipeline = AsyncMock()
    return EmailClassifier(
        ctx=ctx,
        state=AgentState(),
        learnings=[],
        db=db,
        principal_name="Test",
        allowed_senders=set(),
        **kwargs,
    )


class TestClassifyWithSenderHistory:
    @pytest.mark.asyncio
    async def test_should_include_sender_history_in_prompt(self):
        ctx = MagicMock()
        valid_json = '{"intent": "reply", "confidence": 0.9, "reasoning": "Known sender", "priority": "normal"}'
        ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content=valid_json))

        db = MagicMock()
        db.get_sender_history = AsyncMock(
            return_value=[
                EmailRecord(
                    email_from="alice@example.com",
                    email_subject="Previous email",
                    classified_intent="reply",
                    confidence=0.9,
                    reasoning="meeting",
                ),
            ]
        )

        c = make_classifier(ctx=ctx, db=db)
        result = await c.classify("alice@example.com", "Follow up", "Body")

        assert result is not None
        assert result.intent == EmailIntent.REPLY
        db.get_sender_history.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_work_without_db(self):
        ctx = MagicMock()
        valid_json = '{"intent": "notify", "confidence": 0.8, "reasoning": "Info", "priority": "normal"}'
        ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content=valid_json))

        c = make_classifier(ctx=ctx, db=None)
        result = await c.classify("sender@example.com", "Subject", "Body")

        assert result is not None


class TestParseValueError:
    def test_should_handle_invalid_confidence_gracefully(self):
        c = make_classifier()
        raw = '{"intent": "ignore", "confidence": "bad", "reasoning": "x", "priority": "low"}'
        result = c._parse(raw)
        # float("bad") raises ValueError, caught by except (ValueError, KeyError)
        assert result is None
