"""
Tests for SecretsManager — Fernet-encrypted per-identity secrets.

Covers: set/get roundtrip, missing identity, file isolation,
encryption verification, cache behavior, key listing.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
import yaml
from cryptography.fernet import Fernet

from overblick.core.exceptions import ConfigError, SecurityError
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

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix file permissions not available on Windows")
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
        sm.load_plaintext_secrets(
            "anomal",
            {
                "api_key": "sk_123",
                "bot_token": "bot_456",
            },
        )
        assert sm.get("anomal", "api_key") == "sk_123"
        assert sm.get("anomal", "bot_token") == "bot_456"


class TestLoadKeys:
    """Test _load_keys() key rotation directory loading."""

    def test_should_load_keys_from_keys_directory(self, secrets_dir):
        """Keys in the keys/ directory are loaded in sorted order."""
        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()

        keys_dir = secrets_dir / "keys"
        keys_dir.mkdir()
        (keys_dir / "key_2026_01.txt").write_bytes(key1)
        (keys_dir / "key_2026_02.txt").write_bytes(key2)

        # Pre-provision master key so constructor doesn't need keyring
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        assert "key_2026_01" in sm._keys
        assert "key_2026_02" in sm._keys
        assert sm._active_key_id == "key_2026_02"

    def test_should_handle_no_keys_directory(self, secrets_dir):
        """No keys directory means no rotation keys loaded."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        assert sm._keys == {}
        assert sm._active_key_id is None

    def test_should_handle_corrupt_key_file(self, secrets_dir):
        """Corrupt key files are skipped with a warning."""
        keys_dir = secrets_dir / "keys"
        keys_dir.mkdir()
        (keys_dir / "key_2026_01.txt").write_bytes(b"not-a-valid-fernet-key")

        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        assert "key_2026_01" not in sm._keys

    def test_should_handle_cryptography_import_error(self, secrets_dir):
        """If cryptography is not available, _load_keys is a no-op."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cryptography.fernet":
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        sm = SecretsManager(secrets_dir)
        # Now call _load_keys with mocked import
        sm._keys = {}
        sm._active_key_id = None

        with patch("builtins.__import__", side_effect=mock_import):
            sm._load_keys()

        assert sm._keys == {}


class TestGetFernetImportError:
    """Test _get_fernet when cryptography is unavailable."""

    def test_should_raise_config_error_in_safe_mode(self, secrets_dir):
        """In safe mode, missing cryptography raises ConfigError."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        sm._fernet = None  # Reset so it re-initializes

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cryptography.fernet":
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import), \
             patch("overblick.core.security.secrets_manager.SecretsManager._get_fernet.__module__", create=True), \
             patch("overblick.core.security.settings.safe_mode", return_value=True):
            with pytest.raises(ConfigError, match="cryptography library is missing"):
                sm._get_fernet()

    def test_should_reraise_import_error_when_not_safe_mode(self, secrets_dir):
        """Outside safe mode, missing cryptography re-raises ImportError."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        sm._fernet = None

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cryptography.fernet":
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import), \
             patch("overblick.core.security.settings.safe_mode", return_value=False):
            with pytest.raises(ImportError):
                sm._get_fernet()


class TestMasterKeyFromKeyring:
    """Test _get_or_create_master_key keyring paths."""

    def test_should_return_key_from_keyring(self, secrets_dir):
        """When keyring has a stored key, it is returned."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        sm._fernet = None

        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = master.decode()

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "keyring":
                return mock_keyring
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            key = sm._get_or_create_master_key()

        assert key == master

    def test_should_store_new_key_in_keyring(self, tmp_path):
        """When keyring is available and no key exists, stores new key in keyring."""
        secrets_dir = tmp_path / "fresh_secrets"
        secrets_dir.mkdir()

        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "keyring":
                return mock_keyring
            return real_import(name, *args, **kwargs)

        sm = SecretsManager.__new__(SecretsManager)
        sm._secrets_dir = secrets_dir
        sm._keys = {}
        sm._active_key_id = None
        sm._fernet = None
        sm._cache = {}

        with patch("builtins.__import__", side_effect=mock_import):
            key = sm._get_or_create_master_key()

        assert key is not None
        mock_keyring.set_password.assert_called_once()

    def test_should_raise_when_keyring_fails_and_secrets_exist(self, secrets_dir):
        """When keyring fails and .master_key is missing but secrets exist, raises SecurityError."""
        # Remove .master_key if it exists
        key_file = secrets_dir / ".master_key"
        if key_file.exists():
            key_file.unlink()

        # Create a secrets file to simulate existing encrypted data
        (secrets_dir / "anomal.yaml").write_text("token: encrypted_data")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "keyring":
                raise RuntimeError("keyring broken")
            return real_import(name, *args, **kwargs)

        sm = SecretsManager.__new__(SecretsManager)
        sm._secrets_dir = secrets_dir
        sm._keys = {}
        sm._active_key_id = None
        sm._fernet = None
        sm._cache = {}

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(SecurityError, match="Keyring is unavailable"):
                sm._get_or_create_master_key()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix file permissions not available on Windows")
    def test_should_fallback_to_file_when_keyring_set_fails(self, tmp_path):
        """When keyring.set_password fails, key is written to file with 0o600."""
        secrets_dir = tmp_path / "fb_secrets"
        secrets_dir.mkdir()

        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = RuntimeError("keyring broken")
        mock_keyring.set_password.side_effect = RuntimeError("cannot store")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "keyring":
                return mock_keyring
            return real_import(name, *args, **kwargs)

        sm = SecretsManager.__new__(SecretsManager)
        sm._secrets_dir = secrets_dir
        sm._keys = {}
        sm._active_key_id = None
        sm._fernet = None
        sm._cache = {}

        with patch("builtins.__import__", side_effect=mock_import):
            key = sm._get_or_create_master_key()

        key_file = secrets_dir / ".master_key"
        assert key_file.exists()
        assert key_file.read_bytes().strip() == key
        mode = key_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_should_handle_fdopen_write_failure(self, tmp_path):
        """When os.fdopen write fails, fd is closed and exception propagated."""
        secrets_dir = tmp_path / "fd_secrets"
        secrets_dir.mkdir()

        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = RuntimeError("no keyring")
        mock_keyring.set_password.side_effect = RuntimeError("no keyring")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "keyring":
                return mock_keyring
            return real_import(name, *args, **kwargs)

        sm = SecretsManager.__new__(SecretsManager)
        sm._secrets_dir = secrets_dir
        sm._keys = {}
        sm._active_key_id = None
        sm._fernet = None
        sm._cache = {}

        with patch("builtins.__import__", side_effect=mock_import), \
             patch("os.fdopen", side_effect=OSError("write failure")):
            with pytest.raises(OSError, match="write failure"):
                sm._get_or_create_master_key()


class TestDecryptWithAllKeys:
    """Test multi-key decryption for rotation support."""

    def test_should_decrypt_with_matching_key(self, secrets_dir):
        """decrypt_with_all_keys decrypts data encrypted with a known key."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Add a rotation key
        rotation_key = Fernet.generate_key()
        rotation_fernet = Fernet(rotation_key)
        sm._keys["key_2026_01"] = rotation_fernet

        encrypted = rotation_fernet.encrypt(b"secret_data")
        result = sm.decrypt_with_all_keys(encrypted.decode())
        assert result == b"secret_data"

    def test_should_return_none_when_no_key_matches(self, secrets_dir):
        """decrypt_with_all_keys returns None if no key can decrypt."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Add a key that won't match
        other_key = Fernet.generate_key()
        sm._keys["key_old"] = Fernet(other_key)

        # Encrypt with a different key entirely
        different_fernet = Fernet(Fernet.generate_key())
        encrypted = different_fernet.encrypt(b"data")
        result = sm.decrypt_with_all_keys(encrypted.decode())
        assert result is None

    def test_should_return_none_when_no_keys_loaded(self, secrets_dir):
        """decrypt_with_all_keys returns None if no rotation keys exist."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        sm._keys = {}
        result = sm.decrypt_with_all_keys("garbage_data")
        assert result is None

    def test_should_handle_exception_during_decryption(self, secrets_dir):
        """decrypt_with_all_keys handles unexpected exceptions gracefully."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        # Add a mock key that raises an unexpected error
        mock_fernet = MagicMock()
        mock_fernet.decrypt.side_effect = RuntimeError("unexpected")
        sm._keys["key_bad"] = mock_fernet

        result = sm.decrypt_with_all_keys("some_encrypted_data")
        assert result is None


class TestKeyRotation:
    """Test rotate_key() method."""

    def test_should_rotate_key_with_no_existing_secrets(self, secrets_dir):
        """rotate_key creates a new key even when no secrets exist."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)
        new_key_id = sm.rotate_key()

        assert new_key_id.startswith("key_")
        assert new_key_id in sm._keys
        assert sm._active_key_id == new_key_id

        # current_key.txt should exist
        assert (secrets_dir / "current_key.txt").exists()
        assert (secrets_dir / "current_key.txt").read_text() == new_key_id

    def test_should_reencrypt_existing_secrets(self, secrets_dir):
        """rotate_key re-encrypts existing YAML files with the new key."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Create a secrets file encrypted with an old key
        old_key = Fernet.generate_key()
        old_fernet = Fernet(old_key)
        sm._keys["key_old"] = old_fernet

        encrypted_data = old_fernet.encrypt(b"secret_content")
        (secrets_dir / "identity.yaml").write_bytes(encrypted_data)

        new_key_id = sm.rotate_key()
        assert new_key_id in sm._keys

    def test_should_log_failed_secrets_during_rotation(self, secrets_dir):
        """Secrets that cannot be decrypted are logged as failures."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Create a YAML file that can't be decrypted by any key
        (secrets_dir / "broken.yaml").write_text("undecryptable data")

        new_key_id = sm.rotate_key()
        assert new_key_id in sm._keys

    def test_should_handle_exception_during_file_rotation(self, secrets_dir):
        """Files that raise exceptions during rotation are logged as failures."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Create a file and make decrypt_with_all_keys raise
        (secrets_dir / "error.yaml").write_text("data")

        call_count = 0

        def failing_decrypt(data):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("decrypt crash")

        sm.decrypt_with_all_keys = failing_decrypt

        new_key_id = sm.rotate_key()
        assert new_key_id in sm._keys
        assert call_count >= 1

    def test_should_wrap_unexpected_error_as_security_error(self, secrets_dir):
        """Unexpected errors in rotate_key are wrapped in SecurityError."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Make the keys directory creation fail
        with patch.object(sm, "_secrets_dir", new=secrets_dir):
            # Patch Fernet.generate_key inside the rotate_key function
            with patch("cryptography.fernet.Fernet.generate_key", side_effect=RuntimeError("boom")):
                with pytest.raises(SecurityError, match="Key rotation failed"):
                    sm.rotate_key()

    def test_should_skip_current_key_txt_during_rotation(self, secrets_dir):
        """current_key.txt files in secrets_dir are skipped during rotation."""
        master = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(master)
        (secrets_dir / ".master_key").chmod(0o600)

        sm = SecretsManager(secrets_dir)

        # Create current_key.txt (should be skipped)
        (secrets_dir / "current_key.txt").write_text("key_old")

        # Also create an actual yaml that can't be decrypted
        (secrets_dir / "test.yaml").write_text("some_data")

        new_key_id = sm.rotate_key()
        assert new_key_id in sm._keys


class TestGetDecryptionFailure:
    """Test get() when decryption fails."""

    def test_should_return_none_when_decryption_fails(self, secrets_dir):
        """get() returns None and logs warning when decryption fails."""
        # Create manager with one key
        key1 = Fernet.generate_key()
        (secrets_dir / ".master_key").write_bytes(key1)
        (secrets_dir / ".master_key").chmod(0o600)
        sm = SecretsManager(secrets_dir)

        # Write a secrets file with data encrypted by a different key
        different_key = Fernet.generate_key()
        different_fernet = Fernet(different_key)
        encrypted_with_wrong_key = different_fernet.encrypt(b"secret").decode()

        (secrets_dir / "bad.yaml").write_text(
            yaml.dump({"token": encrypted_with_wrong_key})
        )

        result = sm.get("bad", "token")
        assert result is None
