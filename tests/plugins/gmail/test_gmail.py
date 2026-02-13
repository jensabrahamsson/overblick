"""
Gmail plugin tests.

Tests cover:
- Plugin lifecycle (setup, teardown)
- Email processing flow
- Draft mode (compose but don't send)
- Rate limiting per recipient
- Sender whitelist filtering
- LLM response generation with boundary markers
- Draft approval workflow (boss agent interface)
- Error handling
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blick.core.llm.pipeline import PipelineResult
from blick.plugins.gmail.plugin import (
    EmailAction,
    EmailDraft,
    EmailMessage,
    GmailPlugin,
    RecipientRateLimit,
)
from tests.plugins.gmail.conftest import make_email


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestEmailMessage:
    """Test EmailMessage dataclass."""

    def test_basic_construction(self):
        msg = make_email()
        assert msg.message_id == "msg-001"
        assert msg.thread_id == "thread-001"
        assert msg.subject == "Test Subject"
        assert msg.sender == "friend@example.com"

    def test_is_reply_detection(self):
        reply = make_email(subject="Re: Original Thread")
        assert reply.is_reply

    def test_not_reply(self):
        original = make_email(subject="New Discussion")
        assert not original.is_reply

    def test_is_reply_case_insensitive(self):
        reply = make_email(subject="RE: Urgent Matter")
        assert reply.is_reply


class TestEmailDraft:
    """Test EmailDraft dataclass."""

    def test_draft_defaults(self):
        draft = EmailDraft(to="user@example.com", subject="Test", body="Body")
        assert not draft.approved
        assert not draft.sent
        assert draft.thread_id is None
        assert draft.created_at > 0


class TestRecipientRateLimit:
    """Test per-recipient rate limiting."""

    def test_allows_within_limits(self):
        rl = RecipientRateLimit(email="user@test.com", max_per_hour=5, max_per_day=20)
        for _ in range(4):
            assert rl.is_allowed()
            rl.record()

    def test_blocks_when_hourly_exceeded(self):
        rl = RecipientRateLimit(email="user@test.com", max_per_hour=2, max_per_day=100)
        rl.record()
        rl.record()
        assert not rl.is_allowed()

    def test_blocks_when_daily_exceeded(self):
        rl = RecipientRateLimit(email="user@test.com", max_per_hour=100, max_per_day=3)
        rl.record()
        rl.record()
        rl.record()
        assert not rl.is_allowed()

    def test_prunes_old_timestamps(self):
        rl = RecipientRateLimit(email="user@test.com", max_per_hour=2, max_per_day=100)
        rl.send_timestamps = [time.time() - 90000, time.time() - 90000]  # > 24h ago
        assert rl.is_allowed()

    def test_record_appends_timestamp(self):
        rl = RecipientRateLimit(email="user@test.com")
        assert len(rl.send_timestamps) == 0
        rl.record()
        assert len(rl.send_timestamps) == 1


# ---------------------------------------------------------------------------
# Plugin lifecycle tests
# ---------------------------------------------------------------------------

class TestGmailLifecycle:
    """Test plugin setup and teardown."""

    @pytest.mark.asyncio
    async def test_setup_loads_config(self, gmail_plugin):
        assert gmail_plugin._draft_mode is True
        assert gmail_plugin._check_interval == 300

    @pytest.mark.asyncio
    async def test_setup_loads_allowed_senders(self, gmail_plugin):
        assert "friend@example.com" in gmail_plugin._allowed_senders
        assert "boss@example.com" in gmail_plugin._allowed_senders

    @pytest.mark.asyncio
    async def test_setup_builds_system_prompt(self, gmail_plugin):
        assert gmail_plugin._system_prompt
        assert len(gmail_plugin._system_prompt) > 20

    @pytest.mark.asyncio
    async def test_setup_without_credentials_raises(self, gmail_context):
        gmail_context._secrets_getter = lambda key: None
        plugin = GmailPlugin(gmail_context)
        with pytest.raises(RuntimeError, match="Missing gmail credentials"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_without_email_address_raises(self, gmail_context):
        gmail_context._secrets_getter = lambda key: {
            "gmail_oauth_credentials": '{"test": true}',
        }.get(key)
        plugin = GmailPlugin(gmail_context)
        with pytest.raises(RuntimeError, match="Missing gmail_email_address"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_logs_audit(self, gmail_plugin, mock_audit_log):
        mock_audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_get_status(self, gmail_plugin):
        status = gmail_plugin.get_status()
        assert status["plugin"] == "gmail"
        assert status["draft_mode"] is True
        assert "emails_read" in status
        assert "errors" in status


# ---------------------------------------------------------------------------
# Email processing tests
# ---------------------------------------------------------------------------

class TestEmailProcessing:
    """Test email processing flow."""

    @pytest.mark.asyncio
    async def test_process_new_email_creates_draft(self, gmail_plugin):
        email = make_email(sender="friend@example.com")
        await gmail_plugin._process_email(email)
        assert gmail_plugin._emails_read == 1
        assert len(gmail_plugin.get_pending_drafts()) == 1

    @pytest.mark.asyncio
    async def test_process_reply_email(self, gmail_plugin):
        email = make_email(subject="Re: Previous thread", sender="friend@example.com")
        await gmail_plugin._process_email(email)
        drafts = gmail_plugin.get_pending_drafts()
        assert len(drafts) == 1
        assert drafts[0].in_reply_to == "msg-001"

    @pytest.mark.asyncio
    async def test_new_email_adds_re_prefix(self, gmail_plugin):
        email = make_email(subject="Original Subject", sender="friend@example.com")
        await gmail_plugin._process_email(email)
        draft = gmail_plugin.get_pending_drafts()[0]
        assert draft.subject.startswith("Re:")

    @pytest.mark.asyncio
    async def test_reply_email_keeps_subject(self, gmail_plugin):
        email = make_email(subject="Re: Ongoing", sender="friend@example.com")
        await gmail_plugin._process_email(email)
        draft = gmail_plugin.get_pending_drafts()[0]
        assert draft.subject == "Re: Ongoing"

    @pytest.mark.asyncio
    async def test_non_whitelisted_sender_skipped(self, gmail_plugin):
        email = make_email(sender="stranger@evil.com")
        await gmail_plugin._process_email(email)
        assert len(gmail_plugin.get_pending_drafts()) == 0

    @pytest.mark.asyncio
    async def test_whitelisted_sender_processed(self, gmail_plugin):
        email = make_email(sender="boss@example.com")
        await gmail_plugin._process_email(email)
        assert len(gmail_plugin.get_pending_drafts()) == 1


# ---------------------------------------------------------------------------
# Draft mode tests
# ---------------------------------------------------------------------------

class TestDraftMode:
    """Test draft mode behavior."""

    @pytest.mark.asyncio
    async def test_draft_mode_queues_instead_of_sending(self, gmail_plugin):
        assert gmail_plugin._draft_mode is True
        email = make_email(sender="friend@example.com")
        await gmail_plugin._process_email(email)
        drafts = gmail_plugin.get_pending_drafts()
        assert len(drafts) == 1
        assert not drafts[0].sent

    @pytest.mark.asyncio
    async def test_non_draft_mode_sends_directly(self, gmail_plugin):
        gmail_plugin._draft_mode = False
        email = make_email(sender="friend@example.com")
        with patch.object(gmail_plugin, "_send_email", new_callable=AsyncMock, return_value=True):
            await gmail_plugin._process_email(email)
        assert gmail_plugin._emails_replied == 1

    @pytest.mark.asyncio
    async def test_draft_approval(self, gmail_plugin):
        email = make_email(sender="friend@example.com")
        await gmail_plugin._process_email(email)
        draft = gmail_plugin.approve_draft(0)
        assert draft is not None
        assert draft.approved is True

    @pytest.mark.asyncio
    async def test_draft_approval_invalid_index(self, gmail_plugin):
        result = gmail_plugin.approve_draft(99)
        assert result is None

    @pytest.mark.asyncio
    async def test_pending_drafts_excludes_sent(self, gmail_plugin):
        email1 = make_email(sender="friend@example.com", message_id="msg-1")
        email2 = make_email(sender="boss@example.com", message_id="msg-2")
        await gmail_plugin._process_email(email1)
        await gmail_plugin._process_email(email2)
        # Mark first as sent
        gmail_plugin._drafts[0].sent = True
        pending = gmail_plugin.get_pending_drafts()
        assert len(pending) == 1


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------

class TestGmailRateLimiting:
    """Test per-recipient rate limiting in email processing."""

    @pytest.mark.asyncio
    async def test_rate_limited_sender_skipped(self, gmail_plugin):
        # Exhaust the rate limit for this sender
        rl = gmail_plugin._get_rate_limiter("friend@example.com")
        for _ in range(25):
            rl.record()

        email = make_email(sender="friend@example.com")
        await gmail_plugin._process_email(email)
        # Should not create a draft because rate limited
        assert len(gmail_plugin.get_pending_drafts()) == 0

    @pytest.mark.asyncio
    async def test_rate_limiter_per_recipient(self, gmail_plugin):
        rl1 = gmail_plugin._get_rate_limiter("user1@test.com")
        rl2 = gmail_plugin._get_rate_limiter("user2@test.com")
        assert rl1 is not rl2
        assert rl1.email == "user1@test.com"


# ---------------------------------------------------------------------------
# LLM response generation tests
# ---------------------------------------------------------------------------

class TestResponseGeneration:
    """Test LLM-powered response generation."""

    @pytest.mark.asyncio
    async def test_generates_response_via_pipeline(self, gmail_plugin):
        email = make_email(sender="friend@example.com")
        response = await gmail_plugin._generate_response(email, is_reply=False)
        assert response is not None
        assert len(response) > 0
        gmail_plugin.ctx.llm_pipeline.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_response_uses_boundary_markers(self, gmail_plugin):
        email = make_email(sender="friend@example.com", body="Please respond")
        await gmail_plugin._generate_response(email, is_reply=False)
        call_args = gmail_plugin.ctx.llm_pipeline.chat.call_args
        messages = call_args[1].get("messages") or call_args[0][0]
        user_msg = [m for m in messages if m["role"] == "user"][0]["content"]
        assert "<<<EXTERNAL_EMAIL_BODY_START>>>" in user_msg
        assert "<<<EXTERNAL_EMAIL_SENDER_START>>>" in user_msg

    @pytest.mark.asyncio
    async def test_blocked_response_returns_none(self, gmail_plugin):
        gmail_plugin.ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(blocked=True, block_reason="Unsafe")
        )
        email = make_email(sender="friend@example.com")
        response = await gmail_plugin._generate_response(email, is_reply=False)
        assert response is None

    @pytest.mark.asyncio
    async def test_no_pipeline_returns_none(self, gmail_plugin):
        gmail_plugin.ctx.llm_pipeline = None
        email = make_email(sender="friend@example.com")
        response = await gmail_plugin._generate_response(email, is_reply=False)
        assert response is None

    @pytest.mark.asyncio
    async def test_response_truncated_to_max_length(self, gmail_plugin):
        long_text = "A" * 10000
        gmail_plugin.ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content=long_text)
        )
        email = make_email(sender="friend@example.com")
        response = await gmail_plugin._generate_response(email, is_reply=False)
        assert len(response) <= gmail_plugin._max_body_length


# ---------------------------------------------------------------------------
# Tick behavior tests
# ---------------------------------------------------------------------------

class TestTickBehavior:
    """Test the periodic tick cycle."""

    @pytest.mark.asyncio
    async def test_tick_respects_check_interval(self, gmail_plugin):
        gmail_plugin._last_check = time.time()  # Just checked
        with patch.object(gmail_plugin, "_fetch_unread", new_callable=AsyncMock) as mock_fetch:
            await gmail_plugin.tick()
            mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_fetches_when_interval_passed(self, gmail_plugin):
        gmail_plugin._last_check = time.time() - 600  # 10 min ago
        with patch.object(gmail_plugin, "_fetch_unread", new_callable=AsyncMock, return_value=[]):
            await gmail_plugin.tick()
            # _last_check should be updated

    @pytest.mark.asyncio
    async def test_tick_error_increments_counter(self, gmail_plugin):
        gmail_plugin._last_check = 0  # Force check
        with patch.object(gmail_plugin, "_fetch_unread", new_callable=AsyncMock, side_effect=Exception("API error")):
            await gmail_plugin.tick()
        assert gmail_plugin._errors == 1


# ---------------------------------------------------------------------------
# Teardown tests
# ---------------------------------------------------------------------------

class TestTeardown:
    """Test plugin teardown."""

    @pytest.mark.asyncio
    async def test_teardown_warns_on_unsent_drafts(self, gmail_plugin, caplog):
        import logging
        email = make_email(sender="friend@example.com")
        await gmail_plugin._process_email(email)
        with caplog.at_level(logging.WARNING):
            await gmail_plugin.teardown()
        assert any("unsent drafts" in r.message for r in caplog.records)
