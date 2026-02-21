"""
Unit tests for EmailClassifier — isolated from plugin lifecycle.

Tests cover:
- JSON parsing (valid, surrounding text, invalid, aliases)
- Intent normalization (static, no setup needed)
- Reputation context building (static)
- Email signal extraction (static)
- LLM classification with retry logic
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.classifier import EmailClassifier
from overblick.plugins.email_agent.models import (
    AgentState,
    EmailClassification,
    EmailIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_classifier(
    ctx=None, db=None, principal_name="Test User", allowed_senders=None,
    filter_mode="opt_in", blocked_senders=None,
):
    """Create an EmailClassifier with minimal config for unit tests."""
    if ctx is None:
        ctx = MagicMock()
        ctx.llm_pipeline = AsyncMock()
    return EmailClassifier(
        ctx=ctx,
        state=AgentState(),
        learnings=[],
        db=db,
        principal_name=principal_name,
        allowed_senders=allowed_senders or set(),
        filter_mode=filter_mode,
        blocked_senders=blocked_senders,
    )


# ---------------------------------------------------------------------------
# _parse — JSON parsing
# ---------------------------------------------------------------------------

class TestParse:
    """Tests for EmailClassifier._parse()."""

    def test_valid_json_reply(self):
        """Parses valid reply intent."""
        c = make_classifier()
        raw = '{"intent": "reply", "confidence": 0.95, "reasoning": "Meeting request", "priority": "normal"}'
        result = c._parse(raw)
        assert result is not None
        assert result.intent == EmailIntent.REPLY
        assert result.confidence == 0.95
        assert result.priority == "normal"

    def test_valid_json_ignore(self):
        """Parses valid ignore intent."""
        c = make_classifier()
        raw = '{"intent": "ignore", "confidence": 0.88, "reasoning": "Newsletter", "priority": "low"}'
        result = c._parse(raw)
        assert result is not None
        assert result.intent == EmailIntent.IGNORE

    def test_json_embedded_in_prose(self):
        """Extracts JSON from surrounding text."""
        c = make_classifier()
        raw = 'Here is my analysis:\n{"intent": "ignore", "confidence": 0.85, "reasoning": "Spam", "priority": "low"}\nDone.'
        result = c._parse(raw)
        assert result is not None
        assert result.intent == EmailIntent.IGNORE

    def test_invalid_json_returns_none(self):
        """Returns None when no JSON found."""
        c = make_classifier()
        assert c._parse("This is not JSON at all") is None

    def test_ask_boss_intent(self):
        """Parses ask_boss intent correctly."""
        c = make_classifier()
        raw = '{"intent": "ask_boss", "confidence": 0.4, "reasoning": "Uncertain", "priority": "high"}'
        result = c._parse(raw)
        assert result is not None
        assert result.intent == EmailIntent.ASK_BOSS
        assert result.confidence == 0.4

    def test_hallucinated_intent_normalized_to_ignore(self):
        """Hallucinated intents (e.g. 'spam') are normalized before returning."""
        c = make_classifier()
        raw = '{"intent": "spam", "confidence": 0.9, "reasoning": "Junk mail", "priority": "low"}'
        result = c._parse(raw)
        assert result is not None
        assert result.intent == EmailIntent.IGNORE

    def test_completely_unknown_intent_defaults_to_ignore(self):
        """Unknown intents not in alias map default to ignore."""
        c = make_classifier()
        raw = '{"intent": "do_a_backflip", "confidence": 0.5, "reasoning": "??", "priority": "normal"}'
        result = c._parse(raw)
        assert result is not None
        assert result.intent == EmailIntent.IGNORE

    def test_missing_confidence_defaults_to_half(self):
        """Missing confidence defaults to 0.5."""
        c = make_classifier()
        raw = '{"intent": "notify", "reasoning": "Interesting", "priority": "normal"}'
        result = c._parse(raw)
        assert result is not None
        assert result.confidence == 0.5

    def test_broken_json_within_braces_returns_none(self):
        """Broken JSON with braces returns None gracefully."""
        c = make_classifier()
        result = c._parse("{not valid json}")
        assert result is None


# ---------------------------------------------------------------------------
# normalize_intent — static, no setup needed
# ---------------------------------------------------------------------------

class TestNormalizeIntent:
    """Tests for EmailClassifier.normalize_intent() — pure function."""

    def test_valid_intents_pass_through(self):
        """All four valid EmailIntent values pass through unchanged."""
        assert EmailClassifier.normalize_intent("ignore") == "ignore"
        assert EmailClassifier.normalize_intent("notify") == "notify"
        assert EmailClassifier.normalize_intent("reply") == "reply"
        assert EmailClassifier.normalize_intent("ask_boss") == "ask_boss"

    def test_aliases_map_to_ask_boss(self):
        """Common LLM 'escalation' aliases map to ask_boss."""
        for alias in ("escalate", "verify", "flag", "report"):
            assert EmailClassifier.normalize_intent(alias) == "ask_boss", f"alias={alias!r}"

    def test_aliases_map_to_ignore(self):
        """Common LLM 'filter' aliases map to ignore."""
        for alias in ("block", "spam", "delete", "archive", "skip"):
            assert EmailClassifier.normalize_intent(alias) == "ignore", f"alias={alias!r}"

    def test_aliases_map_to_notify(self):
        """Common LLM 'forward' aliases map to notify."""
        for alias in ("forward", "alert"):
            assert EmailClassifier.normalize_intent(alias) == "notify", f"alias={alias!r}"

    def test_aliases_map_to_reply(self):
        """Common LLM 'answer' aliases map to reply."""
        for alias in ("respond", "answer"):
            assert EmailClassifier.normalize_intent(alias) == "reply", f"alias={alias!r}"

    def test_case_insensitive(self):
        """Normalization is case-insensitive."""
        assert EmailClassifier.normalize_intent("IGNORE") == "ignore"
        assert EmailClassifier.normalize_intent("Escalate") == "ask_boss"
        assert EmailClassifier.normalize_intent("SPAM") == "ignore"

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped."""
        assert EmailClassifier.normalize_intent("  escalate  ") == "ask_boss"
        assert EmailClassifier.normalize_intent(" ignore ") == "ignore"

    def test_unknown_returns_none(self):
        """Completely unrecognizable intents return None."""
        assert EmailClassifier.normalize_intent("do_a_backflip") is None
        assert EmailClassifier.normalize_intent("") is None
        assert EmailClassifier.normalize_intent("   ") is None


# ---------------------------------------------------------------------------
# build_reputation_context — static
# ---------------------------------------------------------------------------

class TestBuildReputationContext:
    """Tests for EmailClassifier.build_reputation_context()."""

    def test_unknown_sender_and_domain(self):
        """Both unknown → empty string."""
        result = EmailClassifier.build_reputation_context(
            {"known": False}, {"known": False},
        )
        assert result == ""

    def test_known_sender_only(self):
        """Known sender stats appear in output."""
        result = EmailClassifier.build_reputation_context(
            {"known": True, "total": 10, "ignore_rate": 0.8, "notify_count": 2, "reply_count": 0},
            {"known": False},
        )
        assert "10 previous emails" in result
        assert "80%" in result

    def test_known_domain_with_feedback(self):
        """Domain reputation includes positive/negative feedback."""
        result = EmailClassifier.build_reputation_context(
            {"known": False},
            {
                "known": True, "domain": "example.com", "total": 50,
                "ignore_rate": 0.9, "negative_feedback": 3, "positive_feedback": 1,
            },
        )
        assert "example.com" in result
        assert "50 total emails" in result
        assert "3 negative" in result
        assert "1 positive" in result

    def test_known_domain_no_feedback(self):
        """Domain without feedback omits the feedback clause."""
        result = EmailClassifier.build_reputation_context(
            {"known": False},
            {
                "known": True, "domain": "spam.com", "total": 20,
                "ignore_rate": 0.95, "negative_feedback": 0, "positive_feedback": 0,
            },
        )
        assert "feedback" not in result
        assert "20 total emails" in result


# ---------------------------------------------------------------------------
# build_email_signals — static
# ---------------------------------------------------------------------------

class TestBuildEmailSignals:
    """Tests for EmailClassifier.build_email_signals()."""

    def test_empty_headers(self):
        """Returns empty string when no headers."""
        assert EmailClassifier.build_email_signals({}) == ""

    def test_list_unsubscribe_detected(self):
        """List-Unsubscribe header is detected."""
        signals = EmailClassifier.build_email_signals(
            {"List-Unsubscribe": "<mailto:unsub@example.com>"},
        )
        assert "List-Unsubscribe" in signals
        assert "newsletter" in signals.lower()

    def test_precedence_bulk(self):
        """Precedence header is included."""
        signals = EmailClassifier.build_email_signals({"Precedence": "bulk"})
        assert "bulk" in signals

    def test_list_id(self):
        """List-Id header is included."""
        signals = EmailClassifier.build_email_signals({"List-Id": "marketing.example.com"})
        assert "marketing.example.com" in signals

    def test_x_mailer(self):
        """X-Mailer header is included."""
        signals = EmailClassifier.build_email_signals({"X-Mailer": "Mailchimp"})
        assert "Mailchimp" in signals

    def test_multiple_headers(self):
        """Multiple headers appear as separate lines."""
        signals = EmailClassifier.build_email_signals({
            "List-Unsubscribe": "<mailto:u@e.com>",
            "Precedence": "bulk",
            "List-Id": "mylist.example.com",
        })
        assert signals.count("\n") >= 2


# ---------------------------------------------------------------------------
# _build_reply_policy — dynamic prompt context
# ---------------------------------------------------------------------------

class TestBuildReplyPolicy:
    """Tests for EmailClassifier._build_reply_policy()."""

    def test_opt_in_with_allowed_senders(self):
        """opt_in mode lists allowed addresses."""
        c = make_classifier(allowed_senders={"alice@example.com", "bob@example.com"})
        policy = c._build_reply_policy()
        assert "Allowed reply addresses:" in policy
        assert "alice@example.com" in policy
        assert "bob@example.com" in policy

    def test_opt_in_empty_allowed_list(self):
        """opt_in mode with empty allow-list blocks all replies."""
        c = make_classifier(allowed_senders=set())
        policy = c._build_reply_policy()
        assert "No senders are allowed" in policy

    def test_opt_out_empty_blocked_list(self):
        """opt_out mode with empty blocked list allows replying to anyone."""
        c = make_classifier(filter_mode="opt_out")
        policy = c._build_reply_policy()
        assert policy == "Can reply to any sender"

    def test_opt_out_with_blocked_senders(self):
        """opt_out mode lists blocked senders."""
        c = make_classifier(
            filter_mode="opt_out",
            blocked_senders={"spam@bad.com"},
        )
        policy = c._build_reply_policy()
        assert "Can reply to any sender except:" in policy
        assert "spam@bad.com" in policy

    def test_opt_out_policy_in_classification_prompt(self):
        """Verify opt_out policy appears in the actual classification prompt."""
        c = make_classifier(filter_mode="opt_out")
        policy = c._build_reply_policy()
        # The prompt should say "Can reply to any sender", not "Allowed reply addresses:"
        assert "Can reply to any sender" in policy
        assert "Allowed reply addresses:" not in policy


# ---------------------------------------------------------------------------
# classify — LLM call with retry
# ---------------------------------------------------------------------------

class TestClassify:
    """Tests for EmailClassifier.classify() — requires async + mock LLM."""

    @pytest.mark.asyncio
    async def test_returns_classification_on_valid_json(self):
        """Returns parsed classification when LLM returns valid JSON."""
        ctx = MagicMock()
        valid_json = '{"intent": "notify", "confidence": 0.75, "reasoning": "Update", "priority": "normal"}'
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content=valid_json),
        )
        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        c = make_classifier(ctx=ctx, db=db)
        result = await c.classify("sender@example.com", "Subject", "Body")

        assert result is not None
        assert result.intent == EmailIntent.NOTIFY
        assert ctx.llm_pipeline.chat.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_when_prose_returned(self):
        """Retries with JSON reminder when first LLM response is prose."""
        ctx = MagicMock()
        valid_json = '{"intent": "ignore", "confidence": 0.9, "reasoning": "Newsletter", "priority": "low"}'
        ctx.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(content="I think this should be ignored."),
            PipelineResult(content=valid_json),
        ])
        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        c = make_classifier(ctx=ctx, db=db)
        result = await c.classify("sender@example.com", "Subject", "Body")

        assert result is not None
        assert result.intent == EmailIntent.IGNORE
        assert ctx.llm_pipeline.chat.call_count == 2

        # Second call must include the JSON reminder
        retry_messages = ctx.llm_pipeline.chat.call_args_list[1][1]["messages"]
        assert any("valid JSON only" in m.get("content", "") for m in retry_messages)

    @pytest.mark.asyncio
    async def test_returns_none_when_both_calls_fail(self):
        """Returns None when both initial call and retry return prose."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(content="I cannot decide."),
            PipelineResult(content="Still not JSON."),
        ])
        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        c = make_classifier(ctx=ctx, db=db)
        result = await c.classify("sender@example.com", "???", "Mystery.")

        assert result is None
        assert ctx.llm_pipeline.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_on_pipeline_exception(self):
        """Returns None gracefully when LLM pipeline raises an exception."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(side_effect=RuntimeError("Pipeline unavailable"))
        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        c = make_classifier(ctx=ctx, db=db)
        result = await c.classify("sender@example.com", "Subject", "Body")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_blocked_pipeline_result(self):
        """Returns None when pipeline result is blocked."""
        ctx = MagicMock()
        ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="", blocked=True),
        )
        db = MagicMock()
        db.get_sender_history = AsyncMock(return_value=[])

        c = make_classifier(ctx=ctx, db=db)
        result = await c.classify("sender@example.com", "Subject", "Body")

        assert result is None
