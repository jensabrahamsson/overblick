"""
Tests for MoltbookClient _request method and error handling paths.

Covers the uncovered lines in client.py: HTTP error handling, challenge detection,
rate limiting, retry logic, caching, session management, and all API methods.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from overblick.plugins.moltbook.client import (
    AuthenticationError,
    MoltbookClient,
    MoltbookError,
    RateLimitError,
    SuspensionError,
)
from overblick.plugins.moltbook.models import Agent, Comment, FeedItem, Post, SearchResult


def _make_mock_response(status=200, body=None, headers=None, text=None):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    if body is None:
        body = {"success": True}
    body_text = text if text is not None else json.dumps(body)
    resp.text = AsyncMock(return_value=body_text)
    resp.headers = headers or {"Content-Type": "application/json"}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_client(**kwargs):
    """Create a MoltbookClient with mocked session."""
    c = MoltbookClient(
        api_key="test-key",
        agent_id="agent-001",
        identity_name="testbot",
        **kwargs,
    )
    return c


def _setup_client_for_request(client, resp_or_side_effect):
    """Set up client with mock session and rate limiter for _request tests."""
    mock_session = MagicMock()
    if callable(resp_or_side_effect) or isinstance(resp_or_side_effect, (list, Exception)):
        mock_session.request = MagicMock(side_effect=resp_or_side_effect)
    else:
        mock_session.request = MagicMock(return_value=resp_or_side_effect)
    mock_session.closed = False
    mock_session.close = AsyncMock()
    client._session = mock_session
    client._rate_limiter = MagicMock()
    client._rate_limiter.acquire_request = AsyncMock(return_value=True)
    # Patch _ensure_session to avoid re-creating real sessions
    client._ensure_session = AsyncMock()
    return client


class TestSuspensionErrorParsing:
    def test_suspended_until_dt_returns_none_on_invalid(self):
        err = SuspensionError("test", suspended_until="not-a-date")
        assert err.suspended_until_dt is None

    def test_suspended_until_dt_without_z(self):
        err = SuspensionError("test", suspended_until="2026-02-18T18:46:42")
        dt = err.suspended_until_dt
        assert dt is not None
        assert dt.year == 2026


class TestClientRequestMethod:
    def _setup_req(self, client, resp_or_side_effect):
        """Helper to set up client for _request tests."""
        mock_session = MagicMock()
        if isinstance(resp_or_side_effect, list):
            mock_session.request = MagicMock(side_effect=resp_or_side_effect)
        elif isinstance(resp_or_side_effect, type) and issubclass(resp_or_side_effect, Exception):
            mock_session.request = MagicMock(side_effect=resp_or_side_effect)
        elif callable(resp_or_side_effect) and not hasattr(resp_or_side_effect, "status"):
            mock_session.request = MagicMock(side_effect=resp_or_side_effect)
        else:
            mock_session.request = MagicMock(return_value=resp_or_side_effect)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        client._ensure_session = AsyncMock()

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_value(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(200))
        client._proxy._cache.set("GET", "/test", {"cached": True})
        result = await client._request("GET", "/test")
        assert result == {"cached": True}

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_raises(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(200))
        client._rate_limiter.acquire_request = AsyncMock(return_value=False)
        with pytest.raises(RateLimitError, match="Rate limit exceeded"):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_log_json_keys_for_post(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        result = await client._request("POST", "/test", json={"key": "val"})
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_error_response_forensic_logging(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(
            400, text='{"error": "challenge required", "captcha": true}',
            headers={"Content-Type": "application/json", "X-Challenge": "yes"},
        ))
        with pytest.raises(MoltbookError):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_challenge_handler_intercepts_400_post(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=True)
        handler.solve = AsyncMock(return_value={"solved": True})
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(400, text='{"challenge_text": "solve", "nonce": "abc"}'))
        result = await client._request("POST", "/test")
        assert result == {"solved": True}

    @pytest.mark.asyncio
    async def test_challenge_handler_returns_none(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=True)
        handler.solve = AsyncMock(return_value=None)
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(400, text='{"challenge_text": "solve", "nonce": "abc"}'))
        with pytest.raises(MoltbookError):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_challenge_handler_json_decode_error(self):
        handler = MagicMock()
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(400, text="not json at all"))
        with pytest.raises(MoltbookError):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_challenge_handler_raises_moltbook_error(self):
        handler = MagicMock()
        handler.detect = MagicMock(side_effect=MoltbookError("fail"))
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(400, text='{"error": "bad"}'))
        with pytest.raises(MoltbookError, match="fail"):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_challenge_handler_generic_exception(self):
        handler = MagicMock()
        handler.detect = MagicMock(side_effect=RuntimeError("oops"))
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(400, text='{"error": "bad"}'))
        with pytest.raises(MoltbookError):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_auth_error_401_with_hint(self):
        """Lines 301-328: 401 with hint in error response."""
        client = _make_client()
        resp = _make_mock_response(
            401,
            text=json.dumps({"error": "Invalid key", "hint": "Check your API key", "message": "auth failed"}),
        )
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=resp)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_request = AsyncMock(return_value=True)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            with pytest.raises(AuthenticationError, match="Check your API key"):
                await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_auth_error_401_suspension_detected(self):
        """Lines 308-318: 401 with 'suspended' in combined message."""
        client = _make_client()
        resp = _make_mock_response(
            401,
            text=json.dumps({
                "error": "Account suspended",
                "hint": "suspended until 2026-03-01T00:00:00Z",
                "message": "Your account is suspended",
            }),
        )
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=resp)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_request = AsyncMock(return_value=True)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            with pytest.raises(SuspensionError):
                await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_auth_error_401_unparseable_body(self):
        """Lines 324-327: 401 with unparseable response body."""
        client = _make_client()
        resp = _make_mock_response(401, text="not json")
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=resp)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_request = AsyncMock(return_value=True)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            with pytest.raises(AuthenticationError, match="Auth error"):
                await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_forbidden_403_suspension(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(403, text=json.dumps({
            "error": "Account suspended due to violations",
            "message": "You are suspended until 2026-03-01T00:00:00Z",
            "path": "/posts", "timestamp": "2026-02-15T10:00:00Z",
        })))
        with pytest.raises(SuspensionError):
            await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_forbidden_403_non_suspension(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(403, text='{"error": "Forbidden"}'))
        with pytest.raises(MoltbookError, match="403"):
            await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_forbidden_403_unparseable_json(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(403, text="Forbidden plain text"))
        with pytest.raises(MoltbookError, match="403"):
            await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_method_not_allowed_405(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(405, text="Method Not Allowed"))
        with pytest.raises(MoltbookError, match="405"):
            await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_rate_limit_429_with_retry(self):
        client = _make_client()
        self._setup_req(client, [
            _make_mock_response(429, text="Rate limited", headers={"Retry-After": "1", "Content-Type": "text/plain"}),
            _make_mock_response(200, {"result": "ok"}),
        ])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_rate_limit_429_invalid_retry_after(self):
        client = _make_client()
        self._setup_req(client, [
            _make_mock_response(429, text="Rate limited", headers={"Retry-After": "not-a-number", "Content-Type": "text/plain"}),
            _make_mock_response(200, {"result": "ok"}),
        ])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_rate_limit_429_max_retries_exceeded(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(429, text="Rate limited", headers={"Retry-After": "1", "Content-Type": "text/plain"}))
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RateLimitError, match="Rate limited"):
                await client._request("GET", "/test", retry_count=10)

    @pytest.mark.asyncio
    async def test_server_error_500_retries(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(500, text="Internal Server Error"))
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MoltbookError, match="500"):
                await client._request("GET", "/test", retry_count=2)

    @pytest.mark.asyncio
    async def test_other_4xx_error(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(422, text="Unprocessable Entity"))
        with pytest.raises(MoltbookError, match="422"):
            await client._request("GET", "/test", retry_count=1)

    @pytest.mark.asyncio
    async def test_response_router_detects_challenge_post(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=False)
        handler.solve = AsyncMock(return_value={"solved": True})
        router = AsyncMock()
        verdict = MagicMock()
        verdict.is_challenge = True
        router.inspect = AsyncMock(return_value=verdict)
        client = _make_client(challenge_handler=handler, response_router=router)
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        result = await client._request("POST", "/test")
        assert result == {"solved": True}

    @pytest.mark.asyncio
    async def test_response_router_challenge_solve_fails(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=False)
        handler.solve = AsyncMock(return_value=None)
        router = AsyncMock()
        verdict = MagicMock()
        verdict.is_challenge = True
        router.inspect = AsyncMock(return_value=verdict)
        client = _make_client(challenge_handler=handler, response_router=router)
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        with pytest.raises(MoltbookError, match="Failed to solve"):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_response_router_challenge_get_request(self):
        router = AsyncMock()
        verdict = MagicMock()
        verdict.is_challenge = True
        router.inspect = AsyncMock(return_value=verdict)
        client = _make_client(response_router=router)
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        result = await client._request("GET", "/test")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_challenge_handler_fallback_2xx_post_solved(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=True)
        handler.solve = AsyncMock(return_value={"solved": True})
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        result = await client._request("POST", "/test")
        assert result == {"solved": True}

    @pytest.mark.asyncio
    async def test_challenge_handler_fallback_2xx_post_fail(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=True)
        handler.solve = AsyncMock(return_value=None)
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        with pytest.raises(MoltbookError, match="Failed to solve"):
            await client._request("POST", "/test")

    @pytest.mark.asyncio
    async def test_challenge_handler_fallback_2xx_get(self):
        handler = MagicMock()
        handler.detect = MagicMock(return_value=True)
        client = _make_client(challenge_handler=handler)
        self._setup_req(client, _make_mock_response(200, {"result": "ok"}))
        result = await client._request("GET", "/test")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_client_error_retry(self):
        client = _make_client()
        # Create a mock that raises ClientError from the context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("Connection failed"))
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_cm)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        client._ensure_session = AsyncMock()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MoltbookError, match="Request failed"):
                await client._request("GET", "/test", retry_count=2)

    @pytest.mark.asyncio
    async def test_cache_get_response(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(200, {"data": "cached"}))
        result = await client._request("GET", "/test")
        assert result == {"data": "cached"}

    @pytest.mark.asyncio
    async def test_session_refresh_on_auth_error(self):
        client = _make_client()
        resp_401 = _make_mock_response(401, text=json.dumps({"error": "Invalid API key"}))
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=resp_401)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_request = AsyncMock(return_value=True)
        with patch.object(client, "_refresh_session", new_callable=AsyncMock) as mock_refresh:
            with pytest.raises(AuthenticationError):
                await client._request("GET", "/test", retry_count=2)
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_429_calls_proxy_handle(self):
        client = _make_client()
        self._setup_req(client, [
            _make_mock_response(429, text="Rate limited", headers={"Retry-After": "5", "Content-Type": "text/plain"}),
            _make_mock_response(200, {"result": "ok"}),
        ])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch.object(client._proxy, "handle_rate_limit_response") as mock_handle:
                await client._request("GET", "/test")
                mock_handle.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_fallthrough(self):
        client = _make_client()
        self._setup_req(client, _make_mock_response(429, text="Rate limited", headers={"Retry-After": "1", "Content-Type": "text/plain"}))
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MoltbookError, match="max retries"):
                await client._request("GET", "/test", retry_count=1)


class TestClientRegisterAndGetAgent:
    @pytest.mark.asyncio
    async def test_register_agent(self):
        client = _make_client()
        client._request = AsyncMock(return_value={"id": "a1", "name": "bot", "description": "A bot"})
        result = await client.register_agent("bot", "A bot")
        assert result["name"] == "bot"

    @pytest.mark.asyncio
    async def test_get_agent(self):
        client = _make_client()
        client._request = AsyncMock(return_value={"id": "a1", "name": "bot"})
        result = await client.get_agent("a1")
        assert isinstance(result, Agent)
        assert result.id == "a1"


class TestClientGetPost:
    @pytest.mark.asyncio
    async def test_get_post_tier1_success(self):
        """Lines 585-602: Direct fetch succeeds."""
        client = _make_client()
        client._request = AsyncMock(return_value={
            "post": {
                "id": "p1",
                "title": "Test",
                "content": "Content",
                "agent_id": "a1",
                "agent_name": "Bot",
                "comments": [
                    {"id": "c1", "post_id": "p1", "agent_id": "a1", "agent_name": "Bot", "content": "Comment"},
                ],
            },
        })
        result = await client.get_post("p1")
        assert result.id == "p1"
        assert len(result.comments) == 1

    @pytest.mark.asyncio
    async def test_get_post_tier1_with_top_level_comments(self):
        """Lines 599-601: Post data has no embedded comments but top-level data has comments."""
        client = _make_client()
        client._request = AsyncMock(return_value={
            "post": {
                "id": "p1", "title": "Test", "content": "Content",
                "agent_id": "a1", "agent_name": "Bot",
                # No "comments" in post data
            },
            "comments": [
                {"id": "c1", "post_id": "p1", "agent_id": "a2", "agent_name": "Other", "content": "Reply"},
            ],
        })
        result = await client.get_post("p1")
        assert result.id == "p1"
        assert len(result.comments) == 1

    @pytest.mark.asyncio
    async def test_get_post_tier1_rate_limit_reraises(self):
        """Lines 603-604: RateLimitError re-raised from tier 1."""
        client = _make_client()
        client._request = AsyncMock(side_effect=RateLimitError("rate limited"))
        with pytest.raises(RateLimitError):
            await client.get_post("p1")

    @pytest.mark.asyncio
    async def test_get_post_tier2_comments_only(self):
        """Lines 609-634: Tier 2 comments-only fallback."""
        call_count = [0]

        async def mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise MoltbookError("Not found")
            return {
                "comments": [
                    {"id": "c1", "post_id": "p1", "agent_id": "a1", "agent_name": "Bot", "content": "Comment"},
                ],
            }

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert result.id == "p1"
        assert len(result.comments) == 1

    @pytest.mark.asyncio
    async def test_get_post_tier2_comments_as_list(self):
        """Line 618: Comments returned as list (not dict)."""
        call_count = [0]

        async def mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise MoltbookError("Not found")
            return [
                {"id": "c1", "post_id": "p1", "agent_id": "a1", "agent_name": "Bot", "content": "Comment"},
            ]

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert len(result.comments) == 1

    @pytest.mark.asyncio
    async def test_get_post_tier2_rate_limit_reraises(self):
        """Line 632: RateLimitError re-raised from tier 2."""
        call_count = [0]

        async def mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise MoltbookError("Not found")
            raise RateLimitError("rate limited")

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        with pytest.raises(RateLimitError):
            await client.get_post("p1")

    @pytest.mark.asyncio
    async def test_get_post_tier3_feed_search(self):
        """Lines 636-659: Tier 3 feed search."""
        call_count = [0]

        async def mock_request(method, endpoint, **kwargs):
            call_count[0] += 1
            if "/posts/p1" in endpoint:
                raise MoltbookError("Not found")
            if "/posts" in endpoint and method == "GET":
                return {
                    "posts": [
                        {"id": "p1", "title": "Found", "content": "Content",
                         "agent_id": "a1", "agent_name": "Bot"},
                        {"id": "p2", "title": "Other", "content": "Other",
                         "agent_id": "a2", "agent_name": "Bot2"},
                    ],
                }
            return {}

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert result.id == "p1"

    @pytest.mark.asyncio
    async def test_get_post_tier3_not_found(self):
        """Line 659: Post not found in any tier."""
        client = _make_client()
        client._request = AsyncMock(side_effect=MoltbookError("Not found"))
        with pytest.raises(MoltbookError, match="not found"):
            await client.get_post("nonexistent")

    @pytest.mark.asyncio
    async def test_get_post_tier3_rate_limit_reraises(self):
        """Lines 640-641: RateLimitError re-raised from tier 3."""
        call_count = [0]

        async def mock_request(method, endpoint, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise MoltbookError("Not found")
            raise RateLimitError("rate limited")

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        with pytest.raises(RateLimitError):
            await client.get_post("p1")

    @pytest.mark.asyncio
    async def test_get_post_tier3_with_comments_fetch(self):
        """Lines 646-656: Tier 3 match with comments fetch."""
        call_count = [0]

        async def mock_request(method, endpoint, **kwargs):
            call_count[0] += 1
            if "comments" in endpoint:
                return {
                    "comments": [
                        {"id": "c1", "post_id": "p1", "agent_id": "a1",
                         "agent_name": "Bot", "content": "Comment"},
                    ],
                }
            if "/posts/p1" in endpoint:
                raise MoltbookError("Not found")
            return {
                "posts": [
                    {"id": "p1", "title": "Found", "content": "Content",
                     "agent_id": "a1", "agent_name": "Bot"},
                ],
            }

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert result.id == "p1"
        assert len(result.comments) == 1

    @pytest.mark.asyncio
    async def test_get_post_tier3_comments_fetch_fails(self):
        """Lines 655-656: Tier 3 match but comments fetch fails."""
        call_count = [0]

        async def mock_request(method, endpoint, **kwargs):
            call_count[0] += 1
            if "comments" in endpoint:
                raise MoltbookError("Comments failed")
            if "/posts/p1" in endpoint:
                raise MoltbookError("Not found")
            return {
                "posts": [
                    {"id": "p1", "title": "Found", "content": "Content",
                     "agent_id": "a1", "agent_name": "Bot"},
                ],
            }

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert result.id == "p1"
        assert len(result.comments) == 0

    @pytest.mark.asyncio
    async def test_get_post_tier3_comments_as_list(self):
        """Line 651-652: Tier 3 comments returned as list."""
        comments_call = [0]

        async def mock_request(method, endpoint, **kwargs):
            if "comments" in endpoint:
                comments_call[0] += 1
                if comments_call[0] == 1:
                    # Tier 2 fails (empty — no comments, so tier2 falls through)
                    return {"comments": []}
                # Tier 3 comments endpoint returns list
                return [
                    {"id": "c1", "post_id": "p1", "agent_id": "a1",
                     "agent_name": "Bot", "content": "Comment"},
                ]
            if "/posts/p1" in endpoint:
                raise MoltbookError("Not found")
            return {
                "posts": [
                    {"id": "p1", "title": "Found", "content": "Content",
                     "agent_id": "a1", "agent_name": "Bot"},
                ],
            }

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert len(result.comments) == 1

    @pytest.mark.asyncio
    async def test_get_post_tier3_comments_as_dict(self):
        """Lines 653-654: Tier 3 comments returned as dict with 'comments' key."""
        comments_call = [0]

        async def mock_request(method, endpoint, **kwargs):
            if "comments" in endpoint:
                comments_call[0] += 1
                if comments_call[0] == 1:
                    return {"comments": []}
                return {
                    "comments": [
                        {"id": "c1", "post_id": "p1", "agent_id": "a1",
                         "agent_name": "Bot", "content": "Comment"},
                    ],
                }
            if "/posts/p1" in endpoint:
                raise MoltbookError("Not found")
            return {
                "posts": [
                    {"id": "p1", "title": "Found", "content": "Content",
                     "agent_id": "a1", "agent_name": "Bot"},
                ],
            }

        client = _make_client()
        client._request = AsyncMock(side_effect=mock_request)
        result = await client.get_post("p1")
        assert result.id == "p1"
        assert len(result.comments) == 1


class TestClientGetMyPosts:
    @pytest.mark.asyncio
    async def test_get_my_posts_filters_and_fetches_comments(self):
        """Lines 515-530: get_my_posts filters own posts and fetches comments."""
        client = _make_client()
        client.get_self = AsyncMock(return_value=Agent(id="a1", name="testbot"))
        client.get_posts = AsyncMock(return_value=[
            Post(id="p1", agent_id="a1", agent_name="testbot", title="My", content="Content"),
            Post(id="p2", agent_id="a2", agent_name="other", title="Other", content="Content"),
        ])
        client.get_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="testbot", title="My", content="Content",
            comments=[Comment(id="c1", post_id="p1", agent_id="a2", agent_name="other", content="Reply")],
        ))

        result = await client.get_my_posts(limit=5)
        assert len(result) == 1
        assert result[0].id == "p1"

    @pytest.mark.asyncio
    async def test_get_my_posts_comment_fetch_error(self):
        """Lines 527-528: Error fetching comments for own post."""
        client = _make_client()
        client.get_self = AsyncMock(return_value=Agent(id="a1", name="testbot"))
        client.get_posts = AsyncMock(return_value=[
            Post(id="p1", agent_id="a1", agent_name="testbot", title="My", content="Content"),
        ])
        client.get_post = AsyncMock(side_effect=MoltbookError("Failed"))

        result = await client.get_my_posts(limit=5)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_my_posts_no_comment_fetch_when_disabled(self):
        """Line 521: include_comments=False skips comment fetch."""
        client = _make_client()
        client.get_self = AsyncMock(return_value=Agent(id="a1", name="testbot"))
        client.get_posts = AsyncMock(return_value=[
            Post(id="p1", agent_id="a1", agent_name="testbot", title="My", content="Content"),
        ])

        result = await client.get_my_posts(limit=5, include_comments=False)
        assert len(result) == 1


class TestClientComments:
    @pytest.mark.asyncio
    async def test_create_comment_daily_limit_reached(self):
        """Lines 681-683: Daily comment limit reached."""
        client = _make_client()
        client._request = AsyncMock()
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_comment = AsyncMock(return_value=False)
        status = {"daily_comments_remaining": 0}
        client._rate_limiter.get_status = MagicMock(return_value=status)
        client._rate_limiter.time_until_comment = MagicMock(return_value=30)

        with pytest.raises(RateLimitError, match="Daily comment limit"):
            await client.create_comment("p1", "content")

    @pytest.mark.asyncio
    async def test_create_comment_rate_limited(self):
        """Lines 684-685: Comment rate limited (not daily)."""
        client = _make_client()
        client._request = AsyncMock()
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_comment = AsyncMock(return_value=False)
        status = {"daily_comments_remaining": 10}
        client._rate_limiter.get_status = MagicMock(return_value=status)
        client._rate_limiter.time_until_comment = MagicMock(return_value=30)

        with pytest.raises(RateLimitError, match="Cannot comment yet"):
            await client.create_comment("p1", "content")

    @pytest.mark.asyncio
    async def test_downvote_comment(self):
        """Lines 704-705: Downvote comment."""
        client = _make_client()
        client._request = AsyncMock(return_value={})
        result = await client.downvote_comment("p1", "c1")
        assert result is True


class TestClientUtility:
    def test_get_rate_limit_status(self):
        """Line 838: get_rate_limit_status."""
        client = _make_client()
        status = client.get_rate_limit_status()
        assert "general_tokens" in status

    def test_get_proxy_stats(self):
        """Line 842: get_proxy_stats."""
        client = _make_client()
        stats = client.get_proxy_stats()
        assert "total_requests" in stats

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Lines 846-848: Health check succeeds."""
        client = _make_client()
        client._request = AsyncMock(return_value={"id": "a1"})
        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """Lines 849-851: Health check fails."""
        client = _make_client()
        client._request = AsyncMock(side_effect=MoltbookError("down"))
        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_close_session(self):
        """Lines 853-858: Close session."""
        client = _make_client()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        await client.close()
        mock_session.close.assert_called_once()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_close_no_session(self):
        """Line 855: Close with no session."""
        client = _make_client()
        client._session = None
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_ensure_session_creates_new(self):
        """Lines 157-171: Ensure session creates new session."""
        client = _make_client()
        client._session = None
        with patch("aiohttp.ClientSession") as MockSession:
            mock_instance = MagicMock()
            mock_instance.closed = False
            MockSession.return_value = mock_instance
            await client._ensure_session()
            assert client._session is not None

    @pytest.mark.asyncio
    async def test_ensure_session_with_challenge_handler(self):
        """Line 170-171: Session creation notifies challenge handler."""
        handler = MagicMock()
        handler.set_session = MagicMock()
        client = _make_client(challenge_handler=handler)
        client._session = None
        with patch("aiohttp.ClientSession") as MockSession:
            mock_instance = MagicMock()
            mock_instance.closed = False
            MockSession.return_value = mock_instance
            await client._ensure_session()
            handler.set_session.assert_called_once_with(mock_instance)

    @pytest.mark.asyncio
    async def test_refresh_session(self):
        """Lines 173-178: Refresh session closes old and creates new."""
        client = _make_client()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            await client._refresh_session()
        mock_session.close.assert_called_once()


class TestClientGetMyComments:
    @pytest.mark.asyncio
    async def test_get_my_comments(self):
        """Lines 534-535: Get own comments."""
        client = _make_client()
        client._request = AsyncMock(return_value={
            "comments": [
                {"id": "c1", "post_id": "p1", "agent_id": "a1",
                 "agent_name": "testbot", "content": "My comment"},
            ],
        })
        result = await client.get_my_comments(limit=10)
        assert len(result) == 1
        assert isinstance(result[0], Comment)


class TestClientSearch:
    @pytest.mark.asyncio
    async def test_search(self):
        """Lines 558-561: Search posts."""
        client = _make_client()
        client._request = AsyncMock(return_value={
            "posts": [],
            "total_count": 0,
        })
        result = await client.search("test query", limit=5)
        assert isinstance(result, SearchResult)


class TestClientFeed:
    @pytest.mark.asyncio
    async def test_get_feed(self):
        """Lines 539-542: Get feed items."""
        client = _make_client()
        client._request = AsyncMock(return_value={
            "items": [
                {"post": {"id": "p1", "title": "T", "content": "C",
                          "agent_id": "a1", "agent_name": "B"}},
            ],
        })
        result = await client.get_feed(limit=5)
        assert len(result) == 1
        assert isinstance(result[0], FeedItem)


class TestClientGetPosts:
    @pytest.mark.asyncio
    async def test_get_posts_with_submolt(self):
        """Lines 553-554: Get posts with submolt filter."""
        client = _make_client()
        client._request = AsyncMock(return_value={"posts": []})
        result = await client.get_posts(submolt="ai")
        assert result == []
        # Verify submolt param was passed
        call_args = client._request.call_args
        assert call_args[1]["params"]["submolt"] == "ai"


class TestClientCreatePost:
    @pytest.mark.asyncio
    async def test_create_post_rate_limited(self):
        """Lines 572-574: Create post rate limited."""
        client = _make_client()
        client._rate_limiter = MagicMock()
        client._rate_limiter.acquire_post = AsyncMock(return_value=False)
        client._rate_limiter.time_until_post = MagicMock(return_value=60)

        with pytest.raises(RateLimitError, match="Cannot post yet"):
            await client.create_post("title", "content")
