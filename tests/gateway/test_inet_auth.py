"""Tests for Internet Gateway API key management."""

import time
from collections.abc import Generator
from pathlib import Path

import pytest

from overblick.gateway.inet_auth import APIKeyManager


@pytest.fixture
def key_manager(tmp_path: Path) -> Generator[APIKeyManager]:
    """Create a key manager with temporary database."""
    manager = APIKeyManager(tmp_path / "test_keys.db")
    yield manager
    manager.close()


class TestAPIKeyManager:
    """Tests for APIKeyManager CRUD operations."""

    def test_create_key(self, key_manager: APIKeyManager):
        raw_key, record = key_manager.create_key(name="test-laptop")

        assert raw_key.startswith("sk-ob-")
        assert len(raw_key) == 6 + 32  # "sk-ob-" + 32 hex chars
        assert record.name == "test-laptop"
        assert record.key_id  # non-empty
        assert record.key_prefix == raw_key[:12]
        assert not record.revoked
        assert record.expires_at is None

    def test_create_key_with_expiry(self, key_manager: APIKeyManager):
        _raw_key, record = key_manager.create_key(name="temp", expires_days=30)

        assert record.expires_at is not None
        assert record.expires_at > time.time()
        assert record.expires_at < time.time() + (31 * 86400)

    def test_create_key_with_permissions(self, key_manager: APIKeyManager):
        _raw_key, record = key_manager.create_key(
            name="restricted",
            allowed_models=["qwen3:8b"],
            allowed_backends=["local"],
            max_tokens_cap=2048,
            requests_per_minute=10,
        )

        assert record.allowed_models == ["qwen3:8b"]
        assert record.allowed_backends == ["local"]
        assert record.max_tokens_cap == 2048
        assert record.requests_per_minute == 10

    def test_verify_valid_key(self, key_manager: APIKeyManager):
        raw_key, original = key_manager.create_key(name="verify-test")

        verified = key_manager.verify_key(raw_key)

        assert verified is not None
        assert verified.key_id == original.key_id
        assert verified.name == "verify-test"

    def test_verify_invalid_key(self, key_manager: APIKeyManager):
        key_manager.create_key(name="exists")

        result = key_manager.verify_key("sk-ob-0000000000000000000000000000dead")
        assert result is None

    def test_verify_wrong_prefix(self, key_manager: APIKeyManager):
        result = key_manager.verify_key("wrong-prefix-key")
        assert result is None

    def test_verify_empty_key(self, key_manager: APIKeyManager):
        result = key_manager.verify_key("")
        assert result is None

    def test_verify_expired_key(self, key_manager: APIKeyManager):
        raw_key, record = key_manager.create_key(name="expired")

        # Manually expire the key
        key_manager._get_conn().execute(
            "UPDATE api_keys SET expires_at = ? WHERE key_id = ?",
            (time.time() - 1, record.key_id),
        )
        key_manager._get_conn().commit()

        result = key_manager.verify_key(raw_key)
        assert result is None

    def test_verify_revoked_key(self, key_manager: APIKeyManager):
        raw_key, record = key_manager.create_key(name="to-revoke")
        key_manager.revoke_key(record.key_id)

        result = key_manager.verify_key(raw_key)
        assert result is None

    def test_revoke_key(self, key_manager: APIKeyManager):
        raw_key, record = key_manager.create_key(name="revocable")

        assert key_manager.revoke_key(record.key_id) is True

        # Verify it's revoked
        result = key_manager.verify_key(raw_key)
        assert result is None

    def test_revoke_nonexistent_key(self, key_manager: APIKeyManager):
        assert key_manager.revoke_key("nonexistent") is False

    def test_rotate_key(self, key_manager: APIKeyManager):
        raw_key_1, record_1 = key_manager.create_key(
            name="rotatable",
            allowed_models=["qwen3:8b"],
            requests_per_minute=15,
        )

        result = key_manager.rotate_key(record_1.key_id)
        assert result is not None

        raw_key_2, _record_2 = result

        # New key works
        assert raw_key_2 != raw_key_1
        verified = key_manager.verify_key(raw_key_2)
        assert verified is not None
        assert verified.name == "rotatable"
        assert verified.allowed_models == ["qwen3:8b"]
        assert verified.requests_per_minute == 15

        # Old key is revoked
        old_result = key_manager.verify_key(raw_key_1)
        assert old_result is None

    def test_rotate_nonexistent_key(self, key_manager: APIKeyManager):
        result = key_manager.rotate_key("nonexistent")
        assert result is None

    def test_list_keys(self, key_manager: APIKeyManager):
        key_manager.create_key(name="key-1")
        key_manager.create_key(name="key-2")

        keys = key_manager.list_keys()
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert "key-1" in names
        assert "key-2" in names

        # Hash should be hidden
        for k in keys:
            assert k.key_hash == "[hidden]"

    def test_list_keys_empty(self, key_manager: APIKeyManager):
        assert key_manager.list_keys() == []

    def test_update_usage(self, key_manager: APIKeyManager):
        raw_key, record = key_manager.create_key(name="usage-test")

        key_manager.update_usage(record.key_id, tokens=100, ip="1.2.3.4")
        key_manager.update_usage(record.key_id, tokens=50, ip="5.6.7.8")

        verified = key_manager.verify_key(raw_key)
        assert verified is not None
        assert verified.total_requests == 2
        assert verified.total_tokens_used == 150
        assert verified.last_used_ip == "5.6.7.8"

    def test_key_uniqueness(self, key_manager: APIKeyManager):
        """Verify that two created keys are distinct."""
        raw_1, rec_1 = key_manager.create_key(name="unique-1")
        raw_2, rec_2 = key_manager.create_key(name="unique-2")

        assert raw_1 != raw_2
        assert rec_1.key_id != rec_2.key_id

    def test_should_handle_corrupted_models_json_in_verify(self, key_manager: APIKeyManager):
        """verify_key handles malformed allowed_models JSON gracefully."""
        raw_key, record = key_manager.create_key(name="corrupt-models")

        # Corrupt the allowed_models JSON in the database
        key_manager._get_conn().execute(
            "UPDATE api_keys SET allowed_models = 'NOT-JSON' WHERE key_id = ?",
            (record.key_id,),
        )
        key_manager._get_conn().commit()

        verified = key_manager.verify_key(raw_key)
        assert verified is not None
        assert verified.allowed_models == []

    def test_should_handle_corrupted_backends_json_in_verify(self, key_manager: APIKeyManager):
        """verify_key handles malformed allowed_backends JSON gracefully."""
        raw_key, record = key_manager.create_key(name="corrupt-backends")

        # Corrupt the allowed_backends JSON in the database
        key_manager._get_conn().execute(
            "UPDATE api_keys SET allowed_backends = 'NOT-JSON' WHERE key_id = ?",
            (record.key_id,),
        )
        key_manager._get_conn().commit()

        verified = key_manager.verify_key(raw_key)
        assert verified is not None
        assert verified.allowed_backends == []

    def test_should_return_none_when_bcrypt_matches_no_rows(self, key_manager: APIKeyManager):
        """verify_key returns None when prefix matches but hash doesn't."""
        raw_key, record = key_manager.create_key(name="match-test")
        prefix = raw_key[:12]

        # Create a fake key with same prefix but different hash
        # by inserting directly with a known different hash
        import bcrypt

        fake_hash = bcrypt.hashpw(b"sk-ob-totally-different-key-here", bcrypt.gensalt()).decode()
        key_manager._get_conn().execute(
            "DELETE FROM api_keys WHERE key_id = ?", (record.key_id,)
        )
        key_manager._get_conn().execute(
            """INSERT INTO api_keys
               (key_id, name, key_hash, key_prefix, created_at, revoked,
                allowed_models, allowed_backends, max_tokens_cap, requests_per_minute)
            VALUES (?, ?, ?, ?, ?, 0, '[]', '[]', 4096, 30)""",
            ("fakeid01", "fake", fake_hash, prefix, 1700000000.0),
        )
        key_manager._get_conn().commit()

        result = key_manager.verify_key(raw_key)
        assert result is None

    def test_should_rollback_rotate_on_failure(self, key_manager: APIKeyManager):
        """rotate_key rolls back if insert fails mid-transaction."""
        from unittest.mock import patch

        raw_key, record = key_manager.create_key(name="rollback-test")

        # Get the real connection and wrap it to intercept specific calls
        real_conn = key_manager._get_conn()

        # We need to make the COMMIT fail after INSERT, which triggers rollback.
        # The simplest approach: make the connection raise on INSERT inside transaction.
        original_execute = real_conn.execute
        insert_seen = False

        class WrappedConn:
            """Wrapper that fails on the INSERT inside rotate_key's transaction."""

            def __getattr__(self, name):
                return getattr(real_conn, name)

            def execute(self, sql, params=None):
                nonlocal insert_seen
                if "INSERT INTO api_keys" in sql and insert_seen is False:
                    # Only fail the second INSERT (rotate, not create_key which already ran)
                    insert_seen = True
                    if params is not None:
                        return original_execute(sql, params)
                if insert_seen and "COMMIT" in sql:
                    raise RuntimeError("simulated commit failure")
                if params is not None:
                    return original_execute(sql, params)
                return original_execute(sql)

        wrapped = WrappedConn()
        with patch.object(key_manager, "_get_conn", return_value=wrapped):
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                key_manager.rotate_key(record.key_id)

        # Original key should still be valid (rollback happened)
        verified = key_manager.verify_key(raw_key)
        assert verified is not None

    def test_should_handle_corrupted_models_json_in_list(self, key_manager: APIKeyManager):
        """list_keys handles malformed allowed_models JSON gracefully."""
        _, record = key_manager.create_key(name="corrupt-list")
        key_manager._get_conn().execute(
            "UPDATE api_keys SET allowed_models = 'BAD' WHERE key_id = ?",
            (record.key_id,),
        )
        key_manager._get_conn().commit()

        keys = key_manager.list_keys()
        assert len(keys) == 1
        assert keys[0].allowed_models == []

    def test_should_handle_corrupted_backends_json_in_list(self, key_manager: APIKeyManager):
        """list_keys handles malformed allowed_backends JSON gracefully."""
        _, record = key_manager.create_key(name="corrupt-list-backends")
        key_manager._get_conn().execute(
            "UPDATE api_keys SET allowed_backends = 'BAD' WHERE key_id = ?",
            (record.key_id,),
        )
        key_manager._get_conn().commit()

        keys = key_manager.list_keys()
        assert len(keys) == 1
        assert keys[0].allowed_backends == []

    def test_should_handle_close_with_broken_connection(self, tmp_path: Path):
        """close() handles exceptions from broken connections."""
        from unittest.mock import MagicMock

        manager = APIKeyManager(tmp_path / "broken_close.db")
        manager._get_conn()  # create a connection

        broken_conn = MagicMock()
        broken_conn.close.side_effect = Exception("broken")
        manager._connections = [broken_conn]

        manager.close()  # should not raise
        assert manager._connections == []
