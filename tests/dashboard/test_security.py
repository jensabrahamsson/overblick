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


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_is_public(self, client):
        """Health endpoint should be accessible without auth."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "overblick-dashboard"
