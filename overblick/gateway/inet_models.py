"""
Pydantic models for the Internet Gateway.

Defines API key records, ban records, and audit entries
for the secure reverse proxy to the internal LLM Gateway.
"""

from pydantic import BaseModel, Field


class APIKeyRecord(BaseModel):
    """A registered API key with permissions and usage stats."""

    key_id: str = Field(description="8 hex char identifier")
    name: str = Field(description="Human-readable name (e.g. 'my-laptop')")
    key_hash: str = Field(description="bcrypt hash of the full key", repr=False)
    key_prefix: str = Field(description="First 12 chars for display (e.g. 'sk-ob-xxxx')")
    created_at: float = Field(description="Unix timestamp of creation")
    expires_at: float | None = Field(
        default=None, description="Unix timestamp of expiry, None = never"
    )
    revoked: bool = Field(default=False, description="Whether key has been revoked")
    allowed_models: list[str] = Field(
        default_factory=list, description="Allowed models (empty = all)"
    )
    allowed_backends: list[str] = Field(
        default_factory=list, description="Allowed backends (empty = all)"
    )
    max_tokens_cap: int = Field(default=4096, description="Per-request max_tokens cap")
    requests_per_minute: int = Field(default=30, description="Per-key rate limit")
    total_requests: int = Field(default=0, description="Lifetime request count")
    total_tokens_used: int = Field(default=0, description="Lifetime token usage")
    last_used_ip: str = Field(default="", description="Last IP that used this key")


class BanRecord(BaseModel):
    """An IP ban entry with expiration."""

    ip: str = Field(description="Banned IP address")
    reason: str = Field(description="Reason for ban")
    banned_at: float = Field(description="Unix timestamp of ban")
    expires_at: float = Field(description="Unix timestamp of ban expiry")
    violations: int = Field(default=0, description="Number of violations that triggered the ban")


class InetAuditEntry(BaseModel):
    """A single internet gateway audit log entry."""

    id: int = Field(default=0)
    timestamp: float = Field(description="Unix timestamp")
    key_id: str = Field(default="", description="API key ID used")
    key_name: str = Field(default="", description="API key name")
    source_ip: str = Field(default="", description="Client IP address")
    method: str = Field(default="", description="HTTP method")
    path: str = Field(default="", description="Request path")
    model: str = Field(default="", description="LLM model requested")
    status_code: int = Field(default=0, description="Response HTTP status")
    request_tokens: int = Field(default=0, description="Tokens in request")
    response_tokens: int = Field(default=0, description="Tokens in response")
    latency_ms: float = Field(default=0.0, description="Request latency in ms")
    error: str = Field(default="", description="Error message if failed")
    violation: str = Field(default="", description="Security violation type if any")
