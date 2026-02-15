"""
Multilingual scenario tests for the email agent.

Each scenario simulates a complete email -> classification -> action flow
with mock LLM responses. Tests verify the full pipeline including
language detection and correct action routing.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.models import EmailClassification, EmailIntent
from overblick.plugins.email_agent.plugin import EmailAgentPlugin
from tests.plugins.email_agent.conftest import make_email


class TestEnglishScenarios:
    """English email scenarios."""

    @pytest.mark.asyncio
    async def test_english_meeting_request(self, stal_plugin_context, mock_gmail_capability):
        """English meeting request -> REPLY with English response."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Configure LLM to return REPLY classification then English reply
        responses = [
            PipelineResult(content='{"intent": "reply", "confidence": 0.95, "reasoning": "Meeting request from colleague", "priority": "normal"}'),
            PipelineResult(content="Dear colleague,\n\nThank you for reaching out. I will check the calendar for Tuesday and come back to you with available time slots.\n\nBest regards,\nJens"),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="test@example.com",
            subject="Meeting next Tuesday?",
            body="Hi Jens, can we schedule a meeting for next Tuesday to discuss the Q1 results?",
        )

        await plugin._process_email(email)

        # Verify classification was stored
        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "reply"
        assert records[0].confidence == 0.95

        # Verify reply was sent via Gmail capability
        mock_gmail_capability.send_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_newsletter_ignored(self, stal_plugin_context):
        """Newsletter/spam -> IGNORE, no action taken."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "ignore", "confidence": 0.98, "reasoning": "Marketing newsletter", "priority": "low"}'
        ))

        email = make_email(
            sender="test@example.com",
            subject="50% off all products!",
            body="Don't miss our biggest sale of the year!",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "ignore"

        # No Gmail send_reply call (ignored email)
        gmail_cap = stal_plugin_context.get_capability("gmail")
        gmail_cap.send_reply.assert_not_called()


class TestSwedishScenarios:
    """Swedish email scenarios."""

    @pytest.mark.asyncio
    async def test_swedish_project_update(self, stal_plugin_context, mock_gmail_capability):
        """Swedish project update -> REPLY in Swedish."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        responses = [
            PipelineResult(content='{"intent": "reply", "confidence": 0.92, "reasoning": "Project status request from colleague", "priority": "normal"}'),
            PipelineResult(content="Hej,\n\nTack for ditt meddelande. Jag aterkommer med en statusuppdatering om Volvo-projektet senast fredag.\n\nMed vanlig halsning,\nJens"),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="test@example.com",
            subject="Uppdatering om projektet",
            body="Hej Jens, kan du skicka en statusuppdatering om Volvo-projektet? Behover det till fredagsmotet.",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "reply"

        # Reply sent via Gmail
        mock_gmail_capability.send_reply.assert_called_once()


class TestGermanScenarios:
    """German email scenarios."""

    @pytest.mark.asyncio
    async def test_german_invoice_question(self, stal_plugin_context, mock_telegram_notifier):
        """German invoice question -> NOTIFY (financial matters need human review)."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        responses = [
            PipelineResult(content='{"intent": "notify", "confidence": 0.88, "reasoning": "Financial question about invoice — needs Jens review", "priority": "high"}'),
            PipelineResult(content="Invoice question from buchhaltung@wirelesscar.com about Rechnung Nr. 2024-0847. Financial matter requiring your review."),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="test@example.com",
            subject="Rechnung Nr. 2024-0847",
            body="Sehr geehrter Herr Abrahamsson, wir haben eine Frage bezuglich Ihrer Rechnung Nr. 2024-0847.",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "notify"

        # Telegram notification sent
        mock_telegram_notifier.send_notification_tracked.assert_called_once()


class TestFrenchScenarios:
    """French email scenarios."""

    @pytest.mark.asyncio
    async def test_french_partnership_inquiry(self, stal_plugin_context, mock_gmail_capability):
        """French partnership inquiry -> REPLY in French."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        responses = [
            PipelineResult(content='{"intent": "reply", "confidence": 0.90, "reasoning": "Partnership inquiry — professional response needed", "priority": "normal"}'),
            PipelineResult(content="Bonjour,\n\nJe vous remercie pour votre message. La proposition de partenariat est tres interessante. Je reviens vers vous dans les plus brefs delais.\n\nCordialement,\nJens Abrahamsson"),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="test@example.com",
            subject="Proposition de partenariat",
            body="Bonjour M. Abrahamsson, nous souhaiterions discuter d'un partenariat potentiel entre nos entreprises.",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "reply"

        mock_gmail_capability.send_reply.assert_called_once()


class TestUncertainScenarios:
    """Scenarios where the agent is uncertain and asks the boss."""

    @pytest.mark.asyncio
    async def test_low_confidence_asks_boss(self, stal_plugin_context, mock_ipc_client_email):
        """Low confidence classification -> ASK_BOSS via IPC."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # First call: classification with LOW confidence (below 0.7 threshold)
        # Second call: question generation for boss
        responses = [
            PipelineResult(content='{"intent": "reply", "confidence": 0.45, "reasoning": "Ambiguous — could be important or spam", "priority": "normal"}'),
            PipelineResult(content="I'm uncertain about this email regarding restructuring. Should I reply or just notify Jens?"),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="test@example.com",
            subject="Confidential: Restructuring",
            body="Jens, we need to discuss the upcoming organizational changes privately.",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        # Should be overridden to ask_boss due to low confidence
        assert records[0].classified_intent == "ask_boss"

        # IPC message should have been sent
        mock_ipc_client_email.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_ambiguous_without_ipc_fails_gracefully(self, stal_context_no_ipc):
        """Low confidence without IPC client fails gracefully."""
        plugin = EmailAgentPlugin(stal_context_no_ipc)
        await plugin.setup()

        stal_context_no_ipc.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "reply", "confidence": 0.3, "reasoning": "Very uncertain", "priority": "normal"}'
        ))

        email = make_email(
            sender="test@example.com",
            subject="Strange email",
            body="Something weird is going on.",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "ask_boss"
        assert records[0].action_taken == "boss_consultation_failed"


class TestSenderFiltering:
    """Test that sender filtering works correctly in the processing pipeline."""

    @pytest.mark.asyncio
    async def test_allowed_sender_check(self, stal_plugin_context):
        """_is_allowed_sender works for opt-in mode."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._is_allowed_sender("random@nobody.com") is False
        assert plugin._is_allowed_sender("jens.abrahamsson@wirelesscar.com") is True
        assert plugin._is_allowed_sender("test@example.com") is True


class TestNotifyAllSenders:
    """Test that NOTIFY works for all senders regardless of filter mode."""

    @pytest.mark.asyncio
    async def test_unknown_sender_notify_works(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """Emails from unknown senders classified as NOTIFY still get sent."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # LLM: classify as NOTIFY, then generate notification
        responses = [
            PipelineResult(content='{"intent": "notify", "confidence": 0.85, "reasoning": "Important financial info", "priority": "high"}'),
            PipelineResult(content="Important financial update from unknown sender."),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="finance@unknowncorp.com",  # NOT in allowed_senders
            subject="Q4 Financial Report",
            body="Please review the attached Q4 financials.",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "notify"
        # Notification should have been sent
        mock_telegram_notifier.send_notification_tracked.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_sender_reply_falls_back_to_notify(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """Emails from unknown senders classified as REPLY fall back to NOTIFY."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # LLM: classify as REPLY (but sender not allowed), then notification fallback
        responses = [
            PipelineResult(content='{"intent": "reply", "confidence": 0.9, "reasoning": "Meeting request", "priority": "normal"}'),
            PipelineResult(content="Meeting request from unknown sender — please review."),
        ]
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=responses)

        email = make_email(
            sender="unknown@randomcorp.com",  # NOT in allowed_senders
            subject="Partnership meeting",
            body="Would you like to discuss a potential partnership?",
        )

        await plugin._process_email(email)

        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "reply"
        # Should have fallen back to notify
        assert "notify_fallback" in records[0].action_taken
        mock_telegram_notifier.send_notification_tracked.assert_called_once()
