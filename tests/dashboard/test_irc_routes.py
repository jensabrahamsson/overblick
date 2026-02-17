"""Tests for the IRC dashboard routes."""

import pytest
from unittest.mock import MagicMock, patch

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.plugins.irc.models import IRCConversation, IRCTurn, ConversationState


def _make_irc_plugin(conversations=None, current=None):
    """Create a mock IRC plugin with test data."""
    plugin = MagicMock()

    if conversations is None:
        conversations = [
            IRCConversation(
                id="irc-test1",
                topic="Can AI have consciousness?",
                topic_description="Exploring the boundaries between computation and awareness",
                participants=["anomal", "cherry", "bjork"],
                turns=[
                    IRCTurn(identity="anomal", display_name="Anomal", content="I think therefore I am.", turn_number=0),
                    IRCTurn(identity="cherry", display_name="Cherry", content="But do you feel?", turn_number=1),
                ],
                state=ConversationState.ACTIVE,
                updated_at=2000.0,
            ),
            IRCConversation(
                id="irc-test2",
                topic="Is democracy in crisis?",
                participants=["rost", "natt"],
                state=ConversationState.COMPLETED,
                updated_at=1000.0,
            ),
        ]

    plugin.get_conversations.return_value = [c.model_dump() for c in conversations]
    plugin.get_current_conversation.return_value = (
        current.model_dump() if current else
        conversations[0].model_dump() if conversations else None
    )

    def get_conv(cid):
        for c in conversations:
            if c.id == cid:
                return c.model_dump()
        return None

    plugin.get_conversation.side_effect = get_conv
    return plugin


def _inject_irc_plugin(app, plugin):
    """Inject a mock IRC plugin into the app state."""
    registry = MagicMock()
    registry.irc = plugin
    app.state.plugin_registry = registry


class TestIRCPage:
    @pytest.mark.asyncio
    async def test_irc_renders_authenticated(self, client, app, session_cookie):
        """Authenticated user sees the IRC page."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "IRC" in resp.text

    @pytest.mark.asyncio
    async def test_irc_redirects_unauthenticated(self, client):
        """Unauthenticated user is redirected to login."""
        resp = await client.get("/irc", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_irc_shows_topic(self, client, app, session_cookie):
        """Page displays the selected conversation topic."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "Can AI have consciousness?" in resp.text

    @pytest.mark.asyncio
    async def test_irc_shows_participants(self, client, app, session_cookie):
        """Page shows participant names."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "anomal" in resp.text
        assert "cherry" in resp.text

    @pytest.mark.asyncio
    async def test_irc_shows_turns(self, client, app, session_cookie):
        """Page shows conversation turns."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "I think therefore I am." in resp.text
        assert "But do you feel?" in resp.text

    @pytest.mark.asyncio
    async def test_irc_select_conversation(self, client, app, session_cookie):
        """Selecting a specific conversation by ID."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc?id=irc-test2", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Is democracy in crisis?" in resp.text

    @pytest.mark.asyncio
    async def test_irc_sidebar_lists_conversations(self, client, app, session_cookie):
        """Sidebar shows both conversations."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "irc-test1" in resp.text
        assert "irc-test2" in resp.text


class TestIRCEmptyState:
    @pytest.mark.asyncio
    async def test_irc_no_conversations(self, client, app, session_cookie):
        """Empty state when no conversations exist."""
        _inject_irc_plugin(app, _make_irc_plugin(conversations=[]))
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "No IRC conversations yet" in resp.text

    @pytest.mark.asyncio
    async def test_irc_no_plugin(self, client, app, session_cookie):
        """Graceful degradation when IRC plugin is not available."""
        # No plugin_registry set at all
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "No IRC conversations yet" in resp.text


class TestIRCFeedPartial:
    @pytest.mark.asyncio
    async def test_feed_returns_html(self, client, app, session_cookie):
        """Feed partial returns HTML fragment with turns."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc/feed?id=irc-test1", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Anomal" in resp.text
        assert "I think therefore I am." in resp.text

    @pytest.mark.asyncio
    async def test_feed_shows_completed_status(self, client, app, session_cookie):
        """Feed shows completion message for finished conversations."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc/feed?id=irc-test2", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        # Completed conversation with 0 turns
        assert "Waiting for first message" in resp.text or "completed" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_feed_no_conversation(self, client, app, session_cookie):
        """Feed shows placeholder when no conversation found."""
        _inject_irc_plugin(app, _make_irc_plugin(conversations=[]))
        cookie_value, _ = session_cookie
        resp = await client.get("/irc/feed?id=nonexistent", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Select a conversation" in resp.text


class TestIRCConversationStates:
    @pytest.mark.asyncio
    async def test_paused_conversation_shows_warning(self, client, app, session_cookie):
        """Paused conversation shows load warning."""
        paused = IRCConversation(
            id="irc-paused",
            topic="Paused Topic",
            participants=["anomal"],
            state=ConversationState.PAUSED,
            turns=[IRCTurn(identity="anomal", display_name="Anomal", content="Before pause", turn_number=0)],
        )
        _inject_irc_plugin(app, _make_irc_plugin(conversations=[paused]))
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "Paused" in resp.text or "paused" in resp.text

    @pytest.mark.asyncio
    async def test_active_conversation_has_htmx_polling(self, client, app, session_cookie):
        """Active conversation feed has htmx polling attributes."""
        _inject_irc_plugin(app, _make_irc_plugin())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "hx-get" in resp.text
        assert "every 3s" in resp.text

    @pytest.mark.asyncio
    async def test_completed_conversation_no_polling(self, client, app, session_cookie):
        """Completed conversation does not poll."""
        completed = IRCConversation(
            id="irc-done",
            topic="Done Topic",
            participants=["anomal"],
            state=ConversationState.COMPLETED,
        )
        _inject_irc_plugin(app, _make_irc_plugin(conversations=[completed]))
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        # htmx polling should NOT be present for completed conversations
        assert 'hx-trigger="every 3s"' not in resp.text


class TestIdentityColor:
    def test_consistent_colors(self):
        from overblick.dashboard.routes.irc import _identity_color
        color1 = _identity_color("anomal")
        color2 = _identity_color("anomal")
        assert color1 == color2

    def test_different_identities_get_different_colors(self):
        from overblick.dashboard.routes.irc import _identity_color
        color_anomal = _identity_color("anomal")
        color_cherry = _identity_color("cherry")
        assert color_anomal != color_cherry

    def test_color_format(self):
        from overblick.dashboard.routes.irc import _identity_color
        color = _identity_color("test")
        assert color.startswith("hsl(")
        assert color.endswith(")")
