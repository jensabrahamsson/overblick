"""
Tests for the email_agent plugin — lifecycle, classification, database, IPC.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.email_agent.database import EmailAgentDB, MIGRATIONS
from overblick.plugins.email_agent.models import (
    AgentGoal,
    AgentLearning,
    AgentState,
    EmailClassification,
    EmailIntent,
    EmailRecord,
)
from overblick.plugins.email_agent.plugin import EmailAgentPlugin
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
        result = plugin._parse_classification(raw)

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
        result = plugin._parse_classification(raw)

        assert result is not None
        assert result.intent == EmailIntent.IGNORE

    @pytest.mark.asyncio
    async def test_parse_invalid_json_returns_none(self, stal_plugin_context):
        """Returns None for unparseable content."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        result = plugin._parse_classification("This is not JSON at all")
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_classification_ask_boss(self, stal_plugin_context):
        """Parses ask_boss intent correctly."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        raw = '{"intent": "ask_boss", "confidence": 0.4, "reasoning": "Uncertain", "priority": "high"}'
        result = plugin._parse_classification(raw)

        assert result is not None
        assert result.intent == EmailIntent.ASK_BOSS
        assert result.confidence == 0.4


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

        result = await plugin._send_reply(email)

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
            allowed_senders="test@example.com",
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

        result = await plugin._request_research("What is the weather?")

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

        result = await plugin._request_research("test query")
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
