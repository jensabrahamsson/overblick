"""
Tests for the GitHub API client.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from overblick.plugins.github.client import (
    GitHubAPIClient,
    GitHubAPIError,
    RateLimitError,
)


class TestGitHubAPIClientInit:
    """Test client initialization."""

    def test_should_init_with_defaults(self):
        client = GitHubAPIClient()
        assert client.rate_limit_remaining == 5000
        assert client._token == ""
        assert client._session is None

    def test_should_accept_token(self):
        client = GitHubAPIClient(token="ghp_test123")
        assert client._token == "ghp_test123"

    def test_should_accept_custom_base_url(self):
        client = GitHubAPIClient(base_url="https://github.example.com/api/v3")
        assert client._base_url == "https://github.example.com/api/v3"

    def test_should_strip_trailing_slash_from_base_url(self):
        client = GitHubAPIClient(base_url="https://api.github.com/")
        assert client._base_url == "https://api.github.com"


class TestDecodeContent:
    """Test base64 content decoding."""

    def test_should_decode_valid_base64(self):
        original = "def hello():\n    print('world')\n"
        encoded = base64.b64encode(original.encode()).decode()
        assert GitHubAPIClient.decode_content(encoded) == original

    def test_should_return_empty_for_empty_string(self):
        assert GitHubAPIClient.decode_content("") == ""

    def test_should_handle_invalid_base64(self):
        result = GitHubAPIClient.decode_content("not-valid-base64!!!")
        assert isinstance(result, str)


class TestUpdateRateLimit:
    """Test rate limit header parsing."""

    def test_should_update_from_valid_headers(self):
        client = GitHubAPIClient()
        client._update_rate_limit({
            "X-RateLimit-Remaining": "4200",
            "X-RateLimit-Reset": "1700000000",
        })
        assert client._rate_limit_remaining == 4200
        assert client._rate_limit_reset == 1700000000

    def test_should_handle_invalid_remaining(self):
        client = GitHubAPIClient()
        original = client._rate_limit_remaining
        client._update_rate_limit({"X-RateLimit-Remaining": "not-a-number"})
        assert client._rate_limit_remaining == original

    def test_should_handle_invalid_reset(self):
        client = GitHubAPIClient()
        original = client._rate_limit_reset
        client._update_rate_limit({"X-RateLimit-Reset": "bad"})
        assert client._rate_limit_reset == original

    def test_should_handle_missing_headers(self):
        client = GitHubAPIClient()
        original = client._rate_limit_remaining
        client._update_rate_limit({})
        assert client._rate_limit_remaining == original


class TestRateLimitError:
    """Test RateLimitError exception."""

    def test_should_store_reset_at(self):
        err = RateLimitError("exhausted", reset_at=1700000000)
        assert err.reset_at == 1700000000
        assert "exhausted" in str(err)

    def test_should_default_reset_at_to_zero(self):
        err = RateLimitError("exhausted")
        assert err.reset_at == 0


class TestEnsureSession:
    """Test session creation."""

    @pytest.mark.asyncio
    async def test_should_create_session_without_token(self):
        client = GitHubAPIClient()
        await client._ensure_session()
        assert client._session is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_should_create_session_with_token(self):
        client = GitHubAPIClient(token="ghp_test")
        await client._ensure_session()
        assert client._session is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_should_recreate_closed_session(self):
        client = GitHubAPIClient()
        await client._ensure_session()
        session1 = client._session
        await client._session.close()
        await client._ensure_session()
        assert client._session is not session1
        await client.close()


def _make_mock_response(status, body=None, json_data=None, headers=None):
    """Helper to create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=body or "")
    return resp


def _make_client_with_mock_session(responses):
    """Create a client with a mock session that returns given responses in order."""
    client = GitHubAPIClient(token="test")
    call_idx = [0]

    class MockCtx:
        async def __aenter__(self_ctx):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(responses):
                return responses[idx]
            return responses[-1]

        async def __aexit__(self_ctx, *args):
            pass

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.request = MagicMock(return_value=MockCtx())
    mock_session.close = AsyncMock()
    client._session = mock_session
    return client


class TestRequest:
    """Test the core _request method."""

    @pytest.mark.asyncio
    async def test_should_return_json_on_200(self):
        resp = _make_mock_response(200, json_data={"data": "ok"})
        client = _make_client_with_mock_session([resp])
        result = await client._request("GET", "/test")
        assert result == {"data": "ok"}

    @pytest.mark.asyncio
    async def test_should_return_json_on_201(self):
        resp = _make_mock_response(201, json_data={"id": 123})
        client = _make_client_with_mock_session([resp])
        result = await client._request("POST", "/test")
        assert result == {"id": 123}

    @pytest.mark.asyncio
    async def test_should_raise_rate_limit_on_403(self):
        resp = _make_mock_response(403, body="rate limited", headers={"X-RateLimit-Remaining": "0"})
        client = _make_client_with_mock_session([resp])
        with pytest.raises(RateLimitError):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_should_raise_rate_limit_on_429(self):
        resp = _make_mock_response(429, body="rate limited", headers={"X-RateLimit-Remaining": "0"})
        client = _make_client_with_mock_session([resp])
        with pytest.raises(RateLimitError):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_should_not_raise_rate_limit_on_403_with_remaining(self):
        """403 with remaining > 0 is not a rate limit error."""
        resp = _make_mock_response(403, body="forbidden", headers={"X-RateLimit-Remaining": "100"})
        client = _make_client_with_mock_session([resp])
        with pytest.raises(GitHubAPIError, match="403"):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_should_raise_on_404(self):
        resp = _make_mock_response(404, body="not found")
        client = _make_client_with_mock_session([resp])
        with pytest.raises(GitHubAPIError, match="Not found"):
            await client._request("GET", "/repos/x/y")

    @pytest.mark.asyncio
    async def test_should_raise_on_401(self):
        resp = _make_mock_response(401, body="bad credentials")
        client = _make_client_with_mock_session([resp])
        with pytest.raises(GitHubAPIError, match="Authentication failed"):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_should_retry_on_500_then_raise(self):
        resp = _make_mock_response(500, body="server error")
        client = _make_client_with_mock_session([resp, resp, resp])
        with patch("overblick.plugins.github.client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GitHubAPIError, match="after 3 attempts"):
                await client._request("GET", "/test", retry_count=3)

    @pytest.mark.asyncio
    async def test_should_retry_on_502_then_succeed(self):
        fail = _make_mock_response(502, body="bad gateway")
        ok = _make_mock_response(200, json_data={"ok": True})
        client = _make_client_with_mock_session([fail, fail, ok])
        with patch("overblick.plugins.github.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test", retry_count=3)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_should_retry_on_503(self):
        fail = _make_mock_response(503, body="unavailable")
        ok = _make_mock_response(200, json_data={"ok": True})
        client = _make_client_with_mock_session([fail, ok])
        with patch("overblick.plugins.github.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test", retry_count=3)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_should_retry_on_504(self):
        fail = _make_mock_response(504, body="timeout")
        ok = _make_mock_response(200, json_data={"ok": True})
        client = _make_client_with_mock_session([fail, ok])
        with patch("overblick.plugins.github.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test", retry_count=3)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_should_raise_on_unhandled_status(self):
        resp = _make_mock_response(422, body="unprocessable")
        client = _make_client_with_mock_session([resp])
        with pytest.raises(GitHubAPIError, match="422"):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_should_retry_on_client_error_then_succeed(self):
        client = GitHubAPIClient(token="test")
        call_count = [0]
        ok_resp = _make_mock_response(200, json_data={"ok": True})

        class MockCtx:
            async def __aenter__(self_ctx):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise aiohttp.ClientError("connection reset")
                return ok_resp

            async def __aexit__(self_ctx, *args):
                pass

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request = MagicMock(return_value=MockCtx())
        mock_session.close = AsyncMock()
        client._session = mock_session

        with patch("overblick.plugins.github.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test", retry_count=3)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_should_raise_after_max_client_errors(self):
        client = GitHubAPIClient(token="test")

        class MockCtx:
            async def __aenter__(self_ctx):
                raise aiohttp.ClientError("connection refused")

            async def __aexit__(self_ctx, *args):
                pass

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.request = MagicMock(return_value=MockCtx())
        mock_session.close = AsyncMock()
        client._session = mock_session

        with patch("overblick.plugins.github.client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GitHubAPIError, match="Request failed after"):
                await client._request("GET", "/test", retry_count=2)

    @pytest.mark.asyncio
    async def test_should_raise_max_retries_exceeded_on_zero_retries(self):
        client = GitHubAPIClient(token="test")
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        with pytest.raises(GitHubAPIError, match="max retries exceeded"):
            await client._request("GET", "/test", retry_count=0)


class TestAPIEndpoints:
    """Test high-level API endpoint methods delegate correctly."""

    @pytest.fixture
    def client(self):
        c = GitHubAPIClient(token="test")
        c._request = AsyncMock(return_value=[])
        return c

    @pytest.mark.asyncio
    async def test_list_issues(self, client):
        await client.list_issues("owner/repo", state="open", per_page=50)
        client._request.assert_called_once()
        args = client._request.call_args
        assert args[0][1] == "/repos/owner/repo/issues"

    @pytest.mark.asyncio
    async def test_list_issues_with_since_and_labels(self, client):
        await client.list_issues("o/r", since="2026-01-01", labels="bug,help")
        params = client._request.call_args[1]["params"]
        assert params["since"] == "2026-01-01"
        assert params["labels"] == "bug,help"

    @pytest.mark.asyncio
    async def test_list_issues_caps_per_page(self, client):
        await client.list_issues("o/r", per_page=200)
        params = client._request.call_args[1]["params"]
        assert params["per_page"] == 100

    @pytest.mark.asyncio
    async def test_list_issue_comments(self, client):
        await client.list_issue_comments("o/r", 42)
        args = client._request.call_args
        assert "/issues/42/comments" in args[0][1]

    @pytest.mark.asyncio
    async def test_list_issue_comments_with_since(self, client):
        await client.list_issue_comments("o/r", 42, since="2026-01-01")
        params = client._request.call_args[1]["params"]
        assert params["since"] == "2026-01-01"

    @pytest.mark.asyncio
    async def test_create_comment(self, client):
        client._request = AsyncMock(return_value={"id": 99})
        result = await client.create_comment("o/r", 42, "Hello!")
        assert result == {"id": 99}

    @pytest.mark.asyncio
    async def test_get_file_tree(self, client):
        client._request = AsyncMock(return_value={"sha": "abc", "tree": []})
        result = await client.get_file_tree("o/r", "main")
        assert result["sha"] == "abc"

    @pytest.mark.asyncio
    async def test_get_file_content_with_ref(self, client):
        client._request = AsyncMock(return_value={"content": "abc"})
        await client.get_file_content("o/r", "src/main.py", ref="abc123")
        params = client._request.call_args[1]["params"]
        assert params["ref"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_file_content_no_ref(self, client):
        client._request = AsyncMock(return_value={"content": "abc"})
        await client.get_file_content("o/r", "README.md")
        params = client._request.call_args[1]["params"]
        assert "ref" not in params

    @pytest.mark.asyncio
    async def test_list_pulls(self, client):
        await client.list_pulls("o/r", state="open")
        args = client._request.call_args
        assert "/pulls" in args[0][1]

    @pytest.mark.asyncio
    async def test_get_pull(self, client):
        client._request = AsyncMock(return_value={"number": 42})
        result = await client.get_pull("o/r", 42)
        assert result["number"] == 42

    @pytest.mark.asyncio
    async def test_merge_pull_with_title(self, client):
        client._request = AsyncMock(return_value={"merged": True})
        await client.merge_pull("o/r", 42, merge_method="squash", commit_title="title")
        json_arg = client._request.call_args[1]["json"]
        assert json_arg["merge_method"] == "squash"
        assert json_arg["commit_title"] == "title"

    @pytest.mark.asyncio
    async def test_merge_pull_no_title(self, client):
        client._request = AsyncMock(return_value={"merged": True})
        await client.merge_pull("o/r", 42)
        json_arg = client._request.call_args[1]["json"]
        assert "commit_title" not in json_arg

    @pytest.mark.asyncio
    async def test_create_pull_review_with_body(self, client):
        client._request = AsyncMock(return_value={})
        await client.create_pull_review("o/r", 42, event="APPROVE", body="LGTM")
        json_arg = client._request.call_args[1]["json"]
        assert json_arg["event"] == "APPROVE"
        assert json_arg["body"] == "LGTM"

    @pytest.mark.asyncio
    async def test_create_pull_review_no_body(self, client):
        client._request = AsyncMock(return_value={})
        await client.create_pull_review("o/r", 42, event="APPROVE")
        json_arg = client._request.call_args[1]["json"]
        assert "body" not in json_arg

    @pytest.mark.asyncio
    async def test_list_pull_reviews(self, client):
        await client.list_pull_reviews("o/r", 42)
        assert "/reviews" in client._request.call_args[0][1]

    @pytest.mark.asyncio
    async def test_get_check_runs(self, client):
        client._request = AsyncMock(return_value={"check_runs": []})
        await client.get_check_runs("o/r", "sha123")
        assert "/check-runs" in client._request.call_args[0][1]

    @pytest.mark.asyncio
    async def test_get_combined_status(self, client):
        client._request = AsyncMock(return_value={"state": "success"})
        result = await client.get_combined_status("o/r", "sha123")
        assert result["state"] == "success"

    @pytest.mark.asyncio
    async def test_get_rate_limit(self, client):
        client._request = AsyncMock(return_value={"resources": {}})
        result = await client.get_rate_limit()
        assert "resources" in result


class TestGetPullDiff:
    """Test the special get_pull_diff method (non-JSON)."""

    @pytest.mark.asyncio
    async def test_should_return_diff_text_on_200(self):
        client = GitHubAPIClient(token="test")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="diff --git a/file.py")
        mock_response.headers = {"X-RateLimit-Remaining": "4900"}

        class MockCtx:
            async def __aenter__(self_ctx):
                return mock_response

            async def __aexit__(self_ctx, *args):
                pass

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=MockCtx())
        mock_session.close = AsyncMock()
        client._session = mock_session

        result = await client.get_pull_diff("o/r", 42)
        assert "diff --git" in result

    @pytest.mark.asyncio
    async def test_should_raise_on_non_200(self):
        client = GitHubAPIClient(token="test")
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.headers = {}

        class MockCtx:
            async def __aenter__(self_ctx):
                return mock_response

            async def __aexit__(self_ctx, *args):
                pass

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=MockCtx())
        mock_session.close = AsyncMock()
        client._session = mock_session

        with pytest.raises(GitHubAPIError, match="Failed to get PR diff"):
            await client.get_pull_diff("o/r", 42)

    @pytest.mark.asyncio
    async def test_should_include_auth_header_with_token(self):
        client = GitHubAPIClient(token="ghp_test")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="diff")
        mock_response.headers = {}

        class MockCtx:
            async def __aenter__(self_ctx):
                return mock_response

            async def __aexit__(self_ctx, *args):
                pass

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=MockCtx())
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.get_pull_diff("o/r", 1)
        call_kwargs = mock_session.get.call_args[1]
        assert "Authorization" in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_should_not_include_auth_without_token(self):
        client = GitHubAPIClient(token="")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="diff")
        mock_response.headers = {}

        class MockCtx:
            async def __aenter__(self_ctx):
                return mock_response

            async def __aexit__(self_ctx, *args):
                pass

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=MockCtx())
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.get_pull_diff("o/r", 1)
        call_kwargs = mock_session.get.call_args[1]
        assert "Authorization" not in call_kwargs["headers"]


class TestClose:
    """Test session cleanup."""

    @pytest.mark.asyncio
    async def test_should_close_open_session(self):
        client = GitHubAPIClient()
        await client._ensure_session()
        assert client._session is not None
        await client.close()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_should_handle_no_session(self):
        client = GitHubAPIClient()
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_should_handle_already_closed_session(self):
        client = GitHubAPIClient()
        mock_session = MagicMock()
        mock_session.closed = True
        client._session = mock_session
        await client.close()  # Should not call close on already-closed session
