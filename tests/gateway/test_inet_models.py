"""Tests for Internet Gateway pydantic models."""

from overblick.gateway.inet_models import APIKeyRecord, BanRecord, InetAuditEntry


class TestAPIKeyRecord:
    """Tests for the APIKeyRecord model."""

    def test_should_create_with_required_fields(self):
        record = APIKeyRecord(
            key_id="abc12345",
            name="my-laptop",
            key_hash="$2b$12$hash",
            key_prefix="sk-ob-xxxx",
            created_at=1700000000.0,
        )
        assert record.key_id == "abc12345"
        assert record.name == "my-laptop"
        assert record.key_prefix == "sk-ob-xxxx"

    def test_should_use_defaults_for_optional_fields(self):
        record = APIKeyRecord(
            key_id="abc12345",
            name="test",
            key_hash="hash",
            key_prefix="sk-ob-xxxx",
            created_at=1700000000.0,
        )
        assert record.expires_at is None
        assert record.revoked is False
        assert record.allowed_models == []
        assert record.allowed_backends == []
        assert record.max_tokens_cap == 4096
        assert record.requests_per_minute == 30
        assert record.total_requests == 0
        assert record.total_tokens_used == 0
        assert record.last_used_ip == ""

    def test_should_accept_all_fields(self):
        record = APIKeyRecord(
            key_id="abc12345",
            name="full",
            key_hash="hash",
            key_prefix="sk-ob-xxxx",
            created_at=1700000000.0,
            expires_at=1700100000.0,
            revoked=True,
            allowed_models=["qwen3:8b"],
            allowed_backends=["local"],
            max_tokens_cap=2048,
            requests_per_minute=10,
            total_requests=50,
            total_tokens_used=1000,
            last_used_ip="1.2.3.4",
        )
        assert record.revoked is True
        assert record.allowed_models == ["qwen3:8b"]
        assert record.allowed_backends == ["local"]
        assert record.max_tokens_cap == 2048
        assert record.requests_per_minute == 10
        assert record.total_requests == 50
        assert record.total_tokens_used == 1000
        assert record.last_used_ip == "1.2.3.4"
        assert record.expires_at == 1700100000.0

    def test_should_hide_hash_in_repr(self):
        record = APIKeyRecord(
            key_id="abc12345",
            name="test",
            key_hash="$2b$12$secrethash",
            key_prefix="sk-ob-xxxx",
            created_at=1700000000.0,
        )
        repr_str = repr(record)
        assert "$2b$12$secrethash" not in repr_str


class TestBanRecord:
    """Tests for the BanRecord model."""

    def test_should_create_with_all_fields(self):
        record = BanRecord(
            ip="1.2.3.4",
            reason="too many violations",
            banned_at=1700000000.0,
            expires_at=1700003600.0,
        )
        assert record.ip == "1.2.3.4"
        assert record.reason == "too many violations"
        assert record.banned_at == 1700000000.0
        assert record.expires_at == 1700003600.0
        assert record.violations == 0

    def test_should_accept_violations_count(self):
        record = BanRecord(
            ip="5.6.7.8",
            reason="brute force",
            banned_at=1700000000.0,
            expires_at=1700003600.0,
            violations=15,
        )
        assert record.violations == 15


class TestInetAuditEntry:
    """Tests for the InetAuditEntry model."""

    def test_should_create_with_required_fields(self):
        entry = InetAuditEntry(timestamp=1700000000.0)
        assert entry.timestamp == 1700000000.0
        assert entry.id == 0
        assert entry.key_id == ""
        assert entry.key_name == ""
        assert entry.source_ip == ""
        assert entry.method == ""
        assert entry.path == ""
        assert entry.model == ""
        assert entry.status_code == 0
        assert entry.request_tokens == 0
        assert entry.response_tokens == 0
        assert entry.latency_ms == 0.0
        assert entry.error == ""
        assert entry.violation == ""

    def test_should_accept_all_fields(self):
        entry = InetAuditEntry(
            id=42,
            timestamp=1700000000.0,
            key_id="abc",
            key_name="test-key",
            source_ip="1.2.3.4",
            method="POST",
            path="/v1/chat/completions",
            model="qwen3:8b",
            status_code=200,
            request_tokens=10,
            response_tokens=50,
            latency_ms=123.45,
            error="",
            violation="",
        )
        assert entry.id == 42
        assert entry.key_name == "test-key"
        assert entry.method == "POST"
        assert entry.status_code == 200
        assert entry.latency_ms == 123.45
