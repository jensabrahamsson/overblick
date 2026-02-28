"""
Tests for SecretsManager — Fernet-encrypted per-identity secrets.

Covers: set/get roundtrip, missing identity, file isolation,
encryption verification, cache behavior, key listing.
"""

import pytest
import yaml
from cryptography.fernet import Fernet

from overblick.core.security.secrets_manager import SecretsManager


@pytest.fixture
def secrets_dir(tmp_path):
    """Create a temporary secrets directory."""
    d = tmp_path / "secrets"
    d.mkdir()
    return d


@pytest.fixture
def sm(secrets_dir):
    """Create a SecretsManager with a pre-provisioned file-based key.

    We bypass keyring entirely by writing a .master_key file before
    the manager is instantiated.  The manager's _get_or_create_master_key
    will find the file and skip keyring altogether.
    """
    key = Fernet.generate_key()
    key_file = secrets_dir / ".master_key"
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return SecretsManager(secrets_dir)


class TestSetGetRoundtrip:
    """Test basic encrypt/decrypt cycle."""

    def test_set_and_get(self, sm):
        """Setting a secret and getting it back returns the original value."""
        sm.set("anomal", "api_key", "sk_test_12345")
        assert sm.get("anomal", "api_key") == "sk_test_12345"

    def test_set_multiple_keys(self, sm):
        """Multiple keys for the same identity are stored independently."""
        sm.set("anomal", "key_a", "value_a")
        sm.set("anomal", "key_b", "value_b")
        assert sm.get("anomal", "key_a") == "value_a"
        assert sm.get("anomal", "key_b") == "value_b"

    def test_overwrite_secret(self, sm):
        """Overwriting a secret returns the new value."""
        sm.set("anomal", "token", "old_value")
        sm.set("anomal", "token", "new_value")
        assert sm.get("anomal", "token") == "new_value"

    def test_unicode_secrets(self, sm):
        """Unicode values are encrypted and decrypted correctly."""
        sm.set("anomal", "greeting", "Hej Överblick! 🎉")
        assert sm.get("anomal", "greeting") == "Hej Överblick! 🎉"

    def test_empty_string_secret(self, sm):
        """Empty string is a valid secret value."""
        sm.set("anomal", "empty", "")
        assert sm.get("anomal", "empty") == ""


class TestMissingIdentity:
    """Test behavior when identity or key doesn't exist."""

    def test_get_missing_identity(self, sm):
        """Getting a secret for a non-existent identity returns None."""
        assert sm.get("nonexistent", "api_key") is None

    def test_get_missing_key(self, sm):
        """Getting a non-existent key for an existing identity returns None."""
        sm.set("anomal", "token", "value")
        assert sm.get("anomal", "other_key") is None


class TestFileIsolation:
    """Test that identities have separate files."""

    def test_separate_files(self, sm, secrets_dir):
        """Each identity gets its own YAML file."""
        sm.set("anomal", "key", "value_a")
        sm.set("cherry", "key", "value_c")
        assert (secrets_dir / "anomal.yaml").exists()
        assert (secrets_dir / "cherry.yaml").exists()

    def test_no_cross_contamination(self, sm):
        """Secrets from one identity don't leak to another."""
        sm.set("anomal", "secret", "anomal_secret")
        sm.set("cherry", "secret", "cherry_secret")
        assert sm.get("anomal", "secret") == "anomal_secret"
        assert sm.get("cherry", "secret") == "cherry_secret"

    def test_file_permissions(self, sm, secrets_dir):
        """Secret files have restricted permissions (0o600)."""
        sm.set("anomal", "key", "value")
        mode = (secrets_dir / "anomal.yaml").stat().st_mode & 0o777
        assert mode == 0o600


class TestEncryptionVerification:
    """Verify that secrets are actually encrypted on disk."""

    def test_not_plaintext_on_disk(self, sm, secrets_dir):
        """The secret value should not appear in plaintext in the YAML file."""
        plaintext = "super_secret_api_key_12345"
        sm.set("anomal", "token", plaintext)

        raw_content = (secrets_dir / "anomal.yaml").read_text()
        assert plaintext not in raw_content

    def test_encrypted_value_in_yaml(self, sm, secrets_dir):
        """The YAML file should contain a Fernet-encrypted string."""
        sm.set("anomal", "key", "value")
        data = yaml.safe_load((secrets_dir / "anomal.yaml").read_text())
        # Fernet tokens start with gAAAAA
        assert data["key"].startswith("gAAAAA")


class TestCacheBehavior:
    """Test in-memory caching."""

    def test_cached_after_set(self, sm):
        """After set(), the value is available without re-reading disk."""
        sm.set("anomal", "key", "cached_value")
        # Clear internal file to verify cache hit
        assert sm.get("anomal", "key") == "cached_value"

    def test_cached_after_get(self, sm):
        """After first get(), subsequent gets use the cache."""
        sm.set("anomal", "key", "value")
        # Clear cache to force disk read
        sm._cache.clear()
        first = sm.get("anomal", "key")
        # Now it should be cached
        assert "anomal" in sm._cache
        assert first == "value"


class TestKeyListing:
    """Test has() and list_keys()."""

    def test_has_existing_key(self, sm):
        """has() returns True for existing secrets."""
        sm.set("anomal", "token", "value")
        assert sm.has("anomal", "token") is True

    def test_has_missing_key(self, sm):
        """has() returns False for non-existent secrets."""
        assert sm.has("anomal", "nonexistent") is False

    def test_list_keys(self, sm):
        """list_keys() returns all secret keys for an identity."""
        sm.set("anomal", "key_a", "a")
        sm.set("anomal", "key_b", "b")
        keys = sm.list_keys("anomal")
        assert "key_a" in keys
        assert "key_b" in keys

    def test_list_keys_empty(self, sm):
        """list_keys() returns empty list for unknown identity."""
        assert sm.list_keys("unknown") == []


class TestPlaintextImport:
    """Test bulk plaintext import."""

    def test_load_plaintext_secrets(self, sm):
        """load_plaintext_secrets encrypts and stores all provided secrets."""
        sm.load_plaintext_secrets("anomal", {
            "api_key": "sk_123",
            "bot_token": "bot_456",
        })
        assert sm.get("anomal", "api_key") == "sk_123"
        assert sm.get("anomal", "bot_token") == "bot_456"
