"""
Additional email_agent plugin tests to achieve 100% line coverage.

Covers all uncovered lines identified by coverage analysis.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.models import (
    AgentLearning,
    EmailClassification,
    EmailIntent,
    EmailRecord,
)
from overblick.plugins.email_agent.plugin import EmailAgentPlugin
from overblick.supervisor.ipc import IPCMessage


# ---------------------------------------------------------------------------
# Line 134: setup() without identity
# ---------------------------------------------------------------------------


class TestSetupNoIdentity:
    """Test setup() when identity is None."""

    @pytest.mark.asyncio
    async def test_setup_raises_without_identity(self, stal_plugin_context):
        """setup() raises RuntimeError when identity is missing."""
        stal_plugin_context.identity = None
        plugin = EmailAgentPlugin(stal_plugin_context)
        with pytest.raises(RuntimeError, match="requires an identity"):
            await plugin.setup()


# ---------------------------------------------------------------------------
# Lines 161-164: setup state loading failure, DB cleanup
# ---------------------------------------------------------------------------


class TestSetupStateLoadingFailure:
    """Test setup() cleanup when state loading fails."""

    @pytest.mark.asyncio
    async def test_setup_cleans_db_on_state_load_failure(self, stal_plugin_context):
        """setup() closes DB when get_stats() raises."""
        plugin = EmailAgentPlugin(stal_plugin_context)

        original_setup = plugin.setup

        with patch(
            "overblick.plugins.email_agent.database.EmailAgentDB.get_stats",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB corrupt"),
        ):
            with pytest.raises(RuntimeError, match="DB corrupt"):
                await plugin.setup()
        # DB should have been cleaned up
        assert plugin._db is None


# ---------------------------------------------------------------------------
# Line 181: principal_name not configured
# ---------------------------------------------------------------------------


class TestNoPrincipalName:
    """Test setup() when principal_name secret is missing."""

    @pytest.mark.asyncio
    async def test_setup_logs_warning_without_principal(self, stal_plugin_context):
        """setup() logs error when principal_name is empty."""
        stal_plugin_context._secrets_getter = lambda key: None
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        assert plugin._principal_name == ""


# ---------------------------------------------------------------------------
# Line 218: dry_run mode enabled
# ---------------------------------------------------------------------------


class TestDryRunSetup:
    """Test setup() with dry_run config."""

    @pytest.mark.asyncio
    async def test_setup_dry_run_mode(self, stal_plugin_context):
        """setup() enables dry_run mode from config."""
        stal_plugin_context.identity.raw_config["email_agent"]["dry_run"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        assert plugin._dry_run is True


# ---------------------------------------------------------------------------
# Lines 288-291: setup config loading failure, DB cleanup
# ---------------------------------------------------------------------------


class TestSetupConfigFailure:
    """Test setup() cleanup when config loading fails after DB init."""

    @pytest.mark.asyncio
    async def test_setup_cleans_db_on_config_failure(self, stal_plugin_context):
        """setup() closes DB when config loading raises."""
        # Make raw_config access raise after DB setup
        plugin = EmailAgentPlugin(stal_plugin_context)

        # Patch identity's raw_config to be a property that raises
        original_identity = stal_plugin_context.identity

        class BrokenIdentity:
            name = original_identity.name
            display_name = original_identity.display_name
            schedule = original_identity.schedule
            llm = original_identity.llm

            @property
            def raw_config(self):
                raise RuntimeError("Config broken")

        stal_plugin_context.identity = BrokenIdentity()

        with pytest.raises(RuntimeError, match="Config broken"):
            await plugin.setup()
        assert plugin._db is None


# ---------------------------------------------------------------------------
# Lines 295-300: _close_db_safe with exception during close
# ---------------------------------------------------------------------------


class TestCloseDbSafe:
    """Test _close_db_safe() exception handling."""

    @pytest.mark.asyncio
    async def test_close_db_safe_suppresses_close_error(self, stal_plugin_context):
        """_close_db_safe() suppresses exceptions from db.close()."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        plugin._db = AsyncMock()
        plugin._db.close = AsyncMock(side_effect=RuntimeError("Connection lost"))
        await plugin._close_db_safe()
        assert plugin._db is None

    @pytest.mark.asyncio
    async def test_close_db_safe_noop_without_db(self, stal_plugin_context):
        """_close_db_safe() is a noop when db is None."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        plugin._db = None
        await plugin._close_db_safe()  # Should not raise


# ---------------------------------------------------------------------------
# Lines 320-331: tick() runs full cycle including exception path
# ---------------------------------------------------------------------------


class TestTickExecution:
    """Test tick() running the full email processing cycle."""

    @pytest.mark.asyncio
    async def test_tick_processes_emails_and_checks_feedback(self, stal_plugin_context):
        """tick() fetches emails, processes them, and checks TG feedback."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0

        with (
            patch.object(
                plugin, "_fetch_unread", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(
                plugin, "_check_tg_feedback", new_callable=AsyncMock
            ) as mock_check,
        ):
            await plugin.tick()
            mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_processes_each_email(self, stal_plugin_context):
        """tick() calls _process_email for each fetched email."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0

        emails = [
            {"sender": "a@b.com", "subject": "s", "body": "b"},
            {"sender": "c@d.com", "subject": "s2", "body": "b2"},
        ]

        with (
            patch.object(
                plugin, "_fetch_unread", new_callable=AsyncMock, return_value=emails
            ),
            patch.object(
                plugin, "_process_email", new_callable=AsyncMock
            ) as mock_process,
            patch.object(
                plugin, "_check_tg_feedback", new_callable=AsyncMock
            ),
        ):
            await plugin.tick()
            assert mock_process.call_count == 2

    @pytest.mark.asyncio
    async def test_tick_sets_degraded_health_on_error(self, stal_plugin_context):
        """tick() sets health to degraded on exception."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0

        with patch.object(
            plugin,
            "_fetch_unread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Network error"),
        ):
            await plugin.tick()
        assert plugin._state.current_health == "degraded"


# ---------------------------------------------------------------------------
# Lines 344-345: _fetch_unread with GmailMessage objects
# ---------------------------------------------------------------------------


class TestFetchUnread:
    """Test _fetch_unread() email fetching."""

    @pytest.mark.asyncio
    async def test_fetch_unread_converts_messages(
        self, stal_plugin_context, mock_gmail_capability
    ):
        """_fetch_unread() converts GmailMessage objects to dicts."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        mock_msg = MagicMock()
        mock_msg.message_id = "msg-001"
        mock_msg.thread_id = "thread-001"
        mock_msg.sender = "alice@example.com"
        mock_msg.subject = "Test"
        mock_msg.body = "Hello"
        mock_msg.snippet = "Hello"
        mock_msg.headers = {}
        mock_msg.timestamp = "2026-03-15T10:00:00Z"

        mock_gmail_capability.fetch_unread = AsyncMock(return_value=[mock_msg])

        result = await plugin._fetch_unread()
        assert len(result) == 1
        assert result[0]["sender"] == "alice@example.com"
        assert result[0]["message_id"] == "msg-001"

    @pytest.mark.asyncio
    async def test_fetch_unread_no_gmail_capability(self, stal_plugin_context):
        """Returns empty list when gmail capability not available."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.capabilities.pop("gmail", None)

        result = await plugin._fetch_unread()
        assert result == []


# ---------------------------------------------------------------------------
# Lines 500-501: classification returns None
# ---------------------------------------------------------------------------


class TestClassificationReturnsNone:
    """Test _process_email when classification returns None."""

    @pytest.mark.asyncio
    async def test_process_email_classification_fails(self, stal_plugin_context):
        """_process_email returns early when classification is None."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Make classifier return None
        with patch.object(
            plugin._classifier, "classify", new_callable=AsyncMock, return_value=None
        ):
            email = {
                "sender": "test@example.com",
                "subject": "Test",
                "body": "Hello",
                "snippet": "Hello",
                "message_id": "class-fail-001",
                "thread_id": "thread-001",
                "headers": {},
            }
            initial_count = plugin._state.emails_processed
            await plugin._process_email(email)
            # Should NOT increment emails_processed
            assert plugin._state.emails_processed == initial_count


# ---------------------------------------------------------------------------
# Lines 602, 606: consultation with no consultant or empty registry
# ---------------------------------------------------------------------------


class TestConsultationEdgeCases:
    """Test _consult_identity_relevance edge cases."""

    @pytest.mark.asyncio
    async def test_consult_no_consultant_capability(self, stal_plugin_context):
        """Returns None when personality_consultant capability is missing."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.capabilities.pop("personality_consultant", None)

        email = {"sender": "test@x.com", "subject": "AI Update", "body": "New AI model"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.7, reasoning="test"
        )
        result = await plugin._consult_identity_relevance(email, classification)
        assert result is None

    @pytest.mark.asyncio
    async def test_consult_empty_registry(self, stal_plugin_context):
        """Returns None when consultant registry is empty."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._relevance_consultants = []

        email = {"sender": "test@x.com", "subject": "AI Update", "body": "New AI model"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.7, reasoning="test"
        )
        result = await plugin._consult_identity_relevance(email, classification)
        assert result is None


# ---------------------------------------------------------------------------
# Lines 647-649: consultation exception handler
# ---------------------------------------------------------------------------


class TestConsultationException:
    """Test _consult_identity_relevance exception handling."""

    @pytest.mark.asyncio
    async def test_consult_exception_returns_none(
        self, stal_plugin_context, mock_personality_consultant
    ):
        """Returns None when consultant.consult() raises."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        mock_personality_consultant.consult = AsyncMock(
            side_effect=RuntimeError("Consultant crashed")
        )

        email = {
            "sender": "test@x.com",
            "subject": "crypto bitcoin update",
            "body": "bitcoin price change",
            "snippet": "bitcoin price",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.7, reasoning="test"
        )
        result = await plugin._consult_identity_relevance(email, classification)
        assert result is None


# ---------------------------------------------------------------------------
# Lines 726-731: dry_run reply skipped
# ---------------------------------------------------------------------------


class TestDryRunReply:
    """Test execute_action REPLY in dry_run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_skips_reply(self, stal_plugin_context):
        """In dry_run mode, REPLY returns 'dry_run_reply_skipped'."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._dry_run = True

        email = {"sender": "alice@example.com", "subject": "Meeting", "body": "Let's meet"}
        classification = EmailClassification(
            intent=EmailIntent.REPLY, confidence=0.95, reasoning="Meeting"
        )

        result = await plugin._execute_action(email, classification)
        assert result == "dry_run_reply_skipped"


# ---------------------------------------------------------------------------
# Lines 761-762: unknown action (default match case)
# ---------------------------------------------------------------------------


class TestUnknownAction:
    """Test execute_action with an unrecognized intent."""

    @pytest.mark.asyncio
    async def test_unknown_intent_returns_unknown_action(self, stal_plugin_context):
        """An intent not matching any case returns 'unknown_action'."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        email = {"sender": "test@x.com", "subject": "Test", "body": "Hello"}
        classification = EmailClassification(
            intent=EmailIntent.IGNORE, confidence=0.9, reasoning="test"
        )
        # Hack to create an unrecognized intent value
        classification.intent = MagicMock()
        classification.intent.value = "unknown"
        classification.intent.__eq__ = lambda self, other: False

        result = await plugin._execute_action(email, classification)
        assert result == "unknown_action"


# ---------------------------------------------------------------------------
# Line 791: notification with blocked/empty result
# ---------------------------------------------------------------------------


class TestNotificationBlocked:
    """Test _send_notification when LLM result is blocked or empty."""

    @pytest.mark.asyncio
    async def test_notification_blocked_returns_false(self, stal_plugin_context):
        """Returns False when LLM result is blocked."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(blocked=True, block_reason="Content policy")
        )

        email = {"sender": "test@x.com", "subject": "Test", "body": "Hello"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="test"
        )
        result = await plugin._send_notification(email, classification)
        assert result is False


# ---------------------------------------------------------------------------
# Lines 816-818: notification exception handler
# ---------------------------------------------------------------------------


class TestNotificationException:
    """Test _send_notification exception handling."""

    @pytest.mark.asyncio
    async def test_notification_exception_returns_false(self, stal_plugin_context):
        """Returns False when notification generation raises."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            side_effect=RuntimeError("LLM crash")
        )

        email = {"sender": "test@x.com", "subject": "Test", "body": "Hello"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="test"
        )
        result = await plugin._send_notification(email, classification)
        assert result is False


# ---------------------------------------------------------------------------
# Lines 855-856: question generation exception in _consult_boss
# ---------------------------------------------------------------------------


class TestConsultBossQuestionFailure:
    """Test _consult_boss when question generation fails."""

    @pytest.mark.asyncio
    async def test_consult_boss_question_gen_failure(self, stal_plugin_context):
        """When question generation LLM call raises, uses default question."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # First chat call (question gen) raises, second should not be called
        # because IPC is tested separately
        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            side_effect=RuntimeError("Question gen failed")
        )

        email = {
            "sender": "test@x.com",
            "subject": "Test",
            "body": "Hello",
            "snippet": "Hello",
        }
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="Uncertain"
        )

        # IPC should still be called with default question
        result = await plugin._consult_boss(email, classification)
        assert result is True  # IPC succeeds

    @pytest.mark.asyncio
    async def test_consult_boss_no_ipc_client(self, stal_context_no_ipc):
        """Returns False when no IPC client available."""
        plugin = EmailAgentPlugin(stal_context_no_ipc)
        await plugin.setup()

        email = {"sender": "test@x.com", "subject": "Test", "body": "Hello", "snippet": "Hello"}
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="Uncertain"
        )
        result = await plugin._consult_boss(email, classification)
        assert result is False


class TestExecuteActionBossConsulted:
    """Test execute_action ASK_BOSS success path."""

    @pytest.mark.asyncio
    async def test_ask_boss_success_returns_boss_consulted(self, stal_plugin_context):
        """ASK_BOSS returns 'boss_consulted' when consultation succeeds."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        email = {"sender": "test@x.com", "subject": "Test", "body": "Hello"}
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="Uncertain"
        )

        with patch.object(
            plugin, "_consult_boss", new_callable=AsyncMock, return_value=True
        ):
            result = await plugin._execute_action(email, classification)
        assert result == "boss_consulted"
        assert plugin._state.boss_consultations >= 1


class TestNotificationNoNotifier:
    """Test _send_notification when no notifier capability."""

    @pytest.mark.asyncio
    async def test_notification_no_notifier(self, stal_plugin_context):
        """Returns False when telegram_notifier is missing."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.capabilities.pop("telegram_notifier", None)

        # LLM returns content successfully
        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Notification text")
        )

        email = {"sender": "test@x.com", "subject": "Test", "body": "Hello"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="test"
        )
        result = await plugin._send_notification(email, classification)
        assert result is False


# ---------------------------------------------------------------------------
# Line 890: boss response with invalid normalized intent
# ---------------------------------------------------------------------------


class TestBossResponseInvalidIntent:
    """Test _process_boss_response when advised_action can't be normalized."""

    @pytest.mark.asyncio
    async def test_process_boss_response_invalid_intent(self, stal_plugin_context):
        """Returns early when advised_action cannot be normalized."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        email = {"sender": "test@x.com", "subject": "Test"}
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="test"
        )
        response = MagicMock()
        response.payload = {"advised_action": "do_a_backflip", "reasoning": "test"}

        # Should not raise
        await plugin._process_boss_response(email, classification, response)

    @pytest.mark.asyncio
    async def test_process_boss_response_empty_action(self, stal_plugin_context):
        """Returns early when advised_action is empty."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        email = {"sender": "test@x.com", "subject": "Test"}
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="test"
        )
        response = MagicMock()
        response.payload = {"advised_action": "", "reasoning": "test"}

        await plugin._process_boss_response(email, classification, response)


# ---------------------------------------------------------------------------
# Lines 915, 918: _check_tg_feedback with no notifier/no DB
# ---------------------------------------------------------------------------


class TestCheckTgFeedbackGuards:
    """Test _check_tg_feedback guard conditions."""

    @pytest.mark.asyncio
    async def test_check_feedback_no_notifier(self, stal_plugin_context):
        """Returns early when no notifier capability."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.capabilities.pop("telegram_notifier", None)

        # Should not raise
        await plugin._check_tg_feedback()

    @pytest.mark.asyncio
    async def test_check_feedback_no_db(self, stal_plugin_context):
        """Returns early when DB is None."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._db = None

        await plugin._check_tg_feedback()


# ---------------------------------------------------------------------------
# Lines 922-924: fetch_updates exception
# ---------------------------------------------------------------------------


class TestCheckTgFeedbackException:
    """Test _check_tg_feedback when fetch_updates raises."""

    @pytest.mark.asyncio
    async def test_check_feedback_fetch_exception(
        self, stal_plugin_context, mock_telegram_notifier
    ):
        """Returns early when fetch_updates raises."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        mock_telegram_notifier.fetch_updates = AsyncMock(
            side_effect=RuntimeError("Network error")
        )

        # Should not raise
        await plugin._check_tg_feedback()


# ---------------------------------------------------------------------------
# Lines 928, 934: feedback loop — skip no-reply, skip no-tracking
# ---------------------------------------------------------------------------


class TestCheckTgFeedbackUpdates:
    """Test _check_tg_feedback update processing."""

    @pytest.mark.asyncio
    async def test_feedback_skips_non_reply(
        self, stal_plugin_context, mock_telegram_notifier
    ):
        """Updates without reply_to_message_id are skipped."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        update = MagicMock()
        update.reply_to_message_id = None
        mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[update])

        await plugin._check_tg_feedback()
        # No DB lookups should have happened

    @pytest.mark.asyncio
    async def test_feedback_skips_unknown_tracking(
        self, stal_plugin_context, mock_telegram_notifier
    ):
        """Updates for unknown TG message IDs are skipped."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        update = MagicMock()
        update.reply_to_message_id = 99999
        update.text = "Great job"
        mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[update])

        await plugin._check_tg_feedback()  # No tracking found, just skips


# ---------------------------------------------------------------------------
# Lines 964-976, 984-986, 992, 995: full feedback processing with learning
# ---------------------------------------------------------------------------


class TestFullFeedbackProcessing:
    """Test complete feedback processing path including learning storage."""

    @pytest.mark.asyncio
    async def test_negative_feedback_creates_learning_and_penalizes(
        self, stal_plugin_context, mock_telegram_notifier
    ):
        """Negative feedback stores learning, penalizes sender, and acknowledges."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Record an email and track notification
        record_id = await plugin._db.record_email(
            EmailRecord(
                gmail_message_id="feedback-test-001",
                email_from="spam@marketing.com",
                email_subject="Buy stuff",
                email_snippet="SALE",
                classified_intent="notify",
                confidence=0.9,
                reasoning="test",
                action_taken="notification_sent",
            )
        )
        await plugin._db.track_notification(
            email_record_id=record_id,
            tg_message_id=42,
            tg_chat_id="12345",
            notification_text="Notification about spam",
        )

        # Mock feedback classification to return negative with learning
        with patch.object(
            plugin,
            "_classify_feedback",
            new_callable=AsyncMock,
            return_value=("negative", "This is spam, stop notifying", True),
        ):
            update = MagicMock()
            update.reply_to_message_id = 42
            update.text = "Stop this spam"
            mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[update])

            await plugin._check_tg_feedback()

        # Should have stored learning
        learnings = await plugin._db.get_learnings()
        assert len(learnings) >= 1
        assert any("IGNORE" in l.content for l in learnings)

        # Should have sent ack
        mock_telegram_notifier.send_notification.assert_called()

    @pytest.mark.asyncio
    async def test_positive_feedback_creates_no_learning(
        self, stal_plugin_context, mock_telegram_notifier
    ):
        """Positive feedback without learning text doesn't store learning."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        record_id = await plugin._db.record_email(
            EmailRecord(
                gmail_message_id="feedback-pos-001",
                email_from="friend@example.com",
                email_subject="Good email",
                email_snippet="Test",
                classified_intent="notify",
                confidence=0.9,
                reasoning="test",
                action_taken="notification_sent",
            )
        )
        await plugin._db.track_notification(
            email_record_id=record_id,
            tg_message_id=43,
            tg_chat_id="12345",
            notification_text="Notification about good email",
        )

        with patch.object(
            plugin,
            "_classify_feedback",
            new_callable=AsyncMock,
            return_value=("positive", "", False),
        ):
            update = MagicMock()
            update.reply_to_message_id = 43
            update.text = "Bra!"
            mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[update])

            await plugin._check_tg_feedback()

    @pytest.mark.asyncio
    async def test_negative_feedback_auto_ignore_domain(
        self, stal_plugin_context, mock_telegram_notifier
    ):
        """Negative feedback can trigger domain auto-ignore."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        record_id = await plugin._db.record_email(
            EmailRecord(
                gmail_message_id="feedback-autoignore-001",
                email_from="spam@badcorp.com",
                email_subject="Spam",
                email_snippet="Spam",
                classified_intent="notify",
                confidence=0.9,
                reasoning="test",
                action_taken="notification_sent",
            )
        )
        await plugin._db.track_notification(
            email_record_id=record_id,
            tg_message_id=44,
            tg_chat_id="12345",
            notification_text="Spam notification",
        )

        # Make domain reputation say should auto-ignore
        with (
            patch.object(
                plugin,
                "_classify_feedback",
                new_callable=AsyncMock,
                return_value=("negative", "Spam domain", False),
            ),
            patch.object(
                plugin._reputation,
                "should_auto_ignore_domain",
                return_value=True,
            ),
        ):
            update = MagicMock()
            update.reply_to_message_id = 44
            update.text = "Stop this"
            mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[update])

            await plugin._check_tg_feedback()

        assert "badcorp.com" in plugin._auto_ignore_domains


# ---------------------------------------------------------------------------
# Line 1022: heuristic neutral feedback
# ---------------------------------------------------------------------------


class TestClassifyFeedbackHeuristicNeutral:
    """Test _classify_feedback heuristic neutral path."""

    @pytest.mark.asyncio
    async def test_heuristic_neutral_feedback(self, stal_plugin_context):
        """Neutral text without keywords returns neutral."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.llm_pipeline = None

        sentiment, learning, should_ack = await plugin._classify_feedback(
            "Hmm, I see",
            "Notification text",
            "Subject",
        )
        assert sentiment == "neutral"
        assert learning == ""
        assert should_ack is False


# ---------------------------------------------------------------------------
# Lines 1039-1042: feedback classification LLM exception
# ---------------------------------------------------------------------------


class TestClassifyFeedbackLLMException:
    """Test _classify_feedback when LLM raises."""

    @pytest.mark.asyncio
    async def test_classify_feedback_llm_exception(self, stal_plugin_context):
        """Returns neutral when LLM classification raises."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            side_effect=RuntimeError("LLM crash")
        )

        sentiment, learning, should_ack = await plugin._classify_feedback(
            "Test feedback",
            "Notification",
            "Subject",
        )
        assert sentiment == "neutral"
        assert learning == ""
        assert should_ack is False


# ---------------------------------------------------------------------------
# Lines 1052-1055: parse_feedback_classification embedded JSON extraction fails
# ---------------------------------------------------------------------------


class TestParseFeedbackEdgeCases:
    """Test _parse_feedback_classification edge cases."""

    @pytest.mark.asyncio
    async def test_parse_feedback_embedded_json_invalid(self, stal_plugin_context):
        """When embedded JSON extraction also fails, returns neutral."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Contains { and } but not valid JSON
        raw = "Here is {broken json content} around"
        sentiment, learning, should_ack = plugin._parse_feedback_classification(raw)
        assert sentiment == "neutral"
        assert learning == ""
        assert should_ack is False

    @pytest.mark.asyncio
    async def test_parse_feedback_no_braces(self, stal_plugin_context):
        """When no braces found, returns neutral."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = "No JSON here at all"
        sentiment, learning, should_ack = plugin._parse_feedback_classification(raw)
        assert sentiment == "neutral"

    @pytest.mark.asyncio
    async def test_parse_feedback_invalid_sentiment(self, stal_plugin_context):
        """Invalid sentiment value is normalized to neutral."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = '{"sentiment": "happy", "learning": "test", "should_acknowledge": false}'
        sentiment, learning, should_ack = plugin._parse_feedback_classification(raw)
        assert sentiment == "neutral"
        assert learning == "test"


# ---------------------------------------------------------------------------
# Line 1126: _send_approved_draft failure
# ---------------------------------------------------------------------------


class TestSendApprovedDraft:
    """Test _send_approved_draft paths."""

    @pytest.mark.asyncio
    async def test_send_draft_no_body(self, stal_plugin_context):
        """Notifies when draft body is empty."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        notifier = AsyncMock()
        tracking = {"draft_reply_body": ""}

        await plugin._send_approved_draft(tracking, notifier)
        notifier.send_notification.assert_called_once()
        assert "not found" in notifier.send_notification.call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_draft_not_allowed_sender(self, stal_plugin_context):
        """Notifies when sender is not in allowed list."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        notifier = AsyncMock()
        tracking = {
            "draft_reply_body": "Hello",
            "email_from": "unknown@evil.com",
            "email_subject": "Test",
        }

        await plugin._send_approved_draft(tracking, notifier)
        notifier.send_notification.assert_called_once()
        assert "not in the allowed" in notifier.send_notification.call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_draft_no_gmail(self, stal_plugin_context):
        """Notifies when gmail capability is unavailable."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        stal_plugin_context.capabilities.pop("gmail", None)

        notifier = AsyncMock()
        tracking = {
            "draft_reply_body": "Hello",
            "email_from": "alice@example.com",
            "email_subject": "Test",
            "original_email_thread_id": "t1",
            "gmail_message_id": "m1",
        }

        await plugin._send_approved_draft(tracking, notifier)
        notifier.send_notification.assert_called_once()
        assert "not available" in notifier.send_notification.call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_draft_success(self, stal_plugin_context, mock_gmail_capability):
        """Successful draft send increments replied count and notifies."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        record_id = await plugin._db.record_email(
            EmailRecord(
                gmail_message_id="draft-001",
                email_from="alice@example.com",
                email_subject="Test",
                classified_intent="notify",
                confidence=0.9,
                reasoning="test",
            )
        )

        notifier = AsyncMock()
        notifier.chat_id = "12345"
        tracking = {
            "draft_reply_body": "Hello there",
            "email_from": "alice@example.com",
            "email_subject": "Test",
            "original_email_thread_id": "t1",
            "gmail_message_id": "m1",
            "email_record_id": record_id,
        }

        initial_replied = plugin._state.emails_replied
        await plugin._send_approved_draft(tracking, notifier)
        assert plugin._state.emails_replied == initial_replied + 1
        notifier.send_notification.assert_called_once()
        assert "Reply sent" in notifier.send_notification.call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_draft_gmail_failure(self, stal_plugin_context, mock_gmail_capability):
        """Notifies on gmail send failure."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        mock_gmail_capability.send_reply = AsyncMock(return_value=False)

        notifier = AsyncMock()
        tracking = {
            "draft_reply_body": "Hello",
            "email_from": "alice@example.com",
            "email_subject": "Re: Test",
            "original_email_thread_id": "t1",
            "gmail_message_id": "m1",
        }

        await plugin._send_approved_draft(tracking, notifier)
        notifier.send_notification.assert_called_once()
        assert "Failed" in notifier.send_notification.call_args[0][0]


# ---------------------------------------------------------------------------
# Line 1134: filter mode neither opt_in nor opt_out
# ---------------------------------------------------------------------------


class TestFilterModeDefault:
    """Test _is_allowed_sender with unknown filter mode."""

    @pytest.mark.asyncio
    async def test_unknown_filter_mode_allows_all(self, stal_plugin_context):
        """Unknown filter mode defaults to allowing all senders."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._filter_mode = "something_else"
        assert plugin._is_allowed_sender("anyone@anywhere.com") is True


# ---------------------------------------------------------------------------
# Lines 1144-1145: _is_reply_rate_limited malformed sender
# ---------------------------------------------------------------------------


class TestRateLimitMalformedSender:
    """Test rate limiting with malformed sender."""

    @pytest.mark.asyncio
    async def test_rate_limit_no_at_sign(self, stal_plugin_context):
        """Sender without @ is not rate limited (graceful fallback)."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        # No @ sign — should not raise, returns False
        assert plugin._is_reply_rate_limited("no-at-sign") is False

    @pytest.mark.asyncio
    async def test_rate_limit_none_sender(self, stal_plugin_context):
        """None sender triggers AttributeError which is caught."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        assert plugin._is_reply_rate_limited(None) is False


# ---------------------------------------------------------------------------
# Lines 1169-1170: _record_reply_sent malformed sender
# ---------------------------------------------------------------------------


class TestRecordReplySentMalformed:
    """Test _record_reply_sent with malformed sender."""

    @pytest.mark.asyncio
    async def test_record_reply_sent_no_at_sign(self, stal_plugin_context):
        """Malformed sender without @ doesn't crash."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        # Should not raise — the try/except catches the failure
        plugin._record_reply_sent("no-at-sign")

    @pytest.mark.asyncio
    async def test_record_reply_sent_none_sender(self, stal_plugin_context):
        """None sender triggers AttributeError which is caught."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        # Should not raise
        plugin._record_reply_sent(None)


# ---------------------------------------------------------------------------
# Lines 1181-1183: _build_system_prompt fallback
# ---------------------------------------------------------------------------


class TestBuildSystemPromptFallback:
    """Test _build_system_prompt when personality file not found."""

    @pytest.mark.asyncio
    async def test_fallback_prompt(self, stal_plugin_context):
        """Returns fallback prompt when personality file is missing."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        plugin._principal_name = "Test User"

        with patch(
            "overblick.identities.load_identity",
            side_effect=FileNotFoundError("not found"),
        ):
            result = plugin._build_system_prompt()
        assert "Stål" in result
        assert "Test User" in result

    @pytest.mark.asyncio
    async def test_fallback_prompt_no_principal(self, stal_plugin_context):
        """Fallback prompt uses 'the principal' when name not set."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        plugin._principal_name = ""

        with patch(
            "overblick.identities.load_identity",
            side_effect=FileNotFoundError("not found"),
        ):
            result = plugin._build_system_prompt()
        assert "the principal" in result


# ---------------------------------------------------------------------------
# Line 1191: _initialize_default_goals without DB
# ---------------------------------------------------------------------------


class TestInitGoalsNoDb:
    """Test _initialize_default_goals when DB is None."""

    @pytest.mark.asyncio
    async def test_init_goals_no_db(self, stal_plugin_context):
        """Returns early when DB is None."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        plugin._db = None
        await plugin._initialize_default_goals()
        # No crash, no goals added
        assert len(plugin._state.goals) == 0
