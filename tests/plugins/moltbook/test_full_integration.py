"""
Full HTTP integration tests for MoltbookClient.

These tests exercise the real HTTP layer by running MoltbookClient against a
local aiohttp mock server — unlike test_integration.py which mocks the Python
client directly. This validates that the client speaks correct HTTP and parses
all API responses faithfully.

Each test class gets a fresh server + client fixture (no shared state).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from overblick.plugins.moltbook.client import (
    AuthenticationError,
    MoltbookClient,
    MoltbookError,
    SuspensionError,
)
from overblick.plugins.moltbook.models import (
    Agent,
    Comment,
    Conversation,
    FeedItem,
    Message,
    Post,
    SearchResult,
    Submolt,
)

from .mock_server import MockMoltbookServer


# ---------------------------------------------------------------------------
# Minimal challenge handler (no LLM required)
# ---------------------------------------------------------------------------

class MockChallengeHandler:
    """
    Test double for PerContentChallengeHandler.

    detect() returns True whenever the response carries verification_required=True.
    solve() returns a pre-configured result dict so the caller can construct a
    proper model without touching any LLM.
    """

    def __init__(self, solve_result: dict | None = None) -> None:
        self.detected: bool = False
        self.solve_called: bool = False
        self.last_challenge_data: dict | None = None
        self._solve_result = solve_result

    def detect(self, response_data: dict, http_status: int) -> bool:
        if response_data.get("verification_required"):
            self.detected = True
            self.last_challenge_data = dict(response_data)
            return True
        return False

    async def solve(self, challenge_data: dict) -> dict | None:
        self.solve_called = True
        return self._solve_result

    def set_session(self, session) -> None:  # noqa: ANN001
        pass


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _build_client(base_url: str, challenge_handler=None) -> MoltbookClient:
    """
    Construct a MoltbookClient configured for testing:
    - rate limiter bypassed (no waits between posts/comments)
    - GET cache disabled (always hits the server for fresh state)
    """
    client = MoltbookClient(
        base_url=base_url,
        api_key="test-key",
        agent_id="agent-1",
        identity_name="test",
        challenge_handler=challenge_handler,
    )
    # Bypass client-side rate limits so tests run without any sleeps
    client._rate_limiter.acquire_post = AsyncMock(return_value=True)
    client._rate_limiter.acquire_comment = AsyncMock(return_value=True)
    # Disable GET caching so every call reflects the latest server state
    client._proxy._cache_enabled = False
    return client


@pytest.fixture
async def server_and_client():
    """Standard fixture: fresh server + client for each test."""
    async with MockMoltbookServer() as server:
        client = _build_client(server.base_url)
        yield server, client
        await client.close()


@pytest.fixture
async def server_with_challenge_client():
    """
    Fixture for challenge handling tests.
    The MockChallengeHandler is pre-configured with a valid solve_result
    so that create_post() can construct a Post from it.
    """
    async with MockMoltbookServer() as server:
        handler = MockChallengeHandler(
            solve_result={
                "post": {
                    "id": "solved-1",
                    "agent_id": "agent-1",
                    "agent_name": "test-agent",
                    "title": "Challenge Post",
                    "content": "Content after challenge solved",
                    "submolt": "ai",
                    "upvotes": 0,
                    "downvotes": 0,
                    "comment_count": 0,
                    "tags": [],
                }
            }
        )
        client = _build_client(server.base_url, challenge_handler=handler)
        yield server, client, handler
        await client.close()


# ---------------------------------------------------------------------------
# TestAgentAPI
# ---------------------------------------------------------------------------

class TestAgentAPI:
    async def test_get_self_returns_agent(self, server_and_client):
        _, client = server_and_client
        agent = await client.get_self()
        assert isinstance(agent, Agent)
        assert agent.id == "agent-1"
        assert agent.name == "test-agent"

    async def test_update_profile(self, server_and_client):
        server, client = server_and_client
        updated = await client.update_profile("New description for testing")
        assert isinstance(updated, Agent)
        assert updated.description == "New description for testing"
        # Verify the description was persisted in server state
        assert server.state.agents["agent-1"]["description"] == "New description for testing"


# ---------------------------------------------------------------------------
# TestPostsAPI
# ---------------------------------------------------------------------------

class TestPostsAPI:
    async def test_create_post_returns_post(self, server_and_client):
        _, client = server_and_client
        post = await client.create_post(
            title="Hello World", content="Test content here", submolt="ai",
        )
        assert isinstance(post, Post)
        assert post.title == "Hello World"
        assert post.content == "Test content here"
        assert post.agent_id == "agent-1"
        assert post.agent_name == "test-agent"

    async def test_get_post_by_id(self, server_and_client):
        _, client = server_and_client
        created = await client.create_post(title="Fetchable Post", content="Some content")
        fetched = await client.get_post(created.id, include_comments=False)
        assert fetched.id == created.id
        assert fetched.title == "Fetchable Post"

    async def test_list_posts_pagination(self, server_and_client):
        server, client = server_and_client
        # Pre-populate 5 posts directly to avoid multiple create_post() calls
        for i in range(1, 6):
            pid = str(100 + i)
            server.state.posts[pid] = {
                "id": pid,
                "agent_id": "agent-1",
                "agent_name": "test-agent",
                "title": f"Post {i}",
                "content": f"Content {i}",
                "submolt": "ai",
                "upvotes": 0,
                "downvotes": 0,
                "comment_count": 0,
                "tags": [],
            }
            server.state.comments[pid] = []

        posts = await client.get_posts(limit=2)
        assert len(posts) == 2
        assert all(isinstance(p, Post) for p in posts)

    async def test_upvote_post(self, server_and_client):
        server, client = server_and_client
        created = await client.create_post(title="Voteable", content="Vote on me")
        result = await client.upvote_post(created.id)
        assert result is True
        assert server.state.posts[created.id]["upvotes"] == 1

    async def test_delete_post(self, server_and_client):
        server, client = server_and_client
        created = await client.create_post(title="Deleteable", content="Delete me")
        post_id = created.id
        assert post_id in server.state.posts

        result = await client.delete_post(post_id)
        assert result is True
        assert post_id not in server.state.posts

    async def test_create_link_post(self, server_and_client):
        server, client = server_and_client
        post = await client.create_link_post(
            title="A Link Post", url="https://example.com/article", submolt="ai",
        )
        assert isinstance(post, Post)
        assert post.title == "A Link Post"
        raw = server.state.posts[post.id]
        assert raw["url"] == "https://example.com/article"


# ---------------------------------------------------------------------------
# TestCommentsAPI
# ---------------------------------------------------------------------------

class TestCommentsAPI:
    async def test_create_comment(self, server_and_client):
        _, client = server_and_client
        post = await client.create_post(title="Post for comments", content="Content")
        comment = await client.create_comment(post.id, "A test comment")
        assert isinstance(comment, Comment)
        assert comment.content == "A test comment"
        assert comment.post_id == post.id
        assert comment.agent_id == "agent-1"

    async def test_threaded_comment(self, server_and_client):
        _, client = server_and_client
        post = await client.create_post(title="Thread post", content="Content")
        parent = await client.create_comment(post.id, "Parent comment")
        child = await client.create_comment(post.id, "Child comment", parent_id=parent.id)
        assert isinstance(child, Comment)
        assert child.parent_id == parent.id

    async def test_get_comments(self, server_and_client):
        server, client = server_and_client
        # Pre-populate post + two comments to avoid rate-limiter waits
        server.state.posts["200"] = {
            "id": "200",
            "agent_id": "agent-1",
            "agent_name": "test-agent",
            "title": "Commented post",
            "content": "Content",
            "submolt": "ai",
            "upvotes": 0,
            "downvotes": 0,
            "comment_count": 2,
            "tags": [],
        }
        server.state.comments["200"] = [
            {
                "id": "c1", "post_id": "200", "agent_id": "agent-1",
                "agent_name": "test-agent", "content": "First comment",
                "upvotes": 0, "parent_id": None,
            },
            {
                "id": "c2", "post_id": "200", "agent_id": "agent-1",
                "agent_name": "test-agent", "content": "Second comment",
                "upvotes": 0, "parent_id": None,
            },
        ]
        post = await client.get_post("200", include_comments=True)
        assert len(post.comments) == 2
        assert all(isinstance(c, Comment) for c in post.comments)

    async def test_upvote_comment(self, server_and_client):
        server, client = server_and_client
        post = await client.create_post(title="Upvotable comments", content="Content")
        comment = await client.create_comment(post.id, "Upvote me")
        result = await client.upvote_comment(post.id, comment.id)
        assert result is True
        comment_in_state = server.state.comments[post.id][0]
        assert comment_in_state["upvotes"] == 1


# ---------------------------------------------------------------------------
# TestFeedAPI
# ---------------------------------------------------------------------------

class TestFeedAPI:
    async def test_feed_returns_posts(self, server_and_client):
        server, client = server_and_client
        server.state.posts["300"] = {
            "id": "300", "agent_id": "agent-1", "agent_name": "test-agent",
            "title": "Feed test post", "content": "Content", "submolt": "ai",
            "upvotes": 0, "downvotes": 0, "comment_count": 0, "tags": [],
        }
        server.state.comments["300"] = []
        feed = await client.get_feed()
        assert isinstance(feed, list)
        assert len(feed) >= 1
        assert all(isinstance(item, FeedItem) for item in feed)

    async def test_feed_contains_new_posts(self, server_and_client):
        _, client = server_and_client
        post = await client.create_post(title="Fresh Feed Post", content="Just posted")
        feed = await client.get_feed()
        feed_post_ids = [item.post.id for item in feed]
        assert post.id in feed_post_ids


# ---------------------------------------------------------------------------
# TestSearchAPI
# ---------------------------------------------------------------------------

class TestSearchAPI:
    async def test_search_finds_post_by_title(self, server_and_client):
        _, client = server_and_client
        post = await client.create_post(
            title="Unique Xylophone Title", content="Content about xylophones",
        )
        result = await client.search("xylophone")
        assert isinstance(result, SearchResult)
        found_ids = [p.id for p in result.posts]
        assert post.id in found_ids


# ---------------------------------------------------------------------------
# TestFollowAPI
# ---------------------------------------------------------------------------

class TestFollowAPI:
    async def test_follow_agent(self, server_and_client):
        server, client = server_and_client
        result = await client.follow_agent("other-agent")
        assert result is True
        assert "agent-1" in server.state.followers.get("other-agent", set())

    async def test_unfollow_agent(self, server_and_client):
        server, client = server_and_client
        await client.follow_agent("other-agent")
        result = await client.unfollow_agent("other-agent")
        assert result is True
        assert "agent-1" not in server.state.followers.get("other-agent", set())


# ---------------------------------------------------------------------------
# TestDMFlow
# ---------------------------------------------------------------------------

class TestDMFlow:
    async def test_dm_request_sent(self, server_and_client):
        server, client = server_and_client
        result = await client.send_dm_request("agent-99", "Hello there!")
        assert "request_id" in result
        assert len(server.state.dm_requests) == 1
        assert server.state.dm_requests[0]["message"] == "Hello there!"

    async def test_dm_request_approved(self, server_and_client):
        server, client = server_and_client
        req = await client.send_dm_request("agent-99", "Let's talk")
        approved = await client.approve_dm_request(req["request_id"])
        assert approved is True
        assert len(server.state.conversations) == 1

    async def test_send_message(self, server_and_client):
        server, client = server_and_client
        req = await client.send_dm_request("agent-99", "Opening message")
        await client.approve_dm_request(req["request_id"])
        conv_id = list(server.state.conversations.keys())[0]

        msg = await client.send_dm(conv_id, "Hello from integration test!")
        assert isinstance(msg, Message)
        assert msg.content == "Hello from integration test!"
        assert msg.sender_id == "agent-1"

    async def test_list_conversations(self, server_and_client):
        server, client = server_and_client
        req = await client.send_dm_request("agent-99", "Chat start")
        await client.approve_dm_request(req["request_id"])

        conversations = await client.list_conversations()
        assert isinstance(conversations, list)
        assert len(conversations) == 1
        assert isinstance(conversations[0], Conversation)


# ---------------------------------------------------------------------------
# TestChallengeHandling
# ---------------------------------------------------------------------------

class TestChallengeHandling:
    async def test_challenge_injected_on_post(self, server_with_challenge_client):
        """
        When server sets challenge_on_next_post=True, the POST /posts response
        embeds verification data. The client's challenge handler must detect it
        and be called with the original challenge payload.
        """
        server, client, handler = server_with_challenge_client
        server.state.challenge_on_next_post = True

        await client.create_post(title="Challenge post", content="Content", submolt="ai")

        assert handler.detected is True
        assert handler.last_challenge_data is not None
        assert handler.last_challenge_data["verification_required"] is True
        assert "verification" in handler.last_challenge_data

    async def test_challenge_solve_retries_post(self, server_with_challenge_client):
        """
        After a challenge is detected, solve() is called and its return value
        is used as the final API response. The resulting Post comes from the
        handler's solve_result, not the original server response.
        """
        server, client, handler = server_with_challenge_client
        server.state.challenge_on_next_post = True

        post = await client.create_post(title="Challenge post", content="Content", submolt="ai")

        assert handler.solve_called is True
        assert isinstance(post, Post)
        assert post.id == "solved-1"
        assert post.title == "Challenge Post"

    async def test_suspension_raises_suspension_error(self, server_and_client):
        """A 401 containing 'suspended' in the error body raises SuspensionError."""
        server, client = server_and_client
        server.state.suspended = True
        with pytest.raises(SuspensionError):
            await client.get_self()


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    async def test_rate_limit_raises_moltbook_error(self, server_and_client):
        """
        When the server returns 429 on every request, the client retries
        retry_count=3 times then raises MoltbookError. The RateLimitError
        subclass is only raised if retry_count > MAX_RATE_LIMIT_RETRIES (3),
        which is not the case for the default retry_count=3. Both are valid
        indicators of rate limiting at the MoltbookError level.
        """
        server, client = server_and_client
        server.state.rate_limited = True
        with pytest.raises(MoltbookError):
            await client.get_self()

    async def test_404_raises_moltbook_error(self, server_and_client):
        """
        Requesting a nonexistent submolt returns 404, which _request() retries
        then raises MoltbookError. asyncio.sleep is patched to prevent the
        exponential backoff (1s + 2s) from slowing down the test suite.
        """
        _, client = server_and_client
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MoltbookError):
                await client.get_submolt("this-submolt-does-not-exist-xyx")

    async def test_auth_error_raises_authentication_error(self, server_and_client):
        """A 401 without 'suspended' in the body raises AuthenticationError."""
        server, client = server_and_client
        server.state.auth_error = True
        with pytest.raises(AuthenticationError):
            await client.get_self()


# ---------------------------------------------------------------------------
# TestSubmolts
# ---------------------------------------------------------------------------

class TestSubmolts:
    async def test_list_submolts(self, server_and_client):
        _, client = server_and_client
        submolts = await client.list_submolts()
        assert isinstance(submolts, list)
        assert len(submolts) >= 1
        assert all(isinstance(s, Submolt) for s in submolts)
        names = [s.name for s in submolts]
        assert "ai" in names

    async def test_get_submolt(self, server_and_client):
        _, client = server_and_client
        submolt = await client.get_submolt("ai")
        assert isinstance(submolt, Submolt)
        assert submolt.name == "ai"
        assert submolt.display_name == "AI"

    async def test_subscribe_submolt(self, server_and_client):
        server, client = server_and_client
        result = await client.subscribe_submolt("ai")
        assert result is True
        assert "agent-1:ai" in server.state.subscriptions

    async def test_unsubscribe_submolt(self, server_and_client):
        server, client = server_and_client
        await client.subscribe_submolt("crypto")
        assert "agent-1:crypto" in server.state.subscriptions
        result = await client.unsubscribe_submolt("crypto")
        assert result is True
        assert "agent-1:crypto" not in server.state.subscriptions


# ---------------------------------------------------------------------------
# TestDownvoteAPI
# ---------------------------------------------------------------------------

class TestDownvoteAPI:
    async def test_downvote_post(self, server_and_client):
        server, client = server_and_client
        post = await client.create_post(title="Downvotable", content="Content")
        result = await client.downvote_post(post.id)
        assert result is True
        assert server.state.posts[post.id]["downvotes"] == 1

    async def test_downvote_then_upvote(self, server_and_client):
        server, client = server_and_client
        post = await client.create_post(title="Both Votes", content="Content")
        await client.downvote_post(post.id)
        await client.upvote_post(post.id)
        assert server.state.posts[post.id]["downvotes"] == 1
        assert server.state.posts[post.id]["upvotes"] == 1


# ---------------------------------------------------------------------------
# TestAgentProfileAPI
# ---------------------------------------------------------------------------

class TestAgentProfileAPI:
    async def test_get_agent_profile_by_name(self, server_and_client):
        _, client = server_and_client
        agent = await client.get_agent_profile("test-agent")
        assert isinstance(agent, Agent)
        assert agent.name == "test-agent"
        assert agent.id == "agent-1"

    async def test_get_agent_profile_not_found(self, server_and_client):
        _, client = server_and_client
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MoltbookError):
                await client.get_agent_profile("nonexistent-agent-xyz")

    async def test_identity_token(self, server_and_client):
        _, client = server_and_client
        result = await client.generate_identity_token()
        assert result is not None


# ---------------------------------------------------------------------------
# TestSearchExtended
# ---------------------------------------------------------------------------

class TestSearchExtended:
    async def test_search_by_content(self, server_and_client):
        _, client = server_and_client
        await client.create_post(title="Boring Title", content="The xylophone plays beautifully")
        result = await client.search("xylophone")
        assert isinstance(result, SearchResult)
        assert len(result.posts) >= 1

    async def test_search_no_results(self, server_and_client):
        _, client = server_and_client
        result = await client.search("zzzznonexistenttermmmmm")
        assert isinstance(result, SearchResult)
        assert len(result.posts) == 0
        assert result.total_count == 0

    async def test_search_case_insensitive(self, server_and_client):
        _, client = server_and_client
        await client.create_post(title="UPPERCASE TITLE", content="Content")
        result = await client.search("uppercase")
        assert len(result.posts) >= 1


# ---------------------------------------------------------------------------
# TestFeedExtended
# ---------------------------------------------------------------------------

class TestFeedExtended:
    async def test_empty_feed(self, server_and_client):
        _, client = server_and_client
        feed = await client.get_feed()
        assert isinstance(feed, list)
        assert len(feed) == 0

    async def test_feed_ordering(self, server_and_client):
        server, client = server_and_client
        for i in range(3):
            pid = str(400 + i)
            server.state.posts[pid] = {
                "id": pid, "agent_id": "agent-1", "agent_name": "test-agent",
                "title": f"Post {i}", "content": f"Content {i}", "submolt": "ai",
                "upvotes": 0, "downvotes": 0, "comment_count": 0, "tags": [],
            }
            server.state.comments[pid] = []
        feed = await client.get_feed()
        assert len(feed) == 3
        # Newest first (highest ID)
        ids = [item.post.id for item in feed]
        assert ids == ["402", "401", "400"]


# ---------------------------------------------------------------------------
# TestSubmoltFiltering
# ---------------------------------------------------------------------------

class TestSubmoltFiltering:
    async def test_list_posts_by_submolt(self, server_and_client):
        server, client = server_and_client
        server.state.posts["500"] = {
            "id": "500", "agent_id": "agent-1", "agent_name": "test-agent",
            "title": "AI Post", "content": "AI content", "submolt": "ai",
            "upvotes": 0, "downvotes": 0, "comment_count": 0, "tags": [],
        }
        server.state.posts["501"] = {
            "id": "501", "agent_id": "agent-1", "agent_name": "test-agent",
            "title": "Crypto Post", "content": "Crypto content", "submolt": "crypto",
            "upvotes": 0, "downvotes": 0, "comment_count": 0, "tags": [],
        }
        server.state.comments["500"] = []
        server.state.comments["501"] = []

        # All posts returned when no submolt filter
        all_posts = await client.get_posts(limit=10)
        assert len(all_posts) == 2


# ---------------------------------------------------------------------------
# TestDMActivity
# ---------------------------------------------------------------------------

class TestDMActivity:
    async def test_dm_activity_count(self, server_and_client):
        server, client = server_and_client
        # Pre-populate pending DM requests
        server.state.dm_requests = [
            {"id": "r1", "sender_id": "a1", "sender_name": "Bot1",
             "message": "Hi", "status": "pending"},
            {"id": "r2", "sender_id": "a2", "sender_name": "Bot2",
             "message": "Hello", "status": "pending"},
            {"id": "r3", "sender_id": "a3", "sender_name": "Bot3",
             "message": "Hey", "status": "approved"},
        ]
        activity = await client.check_dm_activity()
        assert activity["unread_count"] == 2


# ---------------------------------------------------------------------------
# TestScenarioSwitching
# ---------------------------------------------------------------------------

class TestScenarioSwitching:
    """Test switching between server scenarios mid-test."""

    async def test_suspend_then_resume(self, server_and_client):
        """Server suspension followed by recovery."""
        server, client = server_and_client

        # Normal operation
        agent = await client.get_self()
        assert agent.name == "test-agent"

        # Suspend
        server.state.suspended = True
        with pytest.raises(SuspensionError):
            await client.get_self()

        # Resume
        server.state.suspended = False
        agent = await client.get_self()
        assert agent.name == "test-agent"

    async def test_rate_limit_then_clear(self, server_and_client):
        """Rate limit then clear allows normal operation."""
        server, client = server_and_client

        server.state.rate_limited = True
        with pytest.raises(MoltbookError):
            await client.get_self()

        server.state.rate_limited = False
        agent = await client.get_self()
        assert agent.name == "test-agent"

    async def test_challenge_only_affects_next_post(self, server_and_client):
        """Challenge flag is consumed by first POST, not subsequent ones."""
        server, client = server_and_client
        server.state.challenge_on_next_post = True

        # First post triggers challenge
        post1 = await client.create_post(title="First", content="Content")
        # Challenge consumed — flag should be False
        assert server.state.challenge_on_next_post is False

        # Second post is normal
        post2 = await client.create_post(title="Second", content="Content")
        assert post2.title == "Second"


# ---------------------------------------------------------------------------
# TestDMFullFlow
# ---------------------------------------------------------------------------

class TestDMFullFlow:
    """Test the complete DM lifecycle via real HTTP."""

    async def test_full_dm_lifecycle(self, server_and_client):
        """Request → Approve → List conversations → Send message → List messages."""
        server, client = server_and_client

        # Send request
        req = await client.send_dm_request("agent-99", "Can we talk?")
        assert "request_id" in req

        # List pending requests
        requests = await client.list_dm_requests()
        assert len(requests) == 1
        assert requests[0].message == "Can we talk?"

        # Approve request — creates conversation
        approved = await client.approve_dm_request(req["request_id"])
        assert approved is True
        assert len(server.state.conversations) == 1

        # List conversations
        conversations = await client.list_conversations()
        assert len(conversations) == 1
        conv_id = conversations[0].id

        # Send message
        msg = await client.send_dm(conv_id, "Hey, thanks for accepting!")
        assert isinstance(msg, Message)
        assert msg.content == "Hey, thanks for accepting!"

        # Verify message stored in conversation
        conv_state = server.state.conversations[conv_id]
        assert len(conv_state["messages"]) == 1
        assert conv_state["last_message"] == "Hey, thanks for accepting!"

    async def test_send_dm_to_nonexistent_conversation(self, server_and_client):
        """Sending a DM to a nonexistent conversation returns 404."""
        _, client = server_and_client
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MoltbookError):
                await client.send_dm("nonexistent-conv", "Hello?")
