"""
Integration tests — Secrets manager per-identity isolation.

Tests that the SecretsManager correctly:
- Encrypts secrets on disk (not plaintext)
- Isolates secrets between identities
- Caches decrypted values for performance
- Handles missing secrets gracefully
"""

from pathlib import Path

import pytest
import yaml


from overblick.core.security.secrets_manager import SecretsManager


@pytest.fixture
def secrets_dir(tmp_path):
    """Create a temporary secrets directory."""
    return tmp_path / "secrets"


@pytest.fixture
def secrets_mgr(secrets_dir):
    """Create a SecretsManager with a temp directory."""
    return SecretsManager(secrets_dir=secrets_dir)


class TestSecretsEncryption:
    """Secrets are encrypted on disk."""

    def test_stored_secret_not_plaintext(self, secrets_mgr, secrets_dir):
        """After set(), the YAML file does not contain the plaintext value."""
        secrets_mgr.set("anomal", "api_key", "sk_secret_12345")

        secrets_path = secrets_dir / "anomal.yaml"
        assert secrets_path.exists()

        raw = secrets_path.read_text()
        assert "sk_secret_12345" not in raw

        # File should contain encrypted (Fernet) data
        data = yaml.safe_load(raw)
        assert "api_key" in data
        # Fernet tokens start with 'gAAAAA' (base64 of version byte 0x80)
        assert data["api_key"].startswith("gAAAAA")

    def test_file_permissions_restricted(self, secrets_mgr, secrets_dir):
        """Secret files should have restricted permissions (0o600)."""
        secrets_mgr.set("anomal", "telegram_token", "bot123:secret")

        secrets_path = secrets_dir / "anomal.yaml"
        # On Unix, check file permissions
        mode = secrets_path.stat().st_mode & 0o777
        assert mode == 0o600


class TestSecretsRoundTrip:
    """Encrypt → store → load → decrypt cycle."""

    def test_set_then_get_returns_original(self, secrets_mgr):
        """set() + get() round-trips the original value."""
        secrets_mgr.set("anomal", "api_key", "my_secret_value")
        result = secrets_mgr.get("anomal", "api_key")
        assert result == "my_secret_value"

    def test_multiple_secrets_per_identity(self, secrets_mgr):
        """Multiple secrets stored for one identity all accessible."""
        secrets_mgr.set("anomal", "api_key", "key_123")
        secrets_mgr.set("anomal", "bot_token", "bot_456")
        secrets_mgr.set("anomal", "webhook_url", "https://hooks.example.com")

        assert secrets_mgr.get("anomal", "api_key") == "key_123"
        assert secrets_mgr.get("anomal", "bot_token") == "bot_456"
        assert secrets_mgr.get("anomal", "webhook_url") == "https://hooks.example.com"

    def test_list_keys(self, secrets_mgr):
        """list_keys() returns all stored key names."""
        secrets_mgr.set("anomal", "a", "val_a")
        secrets_mgr.set("anomal", "b", "val_b")

        keys = secrets_mgr.list_keys("anomal")
        assert sorted(keys) == ["a", "b"]

    def test_has_returns_true_for_existing(self, secrets_mgr):
        """has() returns True for existing secrets."""
        secrets_mgr.set("anomal", "exists", "value")
        assert secrets_mgr.has("anomal", "exists")

    def test_has_returns_false_for_missing(self, secrets_mgr):
        """has() returns False for non-existent secrets."""
        assert not secrets_mgr.has("anomal", "missing")


class TestSecretsIdentityIsolation:
    """Secrets are isolated between different identities."""

    def test_different_identities_separate_files(self, secrets_mgr, secrets_dir):
        """Each identity gets its own YAML file."""
        secrets_mgr.set("anomal", "key", "anomal_value")
        secrets_mgr.set("cherry", "key", "cherry_value")

        assert (secrets_dir / "anomal.yaml").exists()
        assert (secrets_dir / "cherry.yaml").exists()

    def test_same_key_different_values_per_identity(self, secrets_mgr):
        """Same key name returns different values per identity."""
        secrets_mgr.set("anomal", "api_key", "anomal_secret")
        secrets_mgr.set("cherry", "api_key", "cherry_secret")

        assert secrets_mgr.get("anomal", "api_key") == "anomal_secret"
        assert secrets_mgr.get("cherry", "api_key") == "cherry_secret"

    def test_identity_cannot_access_other_identity(self, secrets_mgr):
        """Getting a key from wrong identity returns None."""
        secrets_mgr.set("anomal", "private_key", "anomal_only")

        assert secrets_mgr.get("anomal", "private_key") == "anomal_only"
        assert secrets_mgr.get("cherry", "private_key") is None

    def test_list_keys_identity_scoped(self, secrets_mgr):
        """list_keys only shows keys for the specified identity."""
        secrets_mgr.set("anomal", "key_a", "val")
        secrets_mgr.set("anomal", "key_b", "val")
        secrets_mgr.set("cherry", "key_c", "val")

        anomal_keys = secrets_mgr.list_keys("anomal")
        cherry_keys = secrets_mgr.list_keys("cherry")

        assert sorted(anomal_keys) == ["key_a", "key_b"]
        assert cherry_keys == ["key_c"]


class TestSecretsMissing:
    """Graceful handling of missing secrets."""

    def test_get_nonexistent_identity(self, secrets_mgr):
        """Getting from an identity with no secrets file returns None."""
        assert secrets_mgr.get("nonexistent", "any_key") is None

    def test_get_nonexistent_key(self, secrets_mgr):
        """Getting a missing key from an existing identity returns None."""
        secrets_mgr.set("anomal", "existing", "value")
        assert secrets_mgr.get("anomal", "missing_key") is None

    def test_list_keys_empty_identity(self, secrets_mgr):
        """list_keys for non-existent identity returns empty list."""
        assert secrets_mgr.list_keys("ghost") == []


class TestSecretsCache:
    """Caching behavior for decrypted secrets."""

    def test_cache_hit_on_second_get(self, secrets_mgr):
        """Second get() for same secret uses cache (no re-decryption)."""
        secrets_mgr.set("anomal", "cached", "value")

        # First call loads from file + decrypts
        val1 = secrets_mgr.get("anomal", "cached")
        # Second call should use cache
        val2 = secrets_mgr.get("anomal", "cached")

        assert val1 == val2 == "value"
        # Cache should be populated
        assert "anomal" in secrets_mgr._cache
        assert secrets_mgr._cache["anomal"]["cached"] == "value"

    def test_set_updates_cache(self, secrets_mgr):
        """set() immediately updates the cache."""
        secrets_mgr.set("anomal", "key", "initial")
        assert secrets_mgr._cache["anomal"]["key"] == "initial"

        secrets_mgr.set("anomal", "key", "updated")
        assert secrets_mgr._cache["anomal"]["key"] == "updated"
        assert secrets_mgr.get("anomal", "key") == "updated"


class TestSecretsBulkImport:
    """Bulk import of plaintext secrets."""

    def test_load_plaintext_secrets(self, secrets_mgr):
        """load_plaintext_secrets encrypts and stores all provided secrets."""
        secrets_mgr.load_plaintext_secrets("anomal", {
            "api_key": "key_123",
            "bot_token": "bot_456",
            "webhook_url": "https://example.com",
        })

        assert secrets_mgr.get("anomal", "api_key") == "key_123"
        assert secrets_mgr.get("anomal", "bot_token") == "bot_456"
        assert secrets_mgr.get("anomal", "webhook_url") == "https://example.com"
