"""Tests for the IRC dashboard routes."""

import pytest
from unittest.mock import MagicMock

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.services.irc import IRCService
from overblick.plugins.irc.models import IRCConversation, IRCTurn, ConversationState


def _make_conversations():
    """Create standard test conversation data."""
    return [
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


def _make_irc_service(conversations=None, current=None):
    """Create a mock IRC service with test data."""
    svc = MagicMock(spec=IRCService)

    if conversations is None:
        conversations = _make_conversations()

    conv_dicts = [c.model_dump() for c in conversations]
    svc.get_conversations.return_value = conv_dicts
    svc.get_current_conversation.return_value = (
        current.model_dump() if current else
        conv_dicts[0] if conv_dicts else None
    )
    svc.has_data.return_value = len(conversations) > 0

    def get_conv(cid):
        for c in conversations:
            if c.id == cid:
                return c.model_dump()
        return None

    svc.get_conversation.side_effect = get_conv
    return svc


def _inject_irc_service(app, svc):
    """Inject a mock IRC service into the app state."""
    app.state.irc_service = svc


class TestIRCPage:
    @pytest.mark.asyncio
    async def test_irc_renders_authenticated(self, client, app, session_cookie):
        """Authenticated user sees the IRC page."""
        _inject_irc_service(app, _make_irc_service())
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
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "Can AI have consciousness?" in resp.text

    @pytest.mark.asyncio
    async def test_irc_shows_participants(self, client, app, session_cookie):
        """Page shows participant names."""
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "anomal" in resp.text
        assert "cherry" in resp.text

    @pytest.mark.asyncio
    async def test_irc_shows_turns(self, client, app, session_cookie):
        """Page shows conversation turns."""
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "I think therefore I am." in resp.text
        assert "But do you feel?" in resp.text

    @pytest.mark.asyncio
    async def test_irc_select_conversation(self, client, app, session_cookie):
        """Selecting a specific conversation by ID."""
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc?id=irc-test2", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Is democracy in crisis?" in resp.text

    @pytest.mark.asyncio
    async def test_irc_sidebar_lists_conversations(self, client, app, session_cookie):
        """Sidebar shows both conversations."""
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "irc-test1" in resp.text
        assert "irc-test2" in resp.text


class TestIRCEmptyState:
    @pytest.mark.asyncio
    async def test_irc_no_conversations_redirects(self, client, app, session_cookie):
        """When no conversations exist, redirect to dashboard."""
        svc = _make_irc_service(conversations=[])
        svc.has_data.return_value = False
        _inject_irc_service(app, svc)
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value}, follow_redirects=False)
        assert resp.status_code == 302

    @pytest.mark.asyncio
    async def test_irc_no_service_redirects(self, client, app, session_cookie):
        """When IRC service is not available, redirect to dashboard."""
        # Remove irc_service from app state
        if hasattr(app.state, "irc_service"):
            delattr(app.state, "irc_service")
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value}, follow_redirects=False)
        assert resp.status_code == 302
        assert "irc_not_available" in resp.headers.get("location", "")


class TestIRCFeedPartial:
    @pytest.mark.asyncio
    async def test_feed_returns_html(self, client, app, session_cookie):
        """Feed partial returns HTML fragment with turns."""
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc/feed?id=irc-test1", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Anomal" in resp.text
        assert "I think therefore I am." in resp.text

    @pytest.mark.asyncio
    async def test_feed_shows_completed_status(self, client, app, session_cookie):
        """Feed shows completion message for finished conversations."""
        _inject_irc_service(app, _make_irc_service())
        cookie_value, _ = session_cookie
        resp = await client.get("/irc/feed?id=irc-test2", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        # Completed conversation with 0 turns
        assert "Waiting for first message" in resp.text or "completed" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_feed_no_conversation(self, client, app, session_cookie):
        """Feed shows placeholder when no conversation found."""
        _inject_irc_service(app, _make_irc_service(conversations=[]))
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
        _inject_irc_service(app, _make_irc_service(conversations=[paused]))
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        assert "Paused" in resp.text or "paused" in resp.text

    @pytest.mark.asyncio
    async def test_active_conversation_has_htmx_polling(self, client, app, session_cookie):
        """Active conversation feed has htmx polling attributes."""
        _inject_irc_service(app, _make_irc_service())
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
        _inject_irc_service(app, _make_irc_service(conversations=[completed]))
        cookie_value, _ = session_cookie
        resp = await client.get("/irc", cookies={SESSION_COOKIE: cookie_value})
        # htmx polling should NOT be present for completed conversations
        assert 'hx-trigger="every 3s"' not in resp.text


class TestIRCService:
    """Unit tests for IRCService file-based data access."""

    def test_get_conversations_empty_without_file(self, tmp_path):
        svc = IRCService(tmp_path)
        assert svc.get_conversations() == []

    def test_get_conversations_reads_json(self, tmp_path):
        import json
        data_dir = tmp_path / "data" / "anomal" / "irc"
        data_dir.mkdir(parents=True)
        convs = [
            {"id": "c1", "topic": "Topic 1", "participants": ["a"], "state": "active", "updated_at": 2000.0},
            {"id": "c2", "topic": "Topic 2", "participants": ["b"], "state": "completed", "updated_at": 1000.0},
        ]
        (data_dir / "conversations.json").write_text(json.dumps(convs))

        svc = IRCService(tmp_path)
        result = svc.get_conversations()
        assert len(result) == 2
        # Sorted by updated_at descending
        assert result[0]["id"] == "c1"

    def test_get_conversation_by_id(self, tmp_path):
        import json
        data_dir = tmp_path / "data" / "anomal" / "irc"
        data_dir.mkdir(parents=True)
        convs = [
            {"id": "c1", "topic": "Topic 1", "participants": [], "updated_at": 1000.0},
            {"id": "c2", "topic": "Topic 2", "participants": [], "updated_at": 2000.0},
        ]
        (data_dir / "conversations.json").write_text(json.dumps(convs))

        svc = IRCService(tmp_path)
        result = svc.get_conversation("c2")
        assert result is not None
        assert result["topic"] == "Topic 2"

    def test_get_conversation_not_found(self, tmp_path):
        svc = IRCService(tmp_path)
        assert svc.get_conversation("nonexistent") is None

    def test_get_current_conversation_active(self, tmp_path):
        import json
        data_dir = tmp_path / "data" / "anomal" / "irc"
        data_dir.mkdir(parents=True)
        convs = [
            {"id": "c1", "topic": "T1", "participants": [], "state": "completed", "updated_at": 2000.0},
            {"id": "c2", "topic": "T2", "participants": [], "state": "active", "updated_at": 1000.0},
        ]
        (data_dir / "conversations.json").write_text(json.dumps(convs))

        svc = IRCService(tmp_path)
        result = svc.get_current_conversation()
        assert result is not None
        assert result["id"] == "c2"

    def test_get_current_conversation_fallback(self, tmp_path):
        import json
        data_dir = tmp_path / "data" / "anomal" / "irc"
        data_dir.mkdir(parents=True)
        convs = [
            {"id": "c1", "topic": "T1", "participants": [], "state": "completed", "updated_at": 2000.0},
        ]
        (data_dir / "conversations.json").write_text(json.dumps(convs))

        svc = IRCService(tmp_path)
        result = svc.get_current_conversation()
        assert result is not None
        assert result["id"] == "c1"


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
