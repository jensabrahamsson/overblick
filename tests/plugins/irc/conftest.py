"""Test fixtures for the IRC plugin."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from overblick.core.plugin_base import PluginContext
from overblick.plugins.irc.plugin import IRCPlugin


@pytest.fixture
def mock_ctx(tmp_path):
    """Create a mock PluginContext for IRC plugin testing."""
    ctx = MagicMock(spec=PluginContext)
    ctx.data_dir = tmp_path / "data"
    ctx.data_dir.mkdir(parents=True, exist_ok=True)
    ctx.log_dir = tmp_path / "logs"
    ctx.log_dir.mkdir(parents=True, exist_ok=True)

    ctx.identity_name = "supervisor"
    ctx.event_bus = AsyncMock()
    ctx.event_bus.emit = AsyncMock()
    ctx.scheduler = MagicMock()
    ctx.quiet_hours_checker = MagicMock()
    ctx.quiet_hours_checker.is_quiet_hours.return_value = False
    ctx.llm_pipeline = AsyncMock()
    ctx.llm_pipeline.generate = AsyncMock(return_value={
        "content": "This is a test response from the IRC conversation.",
    })
    ctx.audit_log = MagicMock()
    ctx.audit_log.log = MagicMock()

    return ctx


@pytest.fixture
def irc_plugin(mock_ctx):
    """Create an IRCPlugin instance with mock context."""
    return IRCPlugin(mock_ctx)


@pytest.fixture
def mock_identities():
    """Create mock identity objects for testing."""
    identities = []
    for name, display_name, keywords in [
        ("anomal", "Anomal", ["AI", "consciousness", "philosophy", "technology"]),
        ("cherry", "Cherry", ["relationships", "psychology", "art", "dreams"]),
        ("bjork", "Björk", ["philosophy", "stoicism", "psychology", "meaning"]),
        ("rost", "Rost", ["crypto", "economics", "politics", "technology"]),
        ("natt", "Natt", ["dreams", "unconscious", "psychology", "loneliness"]),
    ]:
        identity = MagicMock()
        identity.name = name
        identity.display_name = display_name
        identity.interest_keywords = keywords
        identity.interests = {}
        identities.append(identity)
    return identities
