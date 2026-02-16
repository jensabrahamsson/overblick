"""
RSS plugin tests.

Tests cover:
- FeedItem and FeedConfig dataclasses
- Plugin lifecycle (setup, tick, teardown)
- Feed configuration loading
- Status reporting
- Edge cases (empty feeds, missing config)
"""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.identities import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.core.plugin_base import PluginContext
from overblick.plugins.rss.plugin import FeedConfig, FeedItem, RSSPlugin


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestFeedItem:
    """Test FeedItem dataclass."""

    def test_construction(self):
        item = FeedItem(title="Test Article", link="https://example.com/1")
        assert item.title == "Test Article"
        assert item.link == "https://example.com/1"
        assert item.summary == ""
        assert item.guid == ""
        assert item.timestamp > 0

    def test_with_all_fields(self):
        item = FeedItem(
            title="Full Article",
            link="https://example.com/2",
            summary="A summary",
            published="2025-01-15T10:00:00Z",
            feed_url="https://example.com/feed.xml",
            guid="guid-123",
            timestamp=1700000000.0,
        )
        assert item.guid == "guid-123"
        assert item.published == "2025-01-15T10:00:00Z"
        assert item.feed_url == "https://example.com/feed.xml"


class TestFeedConfig:
    """Test FeedConfig dataclass."""

    def test_construction(self):
        config = FeedConfig(url="https://example.com/feed.xml")
        assert config.url == "https://example.com/feed.xml"
        assert config.name == ""
        assert config.poll_interval_minutes == 30
        assert config.keywords == []
        assert config.enabled is True

    def test_custom_config(self):
        config = FeedConfig(
            url="https://example.com/tech.rss",
            name="Tech News",
            poll_interval_minutes=15,
            keywords=["AI", "open source"],
            enabled=False,
        )
        assert config.name == "Tech News"
        assert config.poll_interval_minutes == 15
        assert len(config.keywords) == 2
        assert config.enabled is False


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestRSSLifecycle:
    """Test plugin lifecycle."""

    @pytest.mark.asyncio
    async def test_setup_loads_feeds(self, rss_plugin):
        assert len(rss_plugin._feeds) == 2

    @pytest.mark.asyncio
    async def test_setup_loads_feed_names(self, rss_plugin):
        names = {f.name for f in rss_plugin._feeds}
        assert "Nature News" in names
        assert "Tech Feed" in names

    @pytest.mark.asyncio
    async def test_setup_loads_feed_urls(self, rss_plugin):
        urls = {f.url for f in rss_plugin._feeds}
        assert "https://example.com/nature.rss" in urls
        assert "https://example.com/tech.rss" in urls

    @pytest.mark.asyncio
    async def test_setup_loads_custom_keywords(self, rss_plugin):
        nature_feed = [f for f in rss_plugin._feeds if f.name == "Nature News"][0]
        assert nature_feed.keywords == ["forest", "ecology"]

    @pytest.mark.asyncio
    async def test_setup_inherits_interest_keywords(self, rss_plugin):
        """Feed without explicit keywords inherits from identity interest_keywords."""
        tech_feed = [f for f in rss_plugin._feeds if f.name == "Tech Feed"][0]
        assert "ecology" in tech_feed.keywords
        assert "forests" in tech_feed.keywords

    @pytest.mark.asyncio
    async def test_setup_loads_custom_interval(self, rss_plugin):
        nature_feed = [f for f in rss_plugin._feeds if f.name == "Nature News"][0]
        assert nature_feed.poll_interval_minutes == 15

    @pytest.mark.asyncio
    async def test_setup_default_interval(self, rss_plugin):
        tech_feed = [f for f in rss_plugin._feeds if f.name == "Tech Feed"][0]
        assert tech_feed.poll_interval_minutes == 30

    @pytest.mark.asyncio
    async def test_setup_logs_audit_event(self, rss_plugin):
        rss_plugin.ctx.audit_log.log.assert_called()
        call_kwargs = rss_plugin.ctx.audit_log.log.call_args[1]
        assert call_kwargs["action"] == "plugin_setup"
        assert call_kwargs["details"]["feed_count"] == 2

    @pytest.mark.asyncio
    async def test_tick_is_noop_shell(self, rss_plugin):
        await rss_plugin.tick()

    @pytest.mark.asyncio
    async def test_teardown_completes(self, rss_context):
        plugin = RSSPlugin(rss_context)
        await plugin.setup()
        await plugin.teardown()


# ---------------------------------------------------------------------------
# Configuration edge cases
# ---------------------------------------------------------------------------

class TestRSSConfig:
    """Test configuration edge cases."""

    @pytest.mark.asyncio
    async def test_empty_feeds_list(self, rss_context):
        rss_context.identity = Identity(
            name="birch",
            display_name="Birch",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={"rss": {"feeds": []}},
        )
        plugin = RSSPlugin(rss_context)
        await plugin.setup()
        assert len(plugin._feeds) == 0

    @pytest.mark.asyncio
    async def test_no_rss_config(self, rss_context):
        """Plugin handles missing rss config section."""
        rss_context.identity = Identity(
            name="birch",
            display_name="Birch",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={},
        )
        plugin = RSSPlugin(rss_context)
        await plugin.setup()
        assert len(plugin._feeds) == 0


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------

class TestRSSStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_status_structure(self, rss_plugin):
        status = rss_plugin.get_status()
        assert status["plugin"] == "rss"
        assert status["identity"] == "birch"
        assert status["feeds_configured"] == 2
        assert status["items_processed"] == 0
        assert status["seen_items"] == 0
        assert status["errors"] == 0

    @pytest.mark.asyncio
    async def test_status_tracks_seen_items(self, rss_plugin):
        rss_plugin._seen_guids = {"guid-1", "guid-2", "guid-3"}
        rss_plugin._items_processed = 5
        status = rss_plugin.get_status()
        assert status["seen_items"] == 3
        assert status["items_processed"] == 5

    @pytest.mark.asyncio
    async def test_plugin_name(self, rss_plugin):
        assert rss_plugin.name == "rss"
