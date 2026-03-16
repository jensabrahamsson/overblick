"""
Coverage tests for email_agent database module.

Covers uncovered lines:
- 280: get_learnings with learning_type filter
- 355: get_stats with no rows (None row)
- 560: purge_gdpr_data with scrubbed > 0
"""

import pytest

from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.plugins.email_agent.database import EmailAgentDB
from overblick.plugins.email_agent.models import AgentLearning, EmailRecord


@pytest.fixture
async def db(tmp_path):
    config = DatabaseConfig(sqlite_path=str(tmp_path / "test.db"))
    backend = SQLiteBackend(config)
    email_db = EmailAgentDB(backend)
    await email_db.setup()
    yield email_db
    await email_db.close()


class TestGetLearningsWithType:
    @pytest.mark.asyncio
    async def test_should_filter_by_learning_type(self, db):
        """Line 280: filtered query when learning_type is provided."""
        # Store learnings of different types
        await db.store_learning(
            AgentLearning(learning_type="tone", content="Be warm", source="feedback")
        )
        await db.store_learning(
            AgentLearning(learning_type="policy", content="Auto-ignore spam", source="config")
        )

        # Fetch only tone learnings
        tone_learnings = await db.get_learnings(learning_type="tone")
        assert len(tone_learnings) == 1
        assert tone_learnings[0].learning_type == "tone"

    @pytest.mark.asyncio
    async def test_should_return_all_without_type(self, db):
        """Unfiltered query when learning_type is empty."""
        await db.store_learning(
            AgentLearning(learning_type="tone", content="Be warm", source="feedback")
        )
        await db.store_learning(
            AgentLearning(learning_type="policy", content="Auto-ignore", source="config")
        )

        all_learnings = await db.get_learnings()
        assert len(all_learnings) == 2


class TestGetStatsNoRows:
    @pytest.mark.asyncio
    async def test_should_return_stats_from_empty_db(self, db):
        """get_stats on empty DB returns zeroes."""
        stats = await db.get_stats()
        assert stats["emails_processed"] == 0
        assert stats["emails_replied"] == 0

    @pytest.mark.asyncio
    async def test_should_return_defaults_when_row_is_none(self, db):
        """Line 355: get_stats returns defaults when fetch_one returns None."""
        from unittest.mock import AsyncMock, patch

        with patch.object(db._db, "fetch_one", new_callable=AsyncMock, return_value=None):
            stats = await db.get_stats()
            assert stats["emails_processed"] == 0
            assert stats["emails_replied"] == 0
            assert stats["notifications_sent"] == 0
            assert stats["boss_consultations"] == 0


class TestPurgeGdprData:
    @pytest.mark.asyncio
    async def test_should_purge_old_records(self, db):
        """Line 560: purge_gdpr_data with scrubbed > 0 logs info."""
        # Insert a record
        record = EmailRecord(
            email_from="test@example.com",
            email_subject="Old email",
            email_snippet="This is PII",
            classified_intent="ignore",
            confidence=0.9,
            reasoning="Spam",
        )
        record_id = await db.record_email(record)

        # Manually backdate the record
        await db._db.execute(
            "UPDATE email_records SET created_at = datetime('now', '-60 days') WHERE id = ?",
            (record_id,),
        )

        scrubbed = await db.purge_gdpr_data(retention_days=30)
        assert scrubbed == 1

        # Verify the data was purged
        rows = await db.get_recent_emails()
        assert rows[0].email_snippet == "[GDPR purged]"
        assert rows[0].reasoning == "[GDPR purged]"

    @pytest.mark.asyncio
    async def test_should_not_purge_recent_records(self, db):
        """No purging for records within retention period."""
        record = EmailRecord(
            email_from="test@example.com",
            email_subject="Recent email",
            email_snippet="Fresh data",
            classified_intent="reply",
            confidence=0.95,
            reasoning="Looks important",
        )
        await db.record_email(record)

        scrubbed = await db.purge_gdpr_data(retention_days=30)
        assert scrubbed == 0
