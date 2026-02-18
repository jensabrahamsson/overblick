"""
Security utilities — rate limiting and input validation.

Rate limiting uses a simple in-memory sliding window.
Input validation uses Pydantic models for all form data.
"""

import time
import logging
from collections import defaultdict
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    In-memory sliding window rate limiter.

    Thread-safe for single-process use (asyncio).
    Keyed by client identifier (IP or session).
    """

    _MAX_TRACKED_KEYS = 2000

    def __init__(self):
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """
        Check if a request is within rate limits.

        Args:
            key: Unique identifier (e.g., "login:127.0.0.1")
            max_requests: Max allowed requests in window
            window_seconds: Window duration in seconds

        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        cutoff = now - window_seconds

        # Clean old entries
        self._windows[key] = [t for t in self._windows[key] if t > cutoff]

        if len(self._windows[key]) >= max_requests:
            return False

        # Evict oldest key if dict is too large (prevents unbounded memory growth)
        if len(self._windows) > self._MAX_TRACKED_KEYS:
            oldest = next(iter(self._windows))
            del self._windows[oldest]

        self._windows[key].append(now)
        return True

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        self._windows.pop(key, None)

    def clear(self) -> None:
        """Clear all rate limit state."""
        self._windows.clear()


# Input validation models

class LoginForm(BaseModel):
    """Login form validation."""
    password: str = Field(min_length=1, max_length=256)
    csrf_token: str = Field(min_length=1, max_length=128)


class OnboardingNameForm(BaseModel):
    """Step 1: Identity name."""
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(max_length=500, default="")
    display_name: str = Field(max_length=100, default="")

    @field_validator("name")
    @classmethod
    def name_is_lowercase(cls, v: str) -> str:
        return v.lower()


class OnboardingLLMForm(BaseModel):
    """Step 3: LLM settings."""
    model: str = Field(min_length=1, max_length=100, default="qwen3:8b")
    temperature: float = Field(ge=0.0, le=2.0, default=0.7)
    max_tokens: int = Field(ge=100, le=8000, default=2000)
    provider: str = Field(default="ollama", pattern=r"^(ollama|gateway|cloud)$")
    cloud_api_url: str = Field(default="", max_length=256)
    cloud_model: str = Field(default="", max_length=100)

    @field_validator("cloud_api_url")
    @classmethod
    def validate_cloud_api_url(cls, v: str) -> str:
        if v and not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("cloud_api_url must start with http:// or https://")
        return v


class OnboardingSecretsForm(BaseModel):
    """Step 5: Secret key-value pairs (names only validated here)."""
    keys: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, v: list[str]) -> list[str]:
        for key in v:
            if not key.replace("_", "").replace("-", "").isalnum():
                raise ValueError(f"Invalid secret key name: {key}")
        return v


class AuditFilterForm(BaseModel):
    """Audit trail filter parameters."""
    identity: str = Field(default="", max_length=64)
    category: str = Field(default="", max_length=64)
    action: str = Field(default="", max_length=64)
    hours: int = Field(default=24, ge=1, le=720)
    limit: int = Field(default=50, ge=1, le=500)
