"""
Tests for the GitHub API client.
"""

import base64

import pytest

from overblick.plugins.github.client import GitHubAPIClient


class TestGitHubAPIClient:
    """Test the API client utility methods and initialization."""

    def test_decode_content_valid(self):
        """decode_content correctly decodes base64."""
        original = "def hello():\n    print('world')\n"
        encoded = base64.b64encode(original.encode()).decode()
        result = GitHubAPIClient.decode_content(encoded)
        assert result == original

    def test_decode_content_empty(self):
        """decode_content handles empty string."""
        assert GitHubAPIClient.decode_content("") == ""

    def test_decode_content_invalid(self):
        """decode_content handles invalid base64 gracefully."""
        result = GitHubAPIClient.decode_content("not-valid-base64!!!")
        # Should not raise, returns empty string or partial decode
        assert isinstance(result, str)

    def test_init_defaults(self):
        """Client initializes with sensible defaults."""
        client = GitHubAPIClient()
        assert client.rate_limit_remaining == 5000
        assert client._token == ""
        assert client._session is None

    def test_init_with_token(self):
        """Client accepts a PAT token."""
        client = GitHubAPIClient(token="ghp_test123")
        assert client._token == "ghp_test123"

    def test_init_custom_base_url(self):
        """Client accepts custom base URL (for GitHub Enterprise)."""
        client = GitHubAPIClient(base_url="https://github.example.com/api/v3")
        assert client._base_url == "https://github.example.com/api/v3"

    def test_update_rate_limit(self):
        """Rate limit tracking updates from response headers."""
        client = GitHubAPIClient()
        client._update_rate_limit({
            "X-RateLimit-Remaining": "4200",
            "X-RateLimit-Reset": "1700000000",
        })
        assert client._rate_limit_remaining == 4200
        assert client._rate_limit_reset == 1700000000

    def test_update_rate_limit_invalid_values(self):
        """Rate limit tracking handles invalid header values."""
        client = GitHubAPIClient()
        original_remaining = client._rate_limit_remaining
        client._update_rate_limit({
            "X-RateLimit-Remaining": "not-a-number",
        })
        # Should not change on invalid input
        assert client._rate_limit_remaining == original_remaining

    def test_update_rate_limit_missing_headers(self):
        """Rate limit tracking handles missing headers."""
        client = GitHubAPIClient()
        original = client._rate_limit_remaining
        client._update_rate_limit({})
        assert client._rate_limit_remaining == original
