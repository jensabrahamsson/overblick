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
    notification_prompt,
    reply_prompt,
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
        assert "jens.abrahamsson@wirelesscar.com" in plugin._allowed_senders

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

        assert plugin._is_allowed_sender("jens.abrahamsson@wirelesscar.com") is True

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
        """_send_notification() sends via Telegram notifier."""
        plugin = EmailAgentPlugin(stal_plugin_context)
        await plugin.setup()

        # Mock LLM for notification summary
        stal_plugin_context.llm_pipeline.chat.return_value = PipelineResult(
            content="Meeting request from colleague about Q1 results."
        )

        email = {"sender": "colleague@wirelesscar.com", "subject": "Meeting", "body": "Can we meet?"}
        classification = EmailClassification(
            intent=EmailIntent.NOTIFY, confidence=0.9, reasoning="Important"
        )

        result = await plugin._send_notification(email, classification)

        assert result is True
        mock_telegram_notifier.send_notification.assert_called_once()

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
            "sender": "colleague@wirelesscar.com",
            "subject": "Meeting next Tuesday?",
            "body": "Can we schedule a meeting?",
            "thread_id": "thread-001",
            "message_id": "msg-001",
        }

        result = await plugin._send_reply(email)

        assert result is True
        mock_gmail_capability.send_reply.assert_called_once()
        call_kwargs = mock_gmail_capability.send_reply.call_args.kwargs
        assert call_kwargs["to"] == "colleague@wirelesscar.com"
        assert call_kwargs["subject"] == "Re: Meeting next Tuesday?"
        assert call_kwargs["thread_id"] == "thread-001"
        assert call_kwargs["message_id"] == "msg-001"
        # Verify mark_as_read was called after successful reply
        mock_gmail_capability.mark_as_read.assert_called_once_with("msg-001")

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
            "sender": "unknown@wirelesscar.com",
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
