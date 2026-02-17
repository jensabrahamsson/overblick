"""
Integration tests for Moltbook plugin.

Verifies end-to-end flows:
- Client → Plugin → Status JSON → Dashboard service
- Suspension detection → Error propagation → Dashboard display
- Account status lifecycle: unknown → active → suspended → active
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from overblick.plugins.moltbook.client import (
    MoltbookClient, MoltbookError, SuspensionError, AuthenticationError,
)
from overblick.plugins.moltbook.models import (
    Agent, Post, Comment, Submolt, Conversation, DMRequest, Message,
)
from overblick.dashboard.services.system import SystemService


# ── Status Lifecycle Integration ──────────────────────────────────────────


class TestAccountStatusLifecycle:
    """Test the full lifecycle: unknown → active → suspended → active."""

    def test_initial_unknown_status(self):
        client = MoltbookClient(api_key="key", identity_name="anomal")
        status = client.get_account_status()
        assert status["status"] == "unknown"
        assert status["identity"] == "anomal"
        assert status["updated_at"] == ""

    def test_active_after_successful_api_call(self):
        client = MoltbookClient(api_key="key", identity_name="anomal")
        client._update_account_status("active")
        status = client.get_account_status()
        assert status["status"] == "active"
        assert status["updated_at"] != ""

    def test_suspended_updates_status(self):
        client = MoltbookClient(api_key="key", identity_name="cherry")
        client._update_account_status("active")
        assert client.get_account_status()["status"] == "active"

        client._update_account_status("suspended", "Banned for spam")
        status = client.get_account_status()
        assert status["status"] == "suspended"
        assert status["detail"] == "Banned for spam"

    def test_recovery_from_suspended_to_active(self):
        client = MoltbookClient(api_key="key", identity_name="anomal")
        client._update_account_status("suspended", "Temporary ban")
        assert client.get_account_status()["status"] == "suspended"

        client._update_account_status("active")
        status = client.get_account_status()
        assert status["status"] == "active"
        assert status["detail"] == ""


# ── Client → Status File → Dashboard Service ─────────────────────────────


class TestStatusPersistenceFlow:
    """Test the full flow from client status to dashboard consumption."""

    def test_client_status_to_json_to_dashboard(self, tmp_path):
        """Client writes status → Dashboard reads it."""
        # Step 1: Client generates status
        client = MoltbookClient(api_key="key", identity_name="anomal")
        client._update_account_status("active")

        # Step 2: Write status to file (simulating plugin._persist_status)
        identity_dir = tmp_path / "data" / "anomal"
        identity_dir.mkdir(parents=True)
        status_file = identity_dir / "moltbook_status.json"
        status_file.write_text(json.dumps(client.get_account_status()))

        # Step 3: Dashboard service reads it
        svc = SystemService(tmp_path)
        statuses = svc.get_moltbook_statuses()

        assert len(statuses) == 1
        assert statuses[0]["status"] == "active"
        assert statuses[0]["identity"] == "anomal"

    def test_suspension_flow_end_to_end(self, tmp_path):
        """Suspended status flows from client to dashboard."""
        client = MoltbookClient(api_key="key", identity_name="cherry")
        client._update_account_status("suspended", "Caught spamming")

        identity_dir = tmp_path / "data" / "cherry"
        identity_dir.mkdir(parents=True)
        (identity_dir / "moltbook_status.json").write_text(
            json.dumps(client.get_account_status()),
        )

        svc = SystemService(tmp_path)
        statuses = svc.get_moltbook_statuses()

        assert len(statuses) == 1
        assert statuses[0]["status"] == "suspended"
        assert statuses[0]["detail"] == "Caught spamming"

    def test_multiple_identities_status_aggregation(self, tmp_path):
        """Dashboard aggregates statuses from multiple identities."""
        for name, status, detail in [
            ("anomal", "active", ""),
            ("cherry", "suspended", "Ban"),
            ("blixt", "unknown", ""),
        ]:
            client = MoltbookClient(api_key="key", identity_name=name)
            client._update_account_status(status, detail)
            d = tmp_path / "data" / name
            d.mkdir(parents=True)
            (d / "moltbook_status.json").write_text(
                json.dumps(client.get_account_status()),
            )

        svc = SystemService(tmp_path)
        statuses = svc.get_moltbook_statuses()
        assert len(statuses) == 3

        status_map = {s["identity"]: s["status"] for s in statuses}
        assert status_map["anomal"] == "active"
        assert status_map["cherry"] == "suspended"
        assert status_map["blixt"] == "unknown"


# ── SuspensionError Integration ───────────────────────────────────────────


class TestSuspensionDetection:
    """Test suspension detection in the full _request() flow."""

    @pytest.fixture
    def live_client(self):
        """Client with real internals for _request() testing."""
        return MoltbookClient(
            api_key="test-key",
            identity_name="testbot",
        )

    @pytest.mark.asyncio
    async def test_401_suspension_keyword_raises_suspension_error(self, live_client):
        """401 with 'suspended' keyword raises SuspensionError."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value='{"error": "Account suspended", "hint": "spam"}')
        mock_response.headers = {}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_response)
        mock_session.closed = False
        live_client._session = mock_session

        # Bypass rate limiters
        live_client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        live_client._proxy.wait_for_rate_limit = AsyncMock()
        live_client._proxy.check_rate_limit = AsyncMock()

        with pytest.raises(SuspensionError) as exc_info:
            await live_client._request("GET", "/agents/me")

        assert "suspended" in str(exc_info.value).lower()
        assert live_client._account_status == "suspended"

    @pytest.mark.asyncio
    async def test_403_suspension_keyword_raises_suspension_error(self, live_client):
        """403 with 'suspend' keyword raises SuspensionError."""
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value='{"error": "Your account has been suspended"}')
        mock_response.headers = {}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_response)
        mock_session.closed = False
        live_client._session = mock_session

        live_client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        live_client._proxy.wait_for_rate_limit = AsyncMock()
        live_client._proxy.check_rate_limit = AsyncMock()

        with pytest.raises(SuspensionError):
            await live_client._request("GET", "/posts")

        assert live_client._account_status == "suspended"

    @pytest.mark.asyncio
    async def test_401_non_suspension_raises_auth_error(self, live_client):
        """401 without suspension keyword raises AuthenticationError."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value='{"error": "Invalid API key"}')
        mock_response.headers = {}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_response)
        mock_session.closed = False
        live_client._session = mock_session

        live_client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        live_client._proxy.wait_for_rate_limit = AsyncMock()
        live_client._proxy.check_rate_limit = AsyncMock()

        with pytest.raises(AuthenticationError):
            await live_client._request("GET", "/agents/me")

        assert live_client._account_status == "auth_error"

    @pytest.mark.asyncio
    async def test_successful_request_sets_active(self, live_client):
        """Successful 200 response sets status to active."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"id": "123", "name": "test"}')
        mock_response.json = AsyncMock(return_value={"id": "123", "name": "test"})
        mock_response.headers = {}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_response)
        mock_session.closed = False
        live_client._session = mock_session

        live_client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        live_client._proxy.wait_for_rate_limit = AsyncMock()
        live_client._proxy.check_rate_limit = AsyncMock()
        live_client._proxy._cache_enabled = False

        result = await live_client._request("GET", "/agents/me")
        assert result["id"] == "123"
        assert live_client._account_status == "active"


# ── Model Roundtrip Integration ───────────────────────────────────────────


class TestModelRoundtrip:
    """Verify models can survive a JSON serialize → deserialize roundtrip."""

    def test_submolt_roundtrip(self):
        original = Submolt(name="ai", display_name="AI", description="AI topics", subscriber_count=42)
        data = json.loads(original.model_dump_json())
        restored = Submolt.from_dict(data)
        assert restored == original

    def test_dm_request_roundtrip(self):
        original = DMRequest(id="r1", sender_id="a1", sender_name="Bot", message="Hi")
        data = json.loads(original.model_dump_json())
        restored = DMRequest.from_dict(data)
        assert restored.id == original.id
        assert restored.sender_name == original.sender_name

    def test_conversation_roundtrip(self):
        original = Conversation(id="c1", participant_id="a2", participant_name="X", last_message="Hi")
        data = json.loads(original.model_dump_json())
        restored = Conversation.from_dict(data)
        assert restored.id == original.id

    def test_message_roundtrip(self):
        original = Message(id="m1", sender_id="a1", content="Hello!")
        data = json.loads(original.model_dump_json())
        restored = Message.from_dict(data)
        assert restored.content == original.content


# ── API Method + Model Integration ────────────────────────────────────────


class TestAPIMethodIntegration:
    """Test that client methods correctly parse model responses."""

    @pytest.fixture
    def client(self):
        c = MoltbookClient(api_key="key", identity_name="test")
        c._request = AsyncMock()
        c._rate_limiter.acquire_post = AsyncMock(return_value=True)
        return c

    @pytest.mark.asyncio
    async def test_list_submolts_parses_correctly(self, client):
        client._request.return_value = {
            "submolts": [
                {"name": "ai", "display_name": "AI", "subscriber_count": 100},
                {"name": "crypto", "display_name": "Crypto", "subscriber_count": 50},
            ],
        }
        result = await client.list_submolts()
        assert len(result) == 2
        assert all(isinstance(s, Submolt) for s in result)
        assert result[0].subscriber_count == 100

    @pytest.mark.asyncio
    async def test_dm_flow_request_to_conversation(self, client):
        """Simulate full DM flow: request → approve → list → send."""
        # Step 1: Send DM request
        client._request.return_value = {"id": "req-001", "status": "pending"}
        req = await client.send_dm_request("agent-002", "Hello!")
        assert req["status"] == "pending"

        # Step 2: List pending requests
        client._request.return_value = {
            "requests": [{"id": "req-001", "sender_id": "agent-002", "sender_name": "Bot2"}],
        }
        requests = await client.list_dm_requests()
        assert len(requests) == 1
        assert isinstance(requests[0], DMRequest)

        # Step 3: Approve request
        client._request.return_value = {}
        approved = await client.approve_dm_request("req-001")
        assert approved is True

        # Step 4: List conversations
        client._request.return_value = {
            "conversations": [{"id": "conv-001", "participant_id": "agent-002", "participant_name": "Bot2"}],
        }
        convs = await client.list_conversations()
        assert len(convs) == 1
        assert isinstance(convs[0], Conversation)

        # Step 5: Send message
        client._request.return_value = {
            "message": {"id": "msg-001", "sender_id": "agent-001", "content": "Hi!"},
        }
        msg = await client.send_dm("conv-001", "Hi!")
        assert isinstance(msg, Message)
        assert msg.content == "Hi!"

    @pytest.mark.asyncio
    async def test_follow_unfollow_flow(self, client):
        """Follow then unfollow an agent."""
        client._request.return_value = {}
        assert await client.follow_agent("otherbot") is True
        assert await client.unfollow_agent("otherbot") is True

    @pytest.mark.asyncio
    async def test_submolt_subscribe_unsubscribe_flow(self, client):
        """Subscribe then unsubscribe to submolt."""
        client._request.return_value = {}
        assert await client.subscribe_submolt("ai") is True
        assert await client.unsubscribe_submolt("ai") is True

    @pytest.mark.asyncio
    async def test_create_link_post_and_delete_flow(self, client):
        """Create a link post then delete it."""
        client._request.return_value = {
            "post": {"id": "post-link-1", "title": "Link", "content": "", "agent_id": "a1", "agent_name": "test"},
        }
        post = await client.create_link_post("Link", "https://example.com")
        assert isinstance(post, Post)

        client._request.return_value = {}
        assert await client.delete_post(post.id) is True

    @pytest.mark.asyncio
    async def test_update_and_get_profile(self, client):
        """Update profile then verify it."""
        client._request.return_value = {"id": "a1", "name": "test", "description": "Updated bio"}
        agent = await client.update_profile("Updated bio")
        assert isinstance(agent, Agent)
        assert agent.description == "Updated bio"

        client._request.return_value = {"id": "a1", "name": "test", "description": "Updated bio"}
        fetched = await client.get_agent_profile("test")
        assert fetched.description == "Updated bio"
