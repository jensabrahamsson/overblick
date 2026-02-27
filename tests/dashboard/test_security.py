"""Tests for security utilities — rate limiting, input validation."""

import time
import pytest
from pydantic import ValidationError
from overblick.dashboard.security import (
    RateLimiter, LoginForm, OnboardingNameForm, OnboardingLLMForm, AuditFilterForm,
)


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter()
        for _ in range(5):
            assert rl.check("key", max_requests=5, window_seconds=60)

    def test_blocks_over_limit(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.check("key", max_requests=5, window_seconds=60)
        assert rl.check("key", max_requests=5, window_seconds=60) is False

    def test_different_keys_independent(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.check("key1", max_requests=5, window_seconds=60)
        assert rl.check("key1", max_requests=5, window_seconds=60) is False
        assert rl.check("key2", max_requests=5, window_seconds=60) is True

    def test_reset(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.check("key", max_requests=5, window_seconds=60)
        assert rl.check("key", max_requests=5, window_seconds=60) is False
        rl.reset("key")
        assert rl.check("key", max_requests=5, window_seconds=60) is True

    def test_clear(self):
        rl = RateLimiter()
        rl.check("key1", max_requests=1, window_seconds=60)
        rl.check("key2", max_requests=1, window_seconds=60)
        rl.clear()
        assert rl.check("key1", max_requests=1, window_seconds=60) is True
        assert rl.check("key2", max_requests=1, window_seconds=60) is True


class TestInputValidation:
    def test_login_form_valid(self):
        form = LoginForm(password="test123", csrf_token="abc123")
        assert form.password == "test123"

    def test_login_form_empty_password(self):
        with pytest.raises(ValidationError):
            LoginForm(password="", csrf_token="abc")

    def test_onboarding_name_valid(self):
        form = OnboardingNameForm(name="myagent", description="A test agent")
        assert form.name == "myagent"

    def test_onboarding_name_with_underscores(self):
        form = OnboardingNameForm(name="my_agent")
        assert form.name == "my_agent"

    def test_onboarding_name_invalid_chars(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="my agent!")

    def test_onboarding_name_starts_with_number(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="123agent")

    def test_onboarding_llm_valid(self):
        form = OnboardingLLMForm(model="qwen3:8b", temperature=0.7, max_tokens=2000)
        assert form.model == "qwen3:8b"

    def test_onboarding_llm_temperature_bounds(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(temperature=3.0)
        with pytest.raises(ValidationError):
            OnboardingLLMForm(temperature=-0.1)

    def test_onboarding_llm_max_tokens_bounds(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(max_tokens=50)
        with pytest.raises(ValidationError):
            OnboardingLLMForm(max_tokens=10000)

    def test_audit_filter_defaults(self):
        form = AuditFilterForm()
        assert form.hours == 24
        assert form.limit == 50

    def test_audit_filter_bounds(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(hours=0)
        with pytest.raises(ValidationError):
            AuditFilterForm(limit=0)


class TestRateLimiterBounds:
    def test_windows_does_not_grow_unbounded(self):
        """_windows dict is bounded to _MAX_TRACKED_KEYS entries."""
        rl = RateLimiter()
        limit = rl._MAX_TRACKED_KEYS
        # Add one more key than the limit (each key allowed once, window=60s)
        for i in range(limit + 1):
            rl.check(f"key:{i}", max_requests=5, window_seconds=60)
        assert len(rl._windows) <= limit

    def test_eviction_removes_oldest_key(self):
        """When _windows overflows, the oldest key is evicted."""
        rl = RateLimiter()
        limit = rl._MAX_TRACKED_KEYS
        # Fill to the limit so the first key "key:0" is the oldest
        for i in range(limit):
            rl.check(f"key:{i}", max_requests=5, window_seconds=60)
        # Adding one more should evict "key:0"
        rl.check("key:overflow", max_requests=5, window_seconds=60)
        assert "key:0" not in rl._windows

    def test_current_key_not_evicted(self):
        """The key being checked should never be evicted (even if it's newest)."""
        rl = RateLimiter()
        limit = rl._MAX_TRACKED_KEYS
        for i in range(limit):
            rl.check(f"key:{i}", max_requests=5, window_seconds=60)
        # The new key should survive eviction
        result = rl.check("new_key", max_requests=5, window_seconds=60)
        assert result is True
        assert "new_key" in rl._windows

    def test_eviction_preserves_active_keys(self):
        """Eviction removes least-recently-active key, not most-recently-active."""
        rl = RateLimiter()
        limit = rl._MAX_TRACKED_KEYS
        for i in range(limit):
            rl.check(f"key:{i}", max_requests=5, window_seconds=60)
        # Re-check the first key to make it recent
        rl.check("key:0", max_requests=5, window_seconds=60)
        # Overflow should evict key:1 (oldest), not key:0 (recently active)
        rl.check("overflow", max_requests=5, window_seconds=60)
        assert "key:0" in rl._windows
        assert "key:1" not in rl._windows


class TestOnboardingLLMValidation:
    def test_cloud_api_url_empty_is_allowed(self):
        """Empty cloud_api_url is valid (provider may not require it)."""
        form = OnboardingLLMForm(cloud_api_url="")
        assert form.cloud_api_url == ""

    def test_cloud_api_url_https_valid(self):
        form = OnboardingLLMForm(cloud_api_url="https://api.example.com/v1")
        assert form.cloud_api_url == "https://api.example.com/v1"

    def test_cloud_api_url_http_valid(self):
        form = OnboardingLLMForm(cloud_api_url="http://localhost:1234/v1")
        assert form.cloud_api_url == "http://localhost:1234/v1"

    def test_cloud_api_url_without_scheme_raises(self):
        """cloud_api_url without http/https prefix must raise ValidationError."""
        with pytest.raises(ValidationError):
            OnboardingLLMForm(cloud_api_url="api.example.com/v1")

    def test_cloud_api_url_ftp_scheme_raises(self):
        """Non-http(s) scheme must raise ValidationError (SSRF prevention)."""
        with pytest.raises(ValidationError):
            OnboardingLLMForm(cloud_api_url="ftp://evil.com/data")


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_is_public(self, client):
        """Health endpoint should be accessible without auth."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "overblick-dashboard"
