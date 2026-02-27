"""
Tests for the email_agent plugin — lifecycle, classification, database, IPC,
sender reputation, cross-identity consultation, and enhanced feedback.
"""

import email.utils
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.classifier import EmailClassifier
from overblick.plugins.email_agent.database import EmailAgentDB, MIGRATIONS
from overblick.plugins.email_agent.models import (
    AgentGoal,
    AgentLearning,
    AgentState,
    EmailClassification,
    EmailIntent,
    EmailRecord,
    SenderProfile,
)
from overblick.capabilities.consulting.personality_consultant import (
    PersonalityConsultantCapability,
)
from overblick.plugins.email_agent.plugin import EmailAgentPlugin
from overblick.plugins.email_agent.reputation import ReputationManager
from overblick.plugins.email_agent.prompts import (
    boss_consultation_prompt,
    classification_prompt,
    feedback_classification_prompt,
    notification_prompt,
    reply_prompt,
    reply_prompt_with_research,
)
from overblick.supervisor.ipc import IPCMessage


class TestEmailAgentSetup:
    """Test plugin initialization and configuration."""

    @pytest.mark.asyncio
    async def test_setup_creates_database(self, stal_plugin_context):
        """setup() creates the SQLite database and applies migrations."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        db_path = stal_plugin_context.data_dir / "email_agent.db"
        assert db_path.exists()

    @pytest.mark.asyncio
    async def test_setup_initializes_default_goals(self, stal_plugin_context):
        """setup() creates default goals when none exist."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert len(plugin._state.goals) == 3
        assert any("classify" in g.description.lower() for g in plugin._state.goals)

    @pytest.mark.asyncio
    async def test_setup_loads_filter_config(self, stal_plugin_context):
        """setup() reads sender filtering config from identity."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._filter_mode == "opt_in"
        assert "jens@example.com" in plugin._allowed_senders

    @pytest.mark.asyncio
    async def test_setup_builds_system_prompt(self, stal_plugin_context):
        """setup() builds a system prompt from Stål's personality."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._system_prompt  # Non-empty


class TestEmailAgentFiltering:
    """Test sender filtering logic."""

    @pytest.mark.asyncio
    async def test_opt_in_allows_whitelisted(self, stal_plugin_context):
        """Opt-in mode allows senders in the allowed list."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._is_allowed_sender("jens@example.com") is True

    @pytest.mark.asyncio
    async def test_opt_in_blocks_unknown(self, stal_plugin_context):
        """Opt-in mode blocks senders not in the allowed list."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._is_allowed_sender("random@example.com") is False

    @pytest.mark.asyncio
    async def test_opt_out_allows_unlisted(self, stal_plugin_context):
        """Opt-out mode allows senders not in the blocked list."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        plugin._filter_mode = "opt_out"
        plugin._blocked_senders = {"spam@evil.com"}

        assert plugin._is_allowed_sender("friend@example.com") is True

    @pytest.mark.asyncio
    async def test_opt_out_blocks_listed(self, stal_plugin_context):
        """Opt-out mode blocks senders in the blocked list."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        plugin._filter_mode = "opt_out"
        plugin._blocked_senders = {"spam@evil.com"}

        assert plugin._is_allowed_sender("spam@evil.com") is False


class TestEmailClassification:
    """Test the classification parsing logic."""

    @pytest.mark.asyncio
    async def test_parse_valid_classification(self, stal_plugin_context):
        """Parses valid JSON classification from LLM."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = '{"intent": "reply", "confidence": 0.95, "reasoning": "Meeting request", "priority": "normal"}'
        result = plugin._classifier._parse(raw)

        assert result is not None
        assert result.intent == EmailIntent.REPLY
        assert result.confidence == 0.95
        assert result.priority == "normal"

    @pytest.mark.asyncio
    async def test_parse_classification_with_surrounding_text(self, stal_plugin_context):
        """Extracts JSON from surrounding text."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = 'Here is my analysis:\n{"intent": "ignore", "confidence": 0.85, "reasoning": "Newsletter", "priority": "low"}\nDone.'
        result = plugin._classifier._parse(raw)

        assert result is not None
        assert result.intent == EmailIntent.IGNORE

    @pytest.mark.asyncio
    async def test_parse_invalid_json_returns_none(self, stal_plugin_context):
        """Returns None for unparseable content."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        result = plugin._classifier._parse("This is not JSON at all")
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_classification_ask_boss(self, stal_plugin_context):
        """Parses ask_boss intent correctly."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = '{"intent": "ask_boss", "confidence": 0.4, "reasoning": "Uncertain", "priority": "high"}'
        result = plugin._classifier._parse(raw)

        assert result is not None
        assert result.intent == EmailIntent.ASK_BOSS
        assert result.confidence == 0.4

    @pytest.mark.asyncio
    async def test_classify_email_retries_on_prose_response(self, stal_plugin_context):
        """When LLM returns prose instead of JSON, classify() retries.

        When _parse() returns None (prose, not JSON), the classifier sends
        a follow-up turn with an explicit JSON reminder. The retry must succeed
        if the second call returns valid JSON.
        """
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        valid_json = '{"intent": "notify", "confidence": 0.8, "reasoning": "Newsletter", "priority": "low"}'

        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(content="I think this email should be handled as a notification."),
            PipelineResult(content=valid_json),
        ])

        result = await plugin._classifier.classify(
            sender="newsletter@example.com",
            subject="Weekly digest",
            body="Here is your weekly update...",
        )

        assert result is not None
        assert result.intent == EmailIntent.NOTIFY
        assert result.confidence == 0.8
        # Retry was actually triggered (two LLM calls made)
        assert stal_plugin_context.llm_pipeline.chat.call_count == 2
        # Second call must contain the JSON reminder
        retry_messages = stal_plugin_context.llm_pipeline.chat.call_args_list[1][1]["messages"]
        assert any("valid JSON only" in m.get("content", "") for m in retry_messages)

    @pytest.mark.asyncio
    async def test_classify_email_returns_none_when_both_calls_fail(self, stal_plugin_context):
        """Returns None when both the initial call and the retry return prose."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(content="I cannot decide. More context needed."),
            PipelineResult(content="Still not JSON, sorry."),
        ])

        result = await plugin._classifier.classify(
            sender="unknown@example.com",
            subject="???",
            body="Mystery email.",
        )

        assert result is None
        assert stal_plugin_context.llm_pipeline.chat.call_count == 2


class TestEmailAgentTick:
    """Test the tick() method and its guards."""

    @pytest.mark.asyncio
    async def test_tick_skips_within_interval(self, stal_plugin_context):
        """tick() does nothing if interval hasn't elapsed."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._state.last_check = time.time()  # Just checked

        await plugin.tick()

        # LLM pipeline should NOT have been called
        stal_plugin_context.llm_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_skips_during_quiet_hours(self, stal_plugin_context):
        """tick() skips when quiet hours are active."""
        stal_plugin_context.quiet_hours_checker.is_quiet_hours.return_value = True

        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0  # Force elapsed

        await plugin.tick()

        stal_plugin_context.llm_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_skips_without_pipeline(self, stal_plugin_context):
        """tick() skips when no LLM pipeline is available."""
        stal_plugin_context.llm_pipeline = None

        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._state.last_check = 0

        await plugin.tick()  # Should not raise


class TestEmailAgentActions:
    """Test action execution methods."""

    @pytest.mark.asyncio
    async def test_send_notification(self, stal_plugin_context, mock_telegram_notifier):
        """_send_notification() sends via tracked Telegram notifier."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for notification summary
        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Meeting request from colleague about Q1 results."
        )

        email = {"sender": "colleague@acme-motors.com", "subject": "Meeting", "body": "Can we meet?"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="Important"
        )

        result = await plugin._send_notification(email, classification)

        assert result is True
        mock_telegram_notifier.send_notification_tracked.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_reply(self, stal_plugin_context, mock_gmail_capability):
        """_send_reply() sends via Gmail capability with thread ID."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for reply generation
        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Dear colleague, I'd be happy to meet on Tuesday."
        )

        email = {
            "sender": "colleague@acme-motors.com",
            "subject": "Meeting next Tuesday?",
            "body": "Can we schedule a meeting?",
            "thread_id": "thread-001",
            "message_id": "msg-001",
        }

        result = await plugin._reply_gen.generate_and_send(email)

        assert result is True
        mock_gmail_capability.send_reply.assert_called_once()
        call_kwargs = mock_gmail_capability.send_reply.call_args.kwargs
        assert call_kwargs["to"] == "colleague@acme-motors.com"
        assert call_kwargs["subject"] == "Re: Meeting next Tuesday?"
        assert call_kwargs["thread_id"] == "thread-001"
        assert call_kwargs["message_id"] == "msg-001"

    @pytest.mark.asyncio
    async def test_consult_boss(self, stal_plugin_context, mock_ipc_client_email):
        """_consult_boss() sends IPC message and processes response."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for question generation
        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Should I reply to this restructuring email?"
        )

        email = {
            "sender": "unknown@acme-motors.com",
            "subject": "Confidential: Restructuring",
            "body": "We need to discuss privately.",
            "snippet": "We need to discuss privately.",
        }
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="Uncertain"
        )

        result = await plugin._consult_boss(email, classification)

        assert result is True
        mock_ipc_client_email.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_consult_boss_stores_learning(self, stal_plugin_context, mock_ipc_client_email):
        """Boss consultation stores a learning from the response."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Should I reply?"
        )

        email = {"sender": "test@example.com", "subject": "Test", "body": "Test body", "snippet": "Test"}
        classification = EmailClassification(
            intent=EmailIntent.ASK_BOSS, confidence=0.4, reasoning="Uncertain"
        )

        await plugin._consult_boss(email, classification)

        # Verify learning was stored
        learnings = await plugin._db.get_learnings()
        assert len(learnings) == 1
        assert "boss_feedback" in learnings[0].source


class TestEmailAgentDatabase:
    """Test the database layer directly."""

    @pytest.mark.asyncio
    async def test_database_setup(self, tmp_path):
        """Database setup creates tables."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_email_agent.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Verify tables exist
        assert await backend.table_exists("email_records")
        assert await backend.table_exists("agent_learnings")
        assert await backend.table_exists("agent_goals")

        await db.close()

    @pytest.mark.asyncio
    async def test_record_and_retrieve_email(self, tmp_path):
        """Can record and retrieve email classifications."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_email.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        record = EmailRecord(
            email_from="test@example.com",
            email_subject="Test Subject",
            email_snippet="Hello world",
            classified_intent="reply",
            confidence=0.95,
            reasoning="Meeting request",
            action_taken="reply_sent",
        )
        row_id = await db.record_email(record)
        assert row_id > 0

        recent = await db.get_recent_emails(limit=5)
        assert len(recent) == 1
        assert recent[0].email_from == "test@example.com"
        assert recent[0].classified_intent == "reply"

        await db.close()

    @pytest.mark.asyncio
    async def test_store_and_retrieve_learnings(self, tmp_path):
        """Can store and retrieve agent learnings."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_learnings.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        learning = AgentLearning(
            learning_type="classification",
            content="Boss advised reply for meeting requests",
            source="boss_feedback",
            email_from="colleague@example.com",
        )
        row_id = await db.store_learning(learning)
        assert row_id > 0

        learnings = await db.get_learnings()
        assert len(learnings) == 1
        assert learnings[0].source == "boss_feedback"

        await db.close()

    @pytest.mark.asyncio
    async def test_goals_crud(self, tmp_path):
        """Can create, update, and list goals."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_goals.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        goal = AgentGoal(
            description="Classify emails accurately",
            priority=90,
            progress=0.0,
            status="active",
        )
        goal_id = await db.upsert_goal(goal)
        assert goal_id > 0

        # Update progress
        goal.id = goal_id
        goal.progress = 0.5
        await db.upsert_goal(goal)

        active = await db.get_active_goals()
        assert len(active) == 1
        assert active[0].progress == 0.5

        await db.close()

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path):
        """get_stats() returns aggregate statistics."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_stats.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Insert some records
        for intent in ["reply", "reply", "notify", "ask_boss", "ignore"]:
            await db.record_email(EmailRecord(
                email_from="test@example.com",
                email_subject=f"Subject {intent}",
                classified_intent=intent,
                confidence=0.9,
                reasoning="Test",
            ))

        stats = await db.get_stats()
        assert stats["emails_processed"] == 5
        assert stats["emails_replied"] == 2
        assert stats["notifications_sent"] == 1
        assert stats["boss_consultations"] == 1

        await db.close()

    @pytest.mark.asyncio
    async def test_sender_history(self, tmp_path):
        """get_sender_history() returns records for a specific sender."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_sender.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Insert records from different senders
        await db.record_email(EmailRecord(
            email_from="alice@example.com",
            email_subject="From Alice",
            classified_intent="reply",
            confidence=0.9,
            reasoning="Test",
        ))
        await db.record_email(EmailRecord(
            email_from="bob@example.com",
            email_subject="From Bob",
            classified_intent="ignore",
            confidence=0.8,
            reasoning="Newsletter",
        ))

        alice_history = await db.get_sender_history("alice@example.com")
        assert len(alice_history) == 1
        assert alice_history[0].email_from == "alice@example.com"

        bob_history = await db.get_sender_history("bob@example.com")
        assert len(bob_history) == 1

        unknown_history = await db.get_sender_history("unknown@example.com")
        assert len(unknown_history) == 0

        await db.close()


class TestPromptTemplates:
    """Test that prompt templates produce valid message lists."""

    def test_classification_prompt_structure(self):
        """Classification prompt returns system + user messages with principal_name."""
        messages = classification_prompt(
            goals="- Classify accurately",
            learnings="- No learnings yet",
            sender_history="No history",
            sender="test@example.com",
            subject="Test",
            body="Hello world",
            principal_name="Test Principal",
            reply_policy="Allowed reply addresses: test@example.com",
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "IGNORE" in messages[0]["content"]
        assert "NOTIFY" in messages[0]["content"]
        assert "REPLY" in messages[0]["content"]
        assert "ASK_BOSS" in messages[0]["content"]
        assert "Test Principal" in messages[0]["content"]
        assert "test@example.com" in messages[0]["content"]

    def test_reply_prompt_structure(self):
        """Reply prompt returns system + user messages with Stål identity."""
        messages = reply_prompt(
            sender="test@example.com",
            subject="Meeting",
            body="Can we meet?",
            sender_context="First contact",
            interaction_history="None",
            principal_name="Test Principal",
        )
        assert len(messages) == 2
        assert "SAME LANGUAGE" in messages[0]["content"]
        assert "Stål" in messages[0]["content"]
        assert "Digital Assistant to Test Principal" in messages[0]["content"]

    def test_notification_prompt_structure(self):
        """Notification prompt returns system + user messages with principal_name."""
        messages = notification_prompt(
            sender="test@example.com",
            subject="Important",
            body="Something happened",
            principal_name="Test Principal",
        )
        assert len(messages) == 2
        assert "Telegram" in messages[0]["content"]
        assert "Test Principal" in messages[0]["content"]

    def test_boss_consultation_prompt_structure(self):
        """Boss consultation prompt returns system + user messages."""
        messages = boss_consultation_prompt(
            sender="test@example.com",
            subject="Confidential",
            snippet="We need to discuss...",
            reasoning="Uncertain intent",
            tentative_intent="ask_boss",
            confidence=0.4,
        )
        assert len(messages) == 2
        assert "0.4" in messages[1]["content"]

    def test_boss_consultation_prompt_english_enforcement(self):
        """Boss consultation prompt enforces English for IPC."""
        messages = boss_consultation_prompt(
            sender="test@example.com",
            subject="Test",
            snippet="Test",
            reasoning="Test",
            tentative_intent="ask_boss",
            confidence=0.5,
        )
        assert "English" in messages[0]["content"]
        assert "English" in messages[1]["content"]

    def test_reply_prompt_with_research_structure(self):
        """Reply prompt with research includes research context."""
        messages = reply_prompt_with_research(
            sender="test@example.com",
            subject="Meeting",
            body="Can we meet?",
            sender_context="First contact",
            interaction_history="None",
            principal_name="Test Principal",
            research_context="The topic is well documented.",
        )
        assert len(messages) == 2
        assert "RESEARCH CONTEXT" in messages[0]["content"]
        assert "well documented" in messages[0]["content"]

    def test_reply_prompt_with_research_no_research(self):
        """Reply prompt with empty research context has no RESEARCH section."""
        messages = reply_prompt_with_research(
            sender="test@example.com",
            subject="Meeting",
            body="Can we meet?",
            sender_context="First contact",
            interaction_history="None",
            principal_name="Test Principal",
            research_context="",
        )
        assert "RESEARCH CONTEXT" not in messages[0]["content"]

    def test_feedback_classification_prompt_structure(self):
        """Feedback classification prompt returns system + user messages."""
        messages = feedback_classification_prompt(
            feedback_text="Bra att du flaggade det!",
            original_notification="Email from boss about meeting",
            original_email_subject="Important meeting",
        )
        assert len(messages) == 2
        assert "sentiment" in messages[0]["content"]
        assert "Important meeting" in messages[1]["content"]


class TestGetStatus:
    """Test the plugin status reporting."""

    @pytest.mark.asyncio
    async def test_get_status_returns_all_fields(self, stal_plugin_context):
        """get_status() returns all expected fields."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        status = plugin.get_status()

        assert status["plugin"] == "email_agent"
        assert status["identity"] == "stal"
        assert "emails_processed" in status
        assert "emails_replied" in status
        assert "notifications_sent" in status
        assert "boss_consultations" in status
        assert "confidence_threshold" in status
        assert "active_goals" in status
        assert "learnings_count" in status
        assert "health" in status

    @pytest.mark.asyncio
    async def test_teardown(self, stal_plugin_context):
        """teardown() closes database without error."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        await plugin.teardown()  # Should not raise


class TestSmartSenderFiltering:
    """Test that sender filtering only applies to REPLY, not NOTIFY."""

    @pytest.mark.asyncio
    async def test_reply_blocked_for_unknown_sender_falls_back_to_notify(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """REPLY for non-allowed sender falls back to NOTIFY."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        classification = EmailClassification(
            intent=EmailIntent.REPLY, confidence=0.9, reasoning="Meeting request"
        )

        email = {
            "sender": "unknown@nobody.com",  # Not in allowed_senders
            "subject": "Meeting",
            "body": "Can we meet?",
        }

        # Mock LLM for notification
        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Meeting request summary."
        )

        result = await plugin._execute_action(email, classification)

        assert result == "reply_suppressed_notify_fallback"
        mock_telegram_notifier.send_notification_tracked.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_works_for_all_senders(self, stal_plugin_context, mock_telegram_notifier):
        """NOTIFY works for any sender (no filtering)."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for notification summary
        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Important notification"
        )

        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="Important"
        )

        email = {
            "sender": "unknown@nobody.com",
            "subject": "Urgent",
            "body": "Something important",
        }

        result = await plugin._execute_action(email, classification)

        assert result == "notification_sent"
        mock_telegram_notifier.send_notification_tracked.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_emails_classified_regardless_of_sender(
        self, stal_plugin_context,
    ):
        """All emails get classified, even from non-allowed senders."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for classification
        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "notify", "confidence": 0.85, "reasoning": "Important", "priority": "high"}'
        ))

        # Mock notifier for the notification
        mock_notifier = stal_plugin_context.get_capability("telegram_notifier")
        mock_notifier.send_notification_tracked = AsyncMock(return_value=42)

        email = {
            "sender": "unknown@nobody.com",  # Not in allowed_senders
            "subject": "Important notice",
            "body": "Something important happened",
            "snippet": "Something important",
            "message_id": "msg-123",
            "thread_id": "thread-123",
        }

        await plugin._process_email(email)

        # Should be classified and recorded
        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "notify"


class TestDeduplicationAndMarkRead:
    """Test email deduplication and mark-as-read behavior."""

    @pytest.mark.asyncio
    async def test_duplicate_email_skipped(self, stal_plugin_context):
        """Already-processed emails are skipped via message_id dedup."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for classification
        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "ignore", "confidence": 0.9, "reasoning": "Spam", "priority": "low"}'
        ))

        email = {
            "sender": "test@example.com",
            "subject": "Test",
            "body": "Hello",
            "snippet": "Hello",
            "message_id": "dedup-test-001",
            "thread_id": "thread-001",
        }

        # Process the first time — should work
        await plugin._process_email(email)
        records = await plugin._db.get_recent_emails(limit=10)
        assert len(records) == 1

        # Process the same email again — should be skipped
        stal_plugin_context.llm_pipeline.chat.reset_mock()
        await plugin._process_email(email)

        # No new record, LLM not called again
        records = await plugin._db.get_recent_emails(limit=10)
        assert len(records) == 1
        stal_plugin_context.llm_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_as_read_after_processing(
        self, stal_plugin_context, mock_gmail_capability,
    ):
        """Emails are marked as read in Gmail after processing (any action)."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "ignore", "confidence": 0.95, "reasoning": "Newsletter", "priority": "low"}'
        ))

        email = {
            "sender": "newsletter@example.com",
            "subject": "Newsletter",
            "body": "Weekly update",
            "snippet": "Weekly update",
            "message_id": "mark-read-test-001",
            "thread_id": "thread-001",
        }

        await plugin._process_email(email)

        # mark_as_read should be called even for IGNORE intent
        mock_gmail_capability.mark_as_read.assert_called_once_with("mark-read-test-001")

    @pytest.mark.asyncio
    async def test_mark_as_read_called_in_dry_run_mode(
        self, stal_plugin_context, mock_gmail_capability, mock_telegram_notifier,
    ):
        """Emails are marked as read even in dry_run mode (NOTIFY action)."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        plugin._dry_run = True

        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "notify", "confidence": 0.9, "reasoning": "Important update", "priority": "normal"}'
        ))

        email = {
            "sender": "jens@example.com",
            "subject": "Important Update",
            "body": "Please review this carefully.",
            "snippet": "Please review",
            "message_id": "dry-run-mark-read-001",
            "thread_id": "thread-001",
            "headers": {},
        }

        await plugin._process_email(email)

        # Even in dry_run mode, the email must be marked as read so it does not
        # re-appear on the next tick.
        mock_gmail_capability.mark_as_read.assert_called_with("dry-run-mark-read-001")

    @pytest.mark.asyncio
    async def test_ask_boss_fallback_to_notify(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """When boss consultation fails, falls back to notification."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Make IPC fail
        stal_plugin_context.ipc_client.send = AsyncMock(side_effect=Exception("Connection refused"))

        # Mock LLM: first call for classification (ask_boss), second for question, third for notification
        call_count = 0
        async def _mock_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Classification
                return PipelineResult(
                    content='{"intent": "ask_boss", "confidence": 0.3, "reasoning": "Uncertain", "priority": "high"}'
                )
            # Boss question generation or notification
            return PipelineResult(content="Notification summary for the principal.")

        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=_mock_chat)

        email = {
            "sender": "unknown@example.com",
            "subject": "Uncertain Email",
            "body": "Something we need to think about",
            "snippet": "Something",
            "message_id": "fallback-test-001",
            "thread_id": "thread-001",
        }

        await plugin._process_email(email)

        # Should have recorded with a fallback action
        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert "boss_unavailable" in records[0].action_taken

        # Notification should have been sent as fallback
        mock_telegram_notifier.send_notification_tracked.assert_called_once()

    @pytest.mark.asyncio
    async def test_has_been_processed_database_method(self, tmp_path):
        """has_been_processed() returns True for known message IDs."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_dedup.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Not processed yet
        assert await db.has_been_processed("msg-123") is False

        # Record it
        await db.record_email(EmailRecord(
            gmail_message_id="msg-123",
            email_from="test@example.com",
            email_subject="Test",
            classified_intent="ignore",
            confidence=0.9,
            reasoning="Test",
        ))

        # Now it should be found
        assert await db.has_been_processed("msg-123") is True

        # Empty message_id always returns False
        assert await db.has_been_processed("") is False

        await db.close()


class TestResearchIntegration:
    """Test research capability integration."""

    @pytest.mark.asyncio
    async def test_request_research_uses_boss_cap(
        self, stal_plugin_context, mock_boss_request_capability,
    ):
        """_request_research() uses BossRequestCapability."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        result = await plugin._reply_gen._request_research("What is the weather?")

        assert result is not None
        assert "Research summary" in result
        mock_boss_request_capability.request_research.assert_called_once_with(
            "What is the weather?", "",
        )

    @pytest.mark.asyncio
    async def test_request_research_without_capability(self, stal_context_no_ipc):
        """_request_research() returns None when capability unavailable."""
        plugin = EmailAgentPlugin(stal_context_no_ipc)
        await plugin.setup()

        result = await plugin._reply_gen._request_research("test query")
        assert result is None


class TestFeedbackProcessing:
    """Test Telegram feedback classification and processing."""

    @pytest.mark.asyncio
    async def test_parse_feedback_positive(self, stal_plugin_context):
        """Parses positive feedback JSON correctly."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = '{"sentiment": "positive", "learning": "Good notification", "should_acknowledge": false}'
        sentiment, learning, should_ack = plugin._parse_feedback_classification(raw)

        assert sentiment == "positive"
        assert learning == "Good notification"
        assert should_ack is False

    @pytest.mark.asyncio
    async def test_parse_feedback_negative(self, stal_plugin_context):
        """Parses negative feedback JSON correctly."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = '{"sentiment": "negative", "learning": "Not important", "should_acknowledge": true}'
        sentiment, learning, should_ack = plugin._parse_feedback_classification(raw)

        assert sentiment == "negative"
        assert learning == "Not important"
        assert should_ack is True

    @pytest.mark.asyncio
    async def test_parse_feedback_invalid_json(self, stal_plugin_context):
        """Returns neutral for unparseable feedback."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        sentiment, learning, should_ack = plugin._parse_feedback_classification("not json at all")

        assert sentiment == "neutral"
        assert learning == ""
        assert should_ack is False

    @pytest.mark.asyncio
    async def test_classify_feedback_heuristic_positive(self, stal_plugin_context):
        """Heuristic fallback classifies positive feedback."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Disable LLM to test heuristic
        stal_plugin_context.llm_pipeline = None

        sentiment, learning, should_ack = await plugin._classify_feedback(
            "Bra att du flaggade det!", "Notification text", "Email subject",
        )

        assert sentiment == "positive"

    @pytest.mark.asyncio
    async def test_classify_feedback_heuristic_negative(self, stal_plugin_context):
        """Heuristic fallback classifies negative feedback."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline = None

        sentiment, learning, should_ack = await plugin._classify_feedback(
            "Inte viktigt, sluta notifiera", "Notification text", "Email subject",
        )

        assert sentiment == "negative"
        assert should_ack is True


class TestDatabaseNotificationTracking:
    """Test database notification tracking methods."""

    @pytest.mark.asyncio
    async def test_track_and_retrieve_notification(self, tmp_path):
        """Can track a notification and retrieve it by TG message ID."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend
        from overblick.plugins.email_agent.database import EmailAgentDB

        db_path = tmp_path / "test_tracking.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # First insert an email record
        record = EmailRecord(
            email_from="test@example.com",
            email_subject="Test Subject",
            classified_intent="notify",
            confidence=0.9,
            reasoning="Test",
            action_taken="notification_sent",
        )
        record_id = await db.record_email(record)

        # Track the notification
        tracking_id = await db.track_notification(
            email_record_id=record_id,
            tg_message_id=42,
            tg_chat_id="12345",
            notification_text="Test notification",
        )
        assert tracking_id > 0

        # Retrieve by TG message ID
        result = await db.get_notification_by_tg_id(42)
        assert result is not None
        assert result["email_record_id"] == record_id
        assert result["tg_message_id"] == 42
        assert result["email_subject"] == "Test Subject"

        await db.close()

    @pytest.mark.asyncio
    async def test_record_feedback_on_tracking(self, tmp_path):
        """Can record feedback on a tracked notification."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend
        from overblick.plugins.email_agent.database import EmailAgentDB

        db_path = tmp_path / "test_feedback.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        record_id = await db.record_email(EmailRecord(
            email_from="test@example.com",
            email_subject="Test",
            classified_intent="notify",
            confidence=0.9,
            reasoning="Test",
        ))

        tracking_id = await db.track_notification(
            email_record_id=record_id,
            tg_message_id=42,
            tg_chat_id="12345",
        )

        await db.record_feedback(tracking_id, "Great job!", "positive")

        # Verify feedback was recorded
        result = await db.get_notification_by_tg_id(42)
        assert result["feedback_received"]  # SQLite returns 1 for TRUE
        assert result["feedback_text"] == "Great job!"
        assert result["feedback_sentiment"] == "positive"

        await db.close()

    @pytest.mark.asyncio
    async def test_get_notification_by_tg_id_not_found(self, tmp_path):
        """Returns None for unknown TG message ID."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend
        from overblick.plugins.email_agent.database import EmailAgentDB

        db_path = tmp_path / "test_not_found.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        result = await db.get_notification_by_tg_id(99999)
        assert result is None

        await db.close()


class TestIntentNormalization:
    """Test normalization of hallucinated LLM intents to valid EmailIntent values."""

    def test_valid_intents_pass_through(self):
        """Valid intent strings return unchanged."""
        assert EmailClassifier.normalize_intent("ignore") == "ignore"
        assert EmailClassifier.normalize_intent("notify") == "notify"
        assert EmailClassifier.normalize_intent("reply") == "reply"
        assert EmailClassifier.normalize_intent("ask_boss") == "ask_boss"

    def test_alias_escalate_maps_to_ask_boss(self):
        """'escalate' (common LLM hallucination) maps to 'ask_boss'."""
        assert EmailClassifier.normalize_intent("escalate") == "ask_boss"

    def test_alias_verify_maps_to_ask_boss(self):
        """'verify' maps to 'ask_boss'."""
        assert EmailClassifier.normalize_intent("verify") == "ask_boss"

    def test_alias_block_maps_to_ignore(self):
        """'block' maps to 'ignore'."""
        assert EmailClassifier.normalize_intent("block") == "ignore"

    def test_alias_spam_maps_to_ignore(self):
        """'spam' maps to 'ignore'."""
        assert EmailClassifier.normalize_intent("spam") == "ignore"

    def test_case_insensitive(self):
        """Intent normalization is case-insensitive."""
        assert EmailClassifier.normalize_intent("IGNORE") == "ignore"
        assert EmailClassifier.normalize_intent("Escalate") == "ask_boss"

    def test_unknown_intent_returns_none(self):
        """Completely unrecognizable intents return None."""
        assert EmailClassifier.normalize_intent("do_a_backflip") is None

    def test_whitespace_stripped(self):
        """Whitespace is stripped from intents."""
        assert EmailClassifier.normalize_intent("  escalate  ") == "ask_boss"


class TestSafeSenderName:
    """Test filename sanitization for sender profiles."""

    def test_simple_email(self):
        """Simple email address produces clean filename."""
        result = ReputationManager._safe_sender_name("user@example.com")
        assert result == "user_at_example_com"

    def test_display_name_with_angle_brackets(self):
        """Display name + angle brackets extracts just the email."""
        result = ReputationManager._safe_sender_name("Alice <alice@acme.org>")
        assert result == "alice_at_acme_org"

    def test_complex_display_name(self):
        """Complex display names with quotes and special chars are handled."""
        result = ReputationManager._safe_sender_name(
            '"Adam Mancini from Adam Mancini\'s S&P 500" <tradecompanion@substack.com>'
        )
        assert result == "tradecompanion_at_substack_com"

    def test_no_slashes_or_quotes(self):
        """Result never contains filesystem-dangerous characters."""
        result = ReputationManager._safe_sender_name(
            'Test "Quoted" <user/path@domain.com>'
        )
        assert "/" not in result
        assert '"' not in result
        assert "'" not in result


class TestSenderReputation:
    """Test sender and domain reputation system."""

    def test_extract_domain_simple(self):
        """Extracts domain from plain email address."""
        assert ReputationManager.extract_domain("user@example.com") == "example.com"

    def test_extract_domain_with_name(self):
        """Extracts domain from 'Name <email>' format."""
        assert ReputationManager.extract_domain("Alice <alice@acme.org>") == "acme.org"

    def test_extract_domain_no_at(self):
        """Returns empty string when no @ found."""
        assert ReputationManager.extract_domain("invalid-sender") == ""

    @pytest.mark.asyncio
    async def test_unknown_sender_reputation(self, stal_plugin_context):
        """Unknown sender returns known=False."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        rep = await plugin._reputation.get_sender_reputation("unknown@example.com")
        assert rep["known"] is False

    @pytest.mark.asyncio
    async def test_known_sender_reputation(self, stal_plugin_context):
        """Known sender returns calculated reputation."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Create a sender profile with history
        profile = SenderProfile(
            email="newsletter@spam.com",
            total_interactions=10,
            intent_distribution={"ignore": 9, "notify": 1},
            avg_confidence=0.85,
        )
        safe_name = "newsletter@spam.com".replace("@", "_at_").replace(".", "_")
        profile_path = plugin._profiles_dir / f"{safe_name}.json"
        profile_path.write_text(json.dumps(profile.model_dump(), indent=2))

        rep = await plugin._reputation.get_sender_reputation("newsletter@spam.com")
        assert rep["known"] is True
        assert rep["total"] == 10
        assert rep["ignore_rate"] == 0.9
        assert rep["ignore_count"] == 9
        assert rep["notify_count"] == 1

    @pytest.mark.asyncio
    async def test_should_auto_ignore_sender_true(self, stal_plugin_context):
        """Auto-ignore triggered when ignore rate high enough."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        rep = {"known": True, "total": 10, "ignore_rate": 0.95}
        assert plugin._reputation.should_auto_ignore_sender(rep) is True

    @pytest.mark.asyncio
    async def test_should_auto_ignore_sender_false_low_count(self, stal_plugin_context):
        """Auto-ignore NOT triggered when insufficient interactions."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        rep = {"known": True, "total": 3, "ignore_rate": 1.0}
        assert plugin._reputation.should_auto_ignore_sender(rep) is False

    @pytest.mark.asyncio
    async def test_should_auto_ignore_sender_false_low_rate(self, stal_plugin_context):
        """Auto-ignore NOT triggered when ignore rate below threshold."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        rep = {"known": True, "total": 10, "ignore_rate": 0.5}
        assert plugin._reputation.should_auto_ignore_sender(rep) is False

    @pytest.mark.asyncio
    async def test_auto_ignore_skips_llm(self, stal_plugin_context):
        """Auto-ignored emails skip LLM classification entirely."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Create a sender profile with high ignore rate
        profile = SenderProfile(
            email="spam@marketing.com",
            total_interactions=10,
            intent_distribution={"ignore": 10},
            avg_confidence=0.95,
        )
        safe_name = "spam@marketing.com".replace("@", "_at_").replace(".", "_")
        profile_path = plugin._profiles_dir / f"{safe_name}.json"
        profile_path.write_text(json.dumps(profile.model_dump(), indent=2))

        email = {
            "sender": "spam@marketing.com",
            "subject": "SALE! 50% off!",
            "body": "Buy now!",
            "snippet": "Buy now!",
            "message_id": "auto-ignore-test-001",
            "thread_id": "thread-001",
            "headers": {},
        }

        await plugin._process_email(email)

        # LLM should NOT have been called (auto-ignore skips it)
        stal_plugin_context.llm_pipeline.chat.assert_not_called()

        # Should still be recorded in DB
        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "ignore"
        assert records[0].action_taken == "auto_ignored"

    @pytest.mark.asyncio
    async def test_domain_auto_ignore_from_cache(self, stal_plugin_context):
        """Cached auto-ignore domains bypass LLM."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Manually add domain to auto-ignore cache
        plugin._auto_ignore_domains.add("spam-domain.com")

        email = {
            "sender": "anyone@spam-domain.com",
            "subject": "Another spam",
            "body": "Content",
            "snippet": "Content",
            "message_id": "domain-ignore-test-001",
            "thread_id": "thread-001",
            "headers": {},
        }

        await plugin._process_email(email)

        stal_plugin_context.llm_pipeline.chat.assert_not_called()
        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].action_taken == "auto_ignored"


class TestDomainReputationDB:
    """Test domain reputation database operations."""

    @pytest.mark.asyncio
    async def test_update_and_get_domain_stats(self, tmp_path):
        """Can update and retrieve domain stats."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_domain.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # First update creates the record
        await db.update_domain_stats("spam.com", "ignore")
        stats = await db.get_domain_stats("spam.com")
        assert stats is not None
        assert stats["ignore_count"] == 1
        assert stats["notify_count"] == 0

        # Second update increments
        await db.update_domain_stats("spam.com", "ignore")
        await db.update_domain_stats("spam.com", "notify")
        stats = await db.get_domain_stats("spam.com")
        assert stats["ignore_count"] == 2
        assert stats["notify_count"] == 1

        await db.close()

    @pytest.mark.asyncio
    async def test_domain_stats_feedback(self, tmp_path):
        """Domain stats track negative/positive feedback."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_domain_fb.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        await db.update_domain_stats("example.com", "notify")
        await db.update_domain_stats("example.com", "", feedback="negative")
        await db.update_domain_stats("example.com", "", feedback="positive")

        stats = await db.get_domain_stats("example.com")
        assert stats["negative_feedback_count"] == 1
        assert stats["positive_feedback_count"] == 1

        await db.close()

    @pytest.mark.asyncio
    async def test_auto_ignore_domains(self, tmp_path):
        """Can set and retrieve auto-ignore domains."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_auto_ignore.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Create a domain record first
        await db.update_domain_stats("spam.com", "ignore")
        await db.set_auto_ignore("spam.com", True)

        domains = await db.get_auto_ignore_domains()
        assert "spam.com" in domains

        await db.close()

    @pytest.mark.asyncio
    async def test_unknown_domain_returns_none(self, tmp_path):
        """get_domain_stats() returns None for unknown domain."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_unknown_domain.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        stats = await db.get_domain_stats("nonexistent.com")
        assert stats is None

        await db.close()


class TestCrossIdentityConsultation:
    """Test cross-identity relevance consultation."""

    @pytest.mark.asyncio
    async def test_consultation_downgrade_notify_to_ignore(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """Identity consultation can downgrade NOTIFY to IGNORE."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Consultant says NO
        mock_personality_consultant.consult = AsyncMock(
            return_value="NO — this is just a generic crypto newsletter, not worth notifying."
        )

        email = {
            "sender": "updates@crypto-news.com",
            "subject": "Bitcoin weekly digest",
            "body": "This week in crypto...",
            "snippet": "This week in crypto...",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY,
            confidence=0.65,
            reasoning="Contains crypto content",
        )

        advice = await plugin._consult_identity_relevance(email, classification)
        assert advice is not None
        assert advice.startswith("NO")

        # Verify anomal was consulted (crypto keyword match)
        mock_personality_consultant.consult.assert_called_once()
        call_kwargs = mock_personality_consultant.consult.call_args.kwargs
        assert call_kwargs["consultant_name"] == "anomal"

    @pytest.mark.asyncio
    async def test_consultation_keeps_notify(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """Identity consultation keeps NOTIFY when identity says YES."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Consultant says YES (default mock)
        email = {
            "sender": "expert@security.org",
            "subject": "Critical security vulnerability in Linux kernel",
            "body": "A new zero-day has been discovered...",
            "snippet": "A new zero-day...",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY,
            confidence=0.7,
            reasoning="Security content",
        )

        advice = await plugin._consult_identity_relevance(email, classification)
        assert advice is not None
        assert advice.startswith("YES")

        # Verify blixt was consulted (security keyword match)
        call_kwargs = mock_personality_consultant.consult.call_args.kwargs
        assert call_kwargs["consultant_name"] == "blixt"

    @pytest.mark.asyncio
    async def test_consultation_no_keyword_match(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """No consultation when no keywords match."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        email = {
            "sender": "hr@company.com",
            "subject": "Company picnic next Friday",
            "body": "Join us for the annual company picnic...",
            "snippet": "Join us for the annual...",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY,
            confidence=0.6,
            reasoning="Company event",
        )

        advice = await plugin._consult_identity_relevance(email, classification)
        assert advice is None  # No keyword match, no consultation
        mock_personality_consultant.consult.assert_not_called()

    @pytest.mark.asyncio
    async def test_consultation_not_triggered_high_confidence(
        self, stal_plugin_context,
    ):
        """Consultation NOT triggered for high-confidence NOTIFY."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM: high confidence NOTIFY
        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "notify", "confidence": 0.95, "reasoning": "Clearly important", "priority": "high"}'
        ))

        mock_notifier = stal_plugin_context.get_capability("telegram_notifier")
        mock_notifier.send_notification_tracked = AsyncMock(return_value=42)

        mock_consultant = stal_plugin_context.get_capability("personality_consultant")

        email = {
            "sender": "ceo@crypto-exchange.com",
            "subject": "Important: Bitcoin custody update",
            "body": "Immediate action required for your crypto holdings...",
            "snippet": "Immediate action required",
            "message_id": "high-conf-001",
            "thread_id": "thread-001",
            "headers": {},
        }

        await plugin._process_email(email)

        # Consultation should NOT have been called (confidence 0.95 > 0.8 threshold)
        mock_consultant.consult.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_flow_consultation_downgrades(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """Full _process_email flow: moderate NOTIFY + NO consultation → IGNORE."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # LLM classifies as moderate-confidence NOTIFY (0.75 is within 0.5-0.8
        # consultation range AND above 0.7 confidence threshold)
        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "notify", "confidence": 0.75, "reasoning": "Crypto content", "priority": "normal"}'
        ))

        # Consultant says NO
        mock_personality_consultant.consult = AsyncMock(
            return_value="NO — this is a generic newsletter."
        )

        email = {
            "sender": "news@crypto-weekly.com",
            "subject": "Your weekly Bitcoin update",
            "body": "This week: Bitcoin rose 3%...",
            "snippet": "This week: Bitcoin rose",
            "message_id": "consult-flow-001",
            "thread_id": "thread-001",
            "headers": {"List-Unsubscribe": "<mailto:unsub@crypto-weekly.com>"},
        }

        await plugin._process_email(email)

        # Should have been downgraded to IGNORE
        records = await plugin._db.get_recent_emails(limit=1)
        assert len(records) == 1
        assert records[0].classified_intent == "ignore"
        assert "downgraded by identity consultation" in records[0].reasoning


class TestScoredConsultation:
    """Test scored keyword matching and auto-discovery for consultation."""

    @pytest.mark.asyncio
    async def test_consultation_scored_matching(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """Highest-scoring identity is selected via keyword count."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Anomal has 3 matches (crypto, bitcoin, blockchain), blixt has 0
        email = {
            "sender": "news@crypto-weekly.com",
            "subject": "Bitcoin and blockchain weekly: crypto market update",
            "body": "This week in crypto: Bitcoin rose 3%, blockchain adoption...",
            "snippet": "This week in crypto: Bitcoin rose 3%...",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY,
            confidence=0.65,
            reasoning="Crypto content",
        )

        mock_personality_consultant.consult = AsyncMock(
            return_value="NO — generic newsletter."
        )

        await plugin._consult_identity_relevance(email, classification)

        call_kwargs = mock_personality_consultant.consult.call_args.kwargs
        assert call_kwargs["consultant_name"] == "anomal"

    @pytest.mark.asyncio
    async def test_consultation_auto_discover_mode(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """identities: "all" triggers auto-discovery via discover_consultants()."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Switch to auto-discover mode
        plugin._consultation_identities = "all"
        plugin._discovered_consultants = {}

        # Mock discover_consultants on the consultant capability
        mock_personality_consultant.discover_consultants = MagicMock(
            return_value={
                "anomal": ["crypto", "bitcoin", "ai"],
                "blixt": ["privacy", "security"],
                "cherry": ["dating", "relationships"],
            },
        )
        mock_personality_consultant.score_match = (
            PersonalityConsultantCapability.score_match
        )

        email = {
            "sender": "expert@security.org",
            "subject": "Critical security vulnerability discovered",
            "body": "A new zero-day affects privacy settings...",
            "snippet": "A new zero-day...",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY,
            confidence=0.7,
            reasoning="Security content",
        )

        await plugin._consult_identity_relevance(email, classification)

        # Should have called discover_consultants and picked blixt (security + privacy)
        mock_personality_consultant.discover_consultants.assert_called_once()
        call_kwargs = mock_personality_consultant.consult.call_args.kwargs
        assert call_kwargs["consultant_name"] == "blixt"

    @pytest.mark.asyncio
    async def test_consultation_explicit_mode_backward_compat(
        self, stal_plugin_context, mock_personality_consultant,
    ):
        """Explicit mode (default) uses legacy relevance_consultants list."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Ensure explicit mode is active (default from fixture)
        assert plugin._consultation_identities == "explicit"

        email = {
            "sender": "news@crypto-weekly.com",
            "subject": "Bitcoin weekly digest",
            "body": "This week in crypto...",
            "snippet": "This week in crypto...",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY,
            confidence=0.65,
            reasoning="Crypto content",
        )

        await plugin._consult_identity_relevance(email, classification)

        # Should use the legacy list — anomal is configured with crypto keywords
        call_kwargs = mock_personality_consultant.consult.call_args.kwargs
        assert call_kwargs["consultant_name"] == "anomal"


class TestReputationContext:
    """Test reputation context building for prompts."""

    def test_build_reputation_context_known_sender(self):
        """Builds reputation context string for known sender."""
        sender_rep = {
            "known": True, "total": 10, "ignore_rate": 0.8,
            "notify_count": 2, "reply_count": 0,
        }
        domain_rep = {"known": False}

        context = EmailClassifier.build_reputation_context(sender_rep, domain_rep)
        assert "10 previous emails" in context
        assert "80%" in context

    def test_build_reputation_context_with_domain(self):
        """Builds context with both sender and domain info."""
        sender_rep = {
            "known": True, "total": 5, "ignore_rate": 0.6,
            "notify_count": 2, "reply_count": 0,
        }
        domain_rep = {
            "known": True, "domain": "example.com", "total": 50,
            "ignore_rate": 0.9, "negative_feedback": 3, "positive_feedback": 0,
        }

        context = EmailClassifier.build_reputation_context(sender_rep, domain_rep)
        assert "example.com" in context
        assert "50 total emails" in context
        assert "3 negative" in context

    def test_build_email_signals_with_headers(self):
        """Builds signal context from email headers."""
        headers = {
            "List-Unsubscribe": "<mailto:unsub@example.com>",
            "Precedence": "bulk",
            "List-Id": "marketing.example.com",
        }

        signals = EmailClassifier.build_email_signals(headers)
        assert "List-Unsubscribe" in signals
        assert "newsletter" in signals.lower()
        assert "bulk" in signals
        assert "marketing.example.com" in signals

    def test_build_email_signals_empty(self):
        """Returns empty string when no headers."""
        assert EmailClassifier.build_email_signals({}) == ""


class TestEnhancedPrompts:
    """Test that enhanced prompts include reputation and signals."""

    def test_classification_prompt_with_reputation(self):
        """Classification prompt includes reputation context."""
        messages = classification_prompt(
            goals="- Classify accurately",
            learnings="- No learnings yet",
            sender_history="No history",
            sender="test@example.com",
            subject="Test",
            body="Hello world",
            principal_name="Test Principal",
            reply_policy="Allowed reply addresses: test@example.com",
            sender_reputation="Sender: 10 emails, 80% ignored",
            email_signals="- Has List-Unsubscribe header (likely newsletter)",
        )
        assert "80% ignored" in messages[0]["content"]
        assert "List-Unsubscribe" in messages[0]["content"]

    def test_classification_prompt_ignore_categories(self):
        """Classification prompt has detailed IGNORE categories."""
        messages = classification_prompt(
            goals="- Classify",
            learnings="None",
            sender_history="None",
            sender="test@example.com",
            subject="Test",
            body="Test",
            principal_name="Test Principal",
        )
        system = messages[0]["content"]
        assert "Newsletters and marketing" in system
        assert "Cold outreach" in system
        assert "Automated notifications" in system
        assert "prefer IGNORE" in system

    def test_classification_prompt_without_reputation(self):
        """Classification prompt works without reputation context."""
        messages = classification_prompt(
            goals="- Classify",
            learnings="None",
            sender_history="None",
            sender="test@example.com",
            subject="Test",
            body="Test",
        )
        assert len(messages) == 2
        # Should not have "reputation" section when empty
        assert "Sender:" not in messages[0]["content"] or "reputation" not in messages[0]["content"].lower()


class TestDatabaseMigrationV6:
    """Test that migration v6 (sender_reputation) applies correctly."""

    @pytest.mark.asyncio
    async def test_migration_creates_sender_reputation_table(self, tmp_path):
        """Migration v6 creates the sender_reputation table."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_migration_v6.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        assert await backend.table_exists("sender_reputation")

        # Verify columns by inserting a row
        await db.update_domain_stats("test.com", "ignore")
        stats = await db.get_domain_stats("test.com")
        assert stats is not None
        assert "ignore_count" in stats
        assert "positive_feedback_count" in stats
        assert "auto_ignore" in stats

        await db.close()


class TestGmailMessageHeaders:
    """Test GmailMessage headers field."""

    def test_gmail_message_with_headers(self):
        """GmailMessage includes headers field."""
        from overblick.capabilities.communication.gmail import GmailMessage

        msg = GmailMessage(
            message_id="<test@example.com>",
            thread_id="<test@example.com>",
            sender="alice@example.com",
            subject="Hello",
            body="Body text",
            snippet="Body text",
            timestamp="Mon, 10 Feb 2026 14:30:00 +0100",
            headers={"List-Unsubscribe": "<mailto:unsub@example.com>"},
        )
        assert "List-Unsubscribe" in msg.headers

    def test_gmail_message_default_empty_headers(self):
        """GmailMessage defaults to empty headers."""
        from overblick.capabilities.communication.gmail import GmailMessage

        msg = GmailMessage(
            message_id="<test@example.com>",
            thread_id="<test@example.com>",
            sender="alice@example.com",
            subject="Hello",
            body="Body text",
            snippet="Body text",
            timestamp="Mon, 10 Feb 2026 14:30:00 +0100",
        )
        assert msg.headers == {}


class TestGmailHeaderExtraction:
    """Test that _imap_fetch_message extracts classification-relevant headers."""

    @pytest.mark.asyncio
    async def test_fetch_extracts_list_unsubscribe(self):
        """fetch_unread() extracts List-Unsubscribe header."""
        from unittest.mock import patch
        from overblick.capabilities.communication.gmail import GmailCapability

        from email.mime.text import MIMEText

        # Build email with List-Unsubscribe header
        email_msg = MIMEText("Newsletter content", "plain", "utf-8")
        email_msg["From"] = "news@example.com"
        email_msg["To"] = "test@gmail.com"
        email_msg["Subject"] = "Weekly Newsletter"
        email_msg["Message-ID"] = "<newsletter-001@example.com>"
        email_msg["Date"] = "Mon, 10 Feb 2026 14:30:00 +0100"
        email_msg["List-Unsubscribe"] = "<mailto:unsub@example.com>"
        email_msg["Precedence"] = "bulk"
        raw_bytes = email_msg.as_bytes()

        # Use helper from test_gmail_capability
        from tests.capabilities.test_gmail_capability import _make_ctx, _mock_imap

        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        mock_imap = _mock_imap(
            search_uids=[b"1"],
            fetch_data={b"1": raw_bytes},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread()

        assert len(results) == 1
        assert "List-Unsubscribe" in results[0].headers
        assert results[0].headers["List-Unsubscribe"] == "<mailto:unsub@example.com>"
        assert "Precedence" in results[0].headers
        assert results[0].headers["Precedence"] == "bulk"

    @pytest.mark.asyncio
    async def test_fetch_no_signal_headers(self):
        """fetch_unread() returns empty headers when none present."""
        from unittest.mock import patch
        from overblick.capabilities.communication.gmail import GmailCapability

        from tests.capabilities.test_gmail_capability import (
            _make_ctx, _mock_imap, _build_raw_email,
        )

        ctx = _make_ctx()
        cap = GmailCapability(ctx)
        await cap.setup()

        raw_email = _build_raw_email(
            sender="person@example.com",
            subject="Personal email",
            body="Hey, how are you?",
        )

        mock_imap = _mock_imap(
            search_uids=[b"1"],
            fetch_data={b"1": raw_email},
        )

        with patch("overblick.capabilities.communication.gmail.imaplib.IMAP4_SSL", return_value=mock_imap):
            results = await cap.fetch_unread()

        assert len(results) == 1
        assert results[0].headers == {}


class TestReputationConfigLoading:
    """Test that reputation config is loaded from personality YAML."""

    @pytest.mark.asyncio
    async def test_reputation_thresholds_loaded(self, stal_plugin_context):
        """setup() loads reputation thresholds from config."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._auto_ignore_sender_threshold == 0.9
        assert plugin._auto_ignore_sender_min_count == 5
        assert plugin._auto_ignore_domain_threshold == 0.9
        assert plugin._auto_ignore_domain_min_count == 10

    @pytest.mark.asyncio
    async def test_consultation_config_loaded(self, stal_plugin_context):
        """setup() loads consultation config from personality."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._consultation_confidence_low == 0.5
        assert plugin._consultation_confidence_high == 0.8
        assert len(plugin._relevance_consultants) == 2
        assert plugin._relevance_consultants[0]["identity"] == "anomal"

    @pytest.mark.asyncio
    async def test_headers_passed_to_process_email(self, stal_plugin_context):
        """Headers from email dict are passed through to classification."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM
        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"intent": "ignore", "confidence": 0.95, "reasoning": "Newsletter with unsubscribe", "priority": "low"}'
        ))

        email = {
            "sender": "news@updates.com",
            "subject": "Weekly digest",
            "body": "Content here",
            "snippet": "Content here",
            "message_id": "headers-test-001",
            "thread_id": "thread-001",
            "headers": {"List-Unsubscribe": "<mailto:unsub@updates.com>"},
        }

        await plugin._process_email(email)

        # Verify LLM was called (not auto-ignored since new sender)
        stal_plugin_context.llm_pipeline.chat.assert_called_once()

        # The classification prompt should include email signals
        call_args = stal_plugin_context.llm_pipeline.chat.call_args
        messages = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        system_content = messages[0]["content"]
        assert "List-Unsubscribe" in system_content


class TestMaxEmailAgeFilter:
    """Test max_email_age_hours filtering in _fetch_unread and _is_recent_email."""

    def test_is_recent_email_within_limit(self):
        """Email within max_hours returns True."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        date_str = email.utils.format_datetime(now)
        msg = {"headers": {"Date": date_str}}
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is True

    def test_is_recent_email_beyond_limit(self):
        """Email older than max_hours returns False."""
        from datetime import datetime, timezone, timedelta

        old = datetime.now(timezone.utc) - timedelta(hours=5)
        date_str = email.utils.format_datetime(old)
        msg = {"headers": {"Date": date_str}}
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is False

    def test_is_recent_email_no_date_header(self):
        """Missing Date header fails open (True) — prevent permanent data loss."""
        msg = {"headers": {}}
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is True

    def test_is_recent_email_no_headers(self):
        """Missing headers dict fails open (True) — prevent permanent data loss."""
        msg = {}
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is True

    def test_is_recent_email_unparseable_date(self):
        """Unparseable Date header fails open (True) — prevent permanent data loss."""
        msg = {"headers": {"Date": "not-a-date"}}
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is True

    def test_is_recent_email_naive_datetime(self):
        """Date header without timezone is treated as UTC."""
        from datetime import datetime, timezone, timedelta

        # Create a naive datetime string (no timezone info)
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        date_str = recent.strftime("%a, %d %b %Y %H:%M:%S")
        msg = {"headers": {"Date": date_str}}
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is True

    @pytest.mark.asyncio
    async def test_no_age_filter_defaults_to_48h(self, stal_plugin_context):
        """When max_email_age_hours is not configured, defaults to 48h to prevent old backlog."""
        plugin = EmailAgentPlugin(stal_plugin_context)

        # Override config to remove max_email_age_hours
        stal_plugin_context.identity.raw_config["email_agent"].pop("max_email_age_hours", None)
        await plugin.setup()

        assert plugin._max_email_age_hours == 48

    @pytest.mark.asyncio
    async def test_age_filter_config_loaded(self, stal_plugin_context):
        """setup() loads max_email_age_hours from config."""
        # Add config
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = 3
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._max_email_age_hours == 3.0

    @pytest.mark.asyncio
    async def test_old_emails_skipped_in_fetch(
        self, stal_plugin_context, mock_gmail_capability,
    ):
        """_fetch_unread skips emails older than max_email_age_hours."""
        from datetime import datetime, timezone, timedelta

        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = 3
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        old_date = datetime.now(timezone.utc) - timedelta(hours=5)
        recent_date = datetime.now(timezone.utc) - timedelta(hours=1)

        # Create mock messages
        old_msg = MagicMock()
        old_msg.message_id = "old-001"
        old_msg.thread_id = "thread-001"
        old_msg.sender = "old@example.com"
        old_msg.subject = "Old email"
        old_msg.body = "Old content"
        old_msg.snippet = "Old content"
        old_msg.timestamp = email.utils.format_datetime(old_date)
        old_msg.headers = {}

        recent_msg = MagicMock()
        recent_msg.message_id = "recent-001"
        recent_msg.thread_id = "thread-002"
        recent_msg.sender = "recent@example.com"
        recent_msg.subject = "Recent email"
        recent_msg.body = "Recent content"
        recent_msg.snippet = "Recent content"
        recent_msg.timestamp = email.utils.format_datetime(recent_date)
        recent_msg.headers = {}

        mock_gmail_capability.fetch_unread = AsyncMock(return_value=[old_msg, recent_msg])

        results = await plugin._fetch_unread()

        # Only the recent email should be returned
        assert len(results) == 1
        assert results[0]["message_id"] == "recent-001"

        # Old email should have been marked as read
        mock_gmail_capability.mark_as_read.assert_called_once_with("old-001")

    @pytest.mark.asyncio
    async def test_negative_age_rejected(self, stal_plugin_context):
        """Negative max_email_age_hours is rejected — falls back to default 48h."""
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = -1
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._max_email_age_hours == 48  # Invalid → default 48h

    @pytest.mark.asyncio
    async def test_zero_age_rejected(self, stal_plugin_context):
        """Zero max_email_age_hours is rejected — falls back to default 48h."""
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = 0
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._max_email_age_hours == 48

    @pytest.mark.asyncio
    async def test_nan_age_rejected(self, stal_plugin_context):
        """NaN max_email_age_hours is rejected — falls back to default 48h."""
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = float("nan")
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._max_email_age_hours == 48

    @pytest.mark.asyncio
    async def test_inf_age_rejected(self, stal_plugin_context):
        """Infinity max_email_age_hours is rejected — falls back to default 48h."""
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = float("inf")
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._max_email_age_hours == 48

    @pytest.mark.asyncio
    async def test_string_age_rejected(self, stal_plugin_context):
        """Non-numeric max_email_age_hours is rejected — falls back to default 48h."""
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = "three"
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        assert plugin._max_email_age_hours == 48

    def test_future_date_treated_as_recent(self):
        """Email with future Date header is treated as recent (not blocked)."""
        from datetime import datetime, timezone, timedelta

        future = datetime.now(timezone.utc) + timedelta(hours=48)
        date_str = email.utils.format_datetime(future)
        msg = {"headers": {"Date": date_str}}
        # Future date → age_hours is negative → negative <= 3 → True
        assert EmailAgentPlugin._is_recent_email(msg, max_hours=3) is True

    @pytest.mark.asyncio
    async def test_fetch_unread_propagates_date_header(
        self, stal_plugin_context, mock_gmail_capability,
    ):
        """_fetch_unread() injects msg.timestamp as Date header for age filtering."""
        from datetime import datetime, timezone, timedelta

        recent_date = datetime.now(timezone.utc) - timedelta(hours=1)
        date_str = email.utils.format_datetime(recent_date)

        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = 5
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Create mock message with timestamp but no Date in headers
        msg = MagicMock()
        msg.message_id = "date-prop-001"
        msg.thread_id = "thread-001"
        msg.sender = "test@example.com"
        msg.subject = "Test"
        msg.body = "Body"
        msg.snippet = "Body"
        msg.timestamp = date_str
        msg.headers = {}  # No Date in headers dict

        mock_gmail_capability.fetch_unread = AsyncMock(return_value=[msg])

        results = await plugin._fetch_unread()

        # Should be included (Date injected from timestamp)
        assert len(results) == 1
        assert results[0]["headers"]["Date"] == date_str

    @pytest.mark.asyncio
    async def test_fetch_passes_since_days_to_gmail(
        self, stal_plugin_context, mock_gmail_capability,
    ):
        """_fetch_unread() passes since_days=1 when max_email_age_hours=5."""
        import math

        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = 5
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        mock_gmail_capability.fetch_unread = AsyncMock(return_value=[])

        await plugin._fetch_unread()

        # since_days = ceil(5/24) = 1
        expected_since_days = max(1, math.ceil(5 / 24))
        mock_gmail_capability.fetch_unread.assert_called_once_with(
            max_results=10, since_days=expected_since_days,
        )

    @pytest.mark.asyncio
    async def test_fetch_passes_default_since_days_when_no_age_filter(
        self, stal_plugin_context, mock_gmail_capability,
    ):
        """_fetch_unread() uses default 48h filter when max_email_age_hours is not configured."""
        stal_plugin_context.identity.raw_config["email_agent"].pop("max_email_age_hours", None)
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        mock_gmail_capability.fetch_unread = AsyncMock(return_value=[])

        await plugin._fetch_unread()

        # Default 48h → ceil(48/24) = 2 since_days
        mock_gmail_capability.fetch_unread.assert_called_once_with(
            max_results=10, since_days=2,
        )

    @pytest.mark.asyncio
    async def test_since_days_minimum_one(
        self, stal_plugin_context, mock_gmail_capability,
    ):
        """since_days is always at least 1, even for sub-24-hour age limits."""
        stal_plugin_context.identity.raw_config["email_agent"]["max_email_age_hours"] = 1
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        mock_gmail_capability.fetch_unread = AsyncMock(return_value=[])

        await plugin._fetch_unread()

        # ceil(1/24) = 1, but even ceil(0.5/24) = 1
        mock_gmail_capability.fetch_unread.assert_called_once_with(
            max_results=10, since_days=1,
        )


class TestDraftReplyNotification:
    """Test the draft reply notification feature (show_draft_replies)."""

    @pytest.mark.asyncio
    async def test_draft_reply_sent_after_notify(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """Draft reply sent as tracked TG message with approval instructions."""
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # LLM returns notification summary, then draft reply
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(content="Meeting request from colleague."),  # notification
            PipelineResult(content="Dear colleague, I'll check the calendar."),  # draft
        ])

        email_dict = {
            "sender": "colleague@example.com",
            "subject": "Meeting",
            "body": "Can we meet?",
            "snippet": "Can we meet?",
            "message_id": "msg-001",
            "thread_id": "thread-001",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="Important",
        )

        await plugin._execute_action(email_dict, classification)

        # send_notification_tracked called twice: notification + draft
        assert mock_telegram_notifier.send_notification_tracked.call_count == 2
        draft_text = mock_telegram_notifier.send_notification_tracked.call_args_list[1][0][0]
        assert "Draft reply to" in draft_text
        assert "Dear colleague" in draft_text
        assert 'Reply "skicka"' in draft_text

    @pytest.mark.asyncio
    async def test_draft_reply_not_sent_when_flag_false(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """No draft is sent when show_draft_replies is False (default)."""
        # Explicitly disable draft replies
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = False
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Important meeting request.")
        )

        email_dict = {
            "sender": "boss@example.com",
            "subject": "Urgent",
            "body": "Call me now.",
            "snippet": "Call me now.",
            "message_id": "msg-002",
            "thread_id": "thread-002",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="Urgent",
        )

        await plugin._execute_action(email_dict, classification)

        # Only notification tracked call, no draft
        assert mock_telegram_notifier.send_notification_tracked.call_count == 1

    @pytest.mark.asyncio
    async def test_draft_reply_skipped_on_llm_failure(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """Draft notification fails silently when LLM returns empty/blocked result."""
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(content="Meeting request."),  # notification succeeds
            PipelineResult(content="", blocked=True),    # draft LLM blocked
        ])

        email_dict = {
            "sender": "someone@example.com",
            "subject": "Hi",
            "body": "Hey there.",
            "snippet": "Hey there.",
            "message_id": "msg-003",
            "thread_id": "thread-003",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="test",
        )

        # Should not raise despite draft failure
        result = await plugin._execute_action(email_dict, classification)
        assert result == "notification_sent"
        # Only the notification tracked call, draft LLM was blocked
        assert mock_telegram_notifier.send_notification_tracked.call_count == 1

    @pytest.mark.asyncio
    async def test_draft_not_sent_when_notification_fails(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """Draft is not sent when the primary notification fails."""
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Notification fails (tracked returns None = failure)
        mock_telegram_notifier.send_notification_tracked = AsyncMock(return_value=None)

        stal_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Some notification content.")
        )

        email_dict = {
            "sender": "x@example.com",
            "subject": "Test",
            "body": "Test.",
            "snippet": "Test.",
            "message_id": "msg-004",
            "thread_id": "thread-004",
        }
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="test",
        )

        result = await plugin._execute_action(email_dict, classification)
        assert result == "notification_failed"
        # Only one send_notification_tracked call (the failed notification)
        assert mock_telegram_notifier.send_notification_tracked.call_count == 1

    @pytest.mark.asyncio
    async def test_show_draft_replies_loaded_from_config(self, stal_plugin_context):
        """show_draft_replies is loaded from config correctly."""
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        assert plugin._show_draft_replies is True

    @pytest.mark.asyncio
    async def test_show_draft_replies_default_false(self, stal_plugin_context):
        """show_draft_replies defaults to False when not configured."""
        stal_plugin_context.identity.raw_config["email_agent"].pop("show_draft_replies", None)
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()
        assert plugin._show_draft_replies is False

    @pytest.mark.asyncio
    async def test_draft_tracked_in_db(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """Draft reply is tracked in DB with body and thread ID."""
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # LLM calls in order: classify → notification → draft reply
        stal_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=[
            PipelineResult(
                content='{"intent": "notify", "confidence": 0.9, "reasoning": "Meeting request", "priority": "normal"}'
            ),
            PipelineResult(content="Important meeting notification."),
            PipelineResult(content="Dear colleague, sounds good."),
        ])

        email_dict = {
            "sender": "colleague@example.com",
            "subject": "Meeting",
            "body": "Can we meet?",
            "snippet": "Can we meet?",
            "message_id": "draft-track-001",
            "thread_id": "thread-draft-001",
            "headers": {},
        }

        await plugin._process_email(email_dict)

        # The draft should be tracked via the second send_notification_tracked call
        # First call (tg_id=42) is the notification, second call (tg_id=42) is the draft
        assert mock_telegram_notifier.send_notification_tracked.call_count == 2

        # Verify DB tracking: the draft's tg_message_id is 42 (mock returns 42)
        tracking = await plugin._db.get_notification_by_tg_id(42)
        assert tracking is not None


class TestDraftApproveToSend:
    """Test the approve-to-send flow for draft replies."""

    @pytest.mark.asyncio
    async def test_is_send_approval_accepts_keywords(self, stal_plugin_context):
        """_is_send_approval() recognizes all approval keywords."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        for word in ["skicka", "send", "ja", "yes", "ok", "approve", "godkänn", "\U0001f44d"]:
            assert plugin._is_send_approval(word) is True, f"Should accept '{word}'"
            assert plugin._is_send_approval(f" {word} ") is True, f"Should accept padded '{word}'"
            assert plugin._is_send_approval(word.upper()) is True, f"Should accept '{word.upper()}'"

    @pytest.mark.asyncio
    async def test_is_send_approval_rejects_non_keywords(self, stal_plugin_context):
        """_is_send_approval() rejects non-approval text."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        for word in ["maybe", "nej", "no", "wait", "later", "bra", "tack"]:
            assert plugin._is_send_approval(word) is False, f"Should reject '{word}'"

    @pytest.mark.asyncio
    async def test_send_approved_draft_happy_path(
        self, stal_plugin_context, mock_telegram_notifier, mock_gmail_capability,
    ):
        """_send_approved_draft() sends the reply via Gmail and confirms via TG."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        tracking = {
            "id": 1,
            "email_record_id": 10,
            "draft_reply_body": "Dear colleague, sounds good.",
            "email_from": "jens@example.com",  # In allowed_senders
            "email_subject": "Meeting",
            "original_email_thread_id": "thread-001",
            "gmail_message_id": "msg-001",
            "is_draft_reply": True,
        }

        await plugin._send_approved_draft(tracking, mock_telegram_notifier)

        mock_gmail_capability.send_reply.assert_called_once()
        call_kwargs = mock_gmail_capability.send_reply.call_args.kwargs
        assert call_kwargs["to"] == "jens@example.com"
        assert call_kwargs["subject"] == "Re: Meeting"
        assert call_kwargs["body"] == "Dear colleague, sounds good."
        assert call_kwargs["thread_id"] == "thread-001"
        assert call_kwargs["message_id"] == "msg-001"

        # Confirmation sent
        mock_telegram_notifier.send_notification.assert_called_once()
        confirm_text = mock_telegram_notifier.send_notification.call_args[0][0]
        assert "Reply sent to" in confirm_text

    @pytest.mark.asyncio
    async def test_send_approved_draft_blocked_for_disallowed_sender(
        self, stal_plugin_context, mock_telegram_notifier, mock_gmail_capability,
    ):
        """_send_approved_draft() rejects drafts to non-allowed senders."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        tracking = {
            "id": 1,
            "email_record_id": 10,
            "draft_reply_body": "Some reply text.",
            "email_from": "unknown@nobody.com",  # NOT in allowed_senders
            "email_subject": "Spam",
            "original_email_thread_id": "thread-001",
            "gmail_message_id": "msg-001",
            "is_draft_reply": True,
        }

        await plugin._send_approved_draft(tracking, mock_telegram_notifier)

        # Gmail should NOT be called
        mock_gmail_capability.send_reply.assert_not_called()
        # Error message sent to TG
        mock_telegram_notifier.send_notification.assert_called_once()
        error_text = mock_telegram_notifier.send_notification.call_args[0][0]
        assert "not in the allowed senders list" in error_text

    @pytest.mark.asyncio
    async def test_send_approved_draft_no_draft_body(
        self, stal_plugin_context, mock_telegram_notifier, mock_gmail_capability,
    ):
        """_send_approved_draft() handles missing draft body gracefully."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        tracking = {
            "id": 1,
            "email_record_id": 10,
            "draft_reply_body": "",  # Empty!
            "email_from": "jens@example.com",
            "email_subject": "Test",
        }

        await plugin._send_approved_draft(tracking, mock_telegram_notifier)

        mock_gmail_capability.send_reply.assert_not_called()
        mock_telegram_notifier.send_notification.assert_called_once()
        assert "draft text not found" in mock_telegram_notifier.send_notification.call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_approved_draft_gmail_unavailable(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """_send_approved_draft() handles missing Gmail capability."""
        # Remove gmail from capabilities
        stal_plugin_context.capabilities.pop("gmail", None)
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        tracking = {
            "id": 1,
            "email_record_id": 10,
            "draft_reply_body": "Reply text.",
            "email_from": "jens@example.com",
            "email_subject": "Test",
            "original_email_thread_id": "thread-001",
            "gmail_message_id": "msg-001",
        }

        await plugin._send_approved_draft(tracking, mock_telegram_notifier)

        mock_telegram_notifier.send_notification.assert_called_once()
        assert "Gmail not available" in mock_telegram_notifier.send_notification.call_args[0][0]

    @pytest.mark.asyncio
    async def test_check_tg_feedback_intercepts_draft_approval(
        self, stal_plugin_context, mock_telegram_notifier, mock_gmail_capability,
    ):
        """_check_tg_feedback() detects 'skicka' reply to a draft and sends email."""
        stal_plugin_context.identity.raw_config["email_agent"]["show_draft_replies"] = True
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Insert an email record
        record_id = await plugin._db.record_email(EmailRecord(
            gmail_message_id="gmail-001",
            email_from="jens@example.com",
            email_subject="Meeting next week?",
            classified_intent="notify",
            confidence=0.9,
            reasoning="Important",
            action_taken="notification_sent",
        ))

        # Track a draft notification
        await plugin._db.track_draft_notification(
            email_record_id=record_id,
            tg_message_id=100,
            tg_chat_id="12345",
            draft_reply_body="Dear Jens, I'll check the calendar.",
            original_thread_id="thread-meeting-001",
        )

        # Simulate TG update: principal replies "skicka" to the draft message
        from overblick.capabilities.communication.telegram_notifier import TelegramUpdate
        mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[
            TelegramUpdate(
                message_id=200,
                text="skicka",
                reply_to_message_id=100,
            ),
        ])

        await plugin._check_tg_feedback()

        # Gmail should have been called to send the reply
        mock_gmail_capability.send_reply.assert_called_once()
        call_kwargs = mock_gmail_capability.send_reply.call_args.kwargs
        assert call_kwargs["to"] == "jens@example.com"
        assert call_kwargs["subject"] == "Re: Meeting next week?"
        assert call_kwargs["body"] == "Dear Jens, I'll check the calendar."
        assert call_kwargs["thread_id"] == "thread-meeting-001"

        # Confirmation sent
        confirm_calls = [
            c for c in mock_telegram_notifier.send_notification.call_args_list
            if "Reply sent" in c[0][0]
        ]
        assert len(confirm_calls) == 1

    @pytest.mark.asyncio
    async def test_check_tg_feedback_non_approval_still_classified(
        self, stal_plugin_context, mock_telegram_notifier, mock_gmail_capability,
    ):
        """Non-approval replies to draft messages get classified as normal feedback."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Insert record + draft tracking
        record_id = await plugin._db.record_email(EmailRecord(
            gmail_message_id="gmail-002",
            email_from="jens@example.com",
            email_subject="Test",
            classified_intent="notify",
            confidence=0.9,
            reasoning="Test",
            action_taken="notification_sent",
        ))
        await plugin._db.track_draft_notification(
            email_record_id=record_id,
            tg_message_id=101,
            tg_chat_id="12345",
            draft_reply_body="Draft text.",
            original_thread_id="thread-001",
        )

        # Principal says something that is NOT an approval
        from overblick.capabilities.communication.telegram_notifier import TelegramUpdate
        mock_telegram_notifier.fetch_updates = AsyncMock(return_value=[
            TelegramUpdate(
                message_id=201,
                text="Bra att du flaggade det!",
                reply_to_message_id=101,
            ),
        ])

        # Mock LLM for feedback classification
        stal_plugin_context.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"sentiment": "positive", "learning": "", "should_acknowledge": false}'
        ))

        await plugin._check_tg_feedback()

        # Gmail should NOT be called (not an approval)
        mock_gmail_capability.send_reply.assert_not_called()


class TestDraftReplyDatabaseTracking:
    """Test database tracking of draft reply notifications."""

    @pytest.mark.asyncio
    async def test_track_and_retrieve_draft_notification(self, tmp_path):
        """Can track a draft notification and retrieve it by TG message ID."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_draft_tracking.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Insert email record first
        record_id = await db.record_email(EmailRecord(
            gmail_message_id="gmail-draft-001",
            email_from="test@example.com",
            email_subject="Draft Test",
            classified_intent="notify",
            confidence=0.9,
            reasoning="Test",
            action_taken="notification_sent",
        ))

        # Track draft notification
        tracking_id = await db.track_draft_notification(
            email_record_id=record_id,
            tg_message_id=200,
            tg_chat_id="12345",
            draft_reply_body="Dear colleague, happy to help.",
            original_thread_id="thread-draft-001",
        )
        assert tracking_id > 0

        # Retrieve by TG message ID
        result = await db.get_notification_by_tg_id(200)
        assert result is not None
        assert result["is_draft_reply"] == 1  # SQLite TRUE
        assert result["draft_reply_body"] == "Dear colleague, happy to help."
        assert result["original_email_thread_id"] == "thread-draft-001"
        assert result["email_from"] == "test@example.com"
        assert result["email_subject"] == "Draft Test"
        assert result["gmail_message_id"] == "gmail-draft-001"

        await db.close()

    @pytest.mark.asyncio
    async def test_regular_notification_has_draft_fields_false(self, tmp_path):
        """Regular (non-draft) notifications have is_draft_reply=FALSE."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_regular_tracking.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        record_id = await db.record_email(EmailRecord(
            email_from="test@example.com",
            email_subject="Regular Test",
            classified_intent="notify",
            confidence=0.9,
            reasoning="Test",
        ))

        await db.track_notification(
            email_record_id=record_id,
            tg_message_id=300,
            tg_chat_id="12345",
            notification_text="Regular notification",
        )

        result = await db.get_notification_by_tg_id(300)
        assert result is not None
        assert result["is_draft_reply"] == 0  # SQLite FALSE
        assert result["draft_reply_body"] == ""

        await db.close()

    @pytest.mark.asyncio
    async def test_migration_v7_applies_on_fresh_db(self, tmp_path):
        """Migration v7 applies cleanly on a fresh database."""
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        db_path = tmp_path / "test_migration_v7.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        db = EmailAgentDB(backend)
        await db.setup()

        # Verify migration count includes v7
        assert len(MIGRATIONS) == 7
        assert MIGRATIONS[6].version == 7
        assert MIGRATIONS[6].name == "draft_reply_tracking"

        await db.close()


# ---------------------------------------------------------------------------
# Reply rate limiting
# ---------------------------------------------------------------------------

class TestReplyRateLimiting:
    """Tests for per-domain reply rate limiting in EmailAgentPlugin."""

    @pytest.mark.asyncio
    async def test_rate_limit_not_triggered_below_threshold(self, stal_plugin_context):
        """Replies below the rate limit threshold are allowed."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Record 4 successful replies — still under limit of 5
        for _ in range(4):
            plugin._record_reply_sent("sender@example.com")
            assert plugin._is_reply_rate_limited("sender@example.com") is False

    @pytest.mark.asyncio
    async def test_rate_limit_triggered_at_threshold(self, stal_plugin_context):
        """Rate limit triggers when threshold is reached."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Record 5 successful replies
        for _ in range(5):
            plugin._record_reply_sent("sender@example.com")

        # 6th call should be rate limited
        assert plugin._is_reply_rate_limited("sender@example.com") is True

    @pytest.mark.asyncio
    async def test_rate_limit_per_domain(self, stal_plugin_context):
        """Rate limits are tracked per domain, not per sender."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Exhaust limit for example.com via recorded replies
        for _ in range(5):
            plugin._record_reply_sent("a@example.com")

        # Same domain, different sender — should be limited
        assert plugin._is_reply_rate_limited("b@example.com") is True

        # Different domain — should NOT be limited
        assert plugin._is_reply_rate_limited("c@other.com") is False

    @pytest.mark.asyncio
    async def test_rate_limit_expires_after_one_hour(self, stal_plugin_context):
        """Old timestamps are cleaned up after one hour."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Inject old timestamps (> 1 hour ago)
        old_time = time.time() - 3700  # 1 hour + 100 seconds ago
        plugin._reply_timestamps["example.com"] = [old_time] * 5

        # Should NOT be rate limited — old timestamps are cleaned
        assert plugin._is_reply_rate_limited("sender@example.com") is False

    @pytest.mark.asyncio
    async def test_rate_limit_handles_malformed_sender(self, stal_plugin_context):
        """Malformed sender address does not crash rate limiter."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # No @ sign — should not crash, just return False
        assert plugin._is_reply_rate_limited("no-at-sign") is False

    @pytest.mark.asyncio
    async def test_rate_limited_reply_falls_back_to_notification(
        self, stal_plugin_context, mock_telegram_notifier,
    ):
        """When rate limited, reply action falls back to notification."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Exhaust rate limit for example.com
        plugin._reply_timestamps["example.com"] = [time.time()] * 5

        email = {
            "sender": "jens@example.com",
            "subject": "Another meeting",
            "body": "Can we meet again?",
            "snippet": "Can we meet again?",
            "message_id": "rate-limit-test",
            "thread_id": "thread-rate-limit",
        }

        classification = EmailClassification(
            intent=EmailIntent.REPLY,
            confidence=0.95,
            reasoning="Meeting request",
            priority="normal",
        )

        result = await plugin._execute_action(email, classification)
        assert result == "reply_rate_limited_notify_fallback"
