"""
Secrets manager — Fernet-encrypted secrets with keyring master key.

Secrets are stored encrypted in YAML files under config/secrets/<identity>.yaml.
The master encryption key is stored in the system keyring (macOS Keychain).

Security properties:
- Secrets never stored in plaintext on disk
- Master key protected by OS keyring
- Each identity has its own secrets file
- No environment variable fallback for secrets (intentional)
- Key rotation support for security updates

Usage:
    sm = SecretsManager(secrets_dir=Path("config/secrets"))

    # Get/set secrets (uses current active key)
    sm.get("anomal", "api_key")
    sm.set("anomal", "api_key", "sk_xxx")

    # Rotate encryption key (e.g., after security incident)
    sm.rotate_key()  # Creates new key and re-encrypts all secrets

    # List keys for an identity
    sm.list_keys("anomal")
"""

import logging
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Service name for keyring
_KEYRING_SERVICE = "overblick-secrets"


class SecretsManager:
    """
    Encrypted secrets manager with key rotation support.

    Supports multiple encryption keys for graceful key rotation.
    Old encrypted data can still be decrypted with previous keys,
    while new secrets are always encrypted with the most recent key.

    Usage:
        sm = SecretsManager(secrets_dir=Path("config/secrets"))

        # Get/set secrets (uses current active key)
        sm.get("anomal", "api_key")
        sm.set("anomal", "api_key", "sk_xxx")

        # Rotate encryption key (e.g., after security incident)
        sm.rotate_key()  # Creates new key and re-encrypts all secrets

        # List keys for an identity
        sm.list_keys("anomal")
    """

    def __init__(self, secrets_dir: Path):
        self._secrets_dir = secrets_dir
        self._secrets_dir.mkdir(parents=True, exist_ok=True)

        # Key rotation support
        self._keys: dict[str, Fernet] = {}  # key_id -> Fernet instance
        self._active_key_id: str | None = None

        # Load existing keys if any
        self._load_keys()

        # Initialize with current fernet or create new one
        self._fernet: Fernet | None = None
        self._cache: dict[str, dict[str, str]] = {}

    def _load_keys(self) -> None:
        """Load all known encryption keys from the keys directory.

        This enables decryption of old secrets encrypted with previous keys.
        New secrets are always encrypted with the most recent key.
        """
        try:
            from cryptography.fernet import Fernet

            keys_dir = self._secrets_dir / "keys"
            if not keys_dir.exists():
                return  # No keys directory yet

            # Load all key files in sorted order (oldest first)
            for key_file in sorted(keys_dir.glob("key_*.txt")):
                try:
                    key_id = key_file.stem  # e.g., "key_2026_03"

                    with open(key_file, "rb") as f:
                        raw_key = f.read().strip()

                    fernet = Fernet(raw_key)
                    self._keys[key_id] = fernet

                except Exception as e:
                    logger.warning(f"Failed to load key from {key_file}: {e}")

            if self._keys:
                # Most recent key is the last one loaded
                self._active_key_id = list(self._keys.keys())[-1]
                logger.info(
                    "Loaded %d encryption keys (active: %s)",
                    len(self._keys),
                    self._active_key_id,
                )
            else:
                logger.debug("No existing encryption keys found")

        except ImportError:
            logger.warning("cryptography library not available - key rotation disabled")

    def _get_fernet(self):
        """Lazy-initialize Fernet with master key from keyring.

        For new secrets, always use the active key (most recent during rotation).
        For decryption attempts on old data, try all keys in reverse order.
        """
        if self._fernet is not None:
            return self._fernet

        try:
            from cryptography.fernet import Fernet
        except ImportError:
            from overblick.core.security.settings import safe_mode

            if safe_mode():
                raise RuntimeError(
                    "cryptography library is missing. Secrets cannot be decrypted "
                    "in safe mode. Install with 'pip install cryptography'."
                )
            raise

        master_key = self._get_or_create_master_key()
        self._fernet = Fernet(master_key)
        return self._fernet

    def _get_or_create_master_key(self) -> bytes:
        """Get master key from keyring, or create one if missing.

        Safety invariant: a new key is only generated on genuine first-time
        setup (nothing exists anywhere). If keyring throws an exception AND no
        fallback file exists, we raise RuntimeError to prevent silently
        generating a new key that would make all existing secrets permanently
        unreadable.
        """
        keyring_failed = False
        try:
            import keyring

            stored = keyring.get_password(_KEYRING_SERVICE, "master_key")
            if stored:
                return stored.encode()
        except Exception as e:
            keyring_failed = True
            logger.warning(f"Keyring unavailable ({e}), falling back to file-based key")

        # Fallback: key file in secrets directory
        key_file = self._secrets_dir / ".master_key"
        if key_file.exists():
            return key_file.read_bytes().strip()

        # If keyring threw an exception AND no file backup exists, do not
        # generate a new key — existing secrets encrypted with the old key
        # would become permanently unreadable.  Exception: fresh installs
        # with no secrets files yet can safely generate a new key.
        if keyring_failed:
            existing_secrets = list(self._secrets_dir.glob("*.yaml"))
            if existing_secrets:
                raise RuntimeError(
                    "Keyring is unavailable and no fallback key file exists. "
                    "Existing secrets may be encrypted with a keyring-stored key. "
                    "Restore keyring access or provide the .master_key file."
                )
            logger.info("Fresh install detected (no secrets files), generating new master key")

        # First-time setup only: generate and store new key
        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key()

        # Try to store in keyring
        try:
            import keyring

            keyring.set_password(_KEYRING_SERVICE, "master_key", new_key.decode())
            logger.info("Master key stored in system keyring")
        except Exception:
            # Fallback to file
            from overblick.shared.platform import set_restrictive_permissions

            key_file.write_bytes(new_key)
            set_restrictive_permissions(key_file)
            logger.info("Master key stored in file (keyring unavailable)")

        return new_key

    def decrypt_with_all_keys(self, encrypted_data: str) -> bytes | None:
        """Try to decrypt data with all known keys (for rotation support).

        Args:
            encrypted_data: Base64-encoded encrypted secret

        Returns:
            Decrypted bytes if successful, None if no key works
        """
        try:
            from cryptography.fernet import Fernet, InvalidToken

            # Try all keys in reverse order (newest first for efficiency)
            for key_id in reversed(list(self._keys.keys())):
                try:
                    fernet = self._keys[key_id]
                    return fernet.decrypt(encrypted_data.encode())
                except InvalidToken:
                    continue  # This key doesn't work, try next

        except Exception as e:
            logger.warning(f"Error during multi-key decryption: {e}")

        return None

    def rotate_key(self) -> str:
        """Create a new encryption key and re-encrypt all secrets.

        This method implements secure key rotation by:
        1. Generating a new Fernet key
        2. Saving it to the keys directory with timestamp-based ID
        3. Attempting to decrypt all existing secrets with old keys
        4. Re-encrypting them with the new key
        5. Updating the active key reference

        Use this method when:
        - Suspected security breach (rotate immediately)
        - Scheduled maintenance (quarterly recommended)
        - Personnel changes (key holders leaving organization)

        Returns:
            New key ID (e.g., "key_2026_03_09")

        Raises:
            RuntimeError: If no secrets exist to rotate or decryption fails
        """
        try:
            from cryptography.fernet import Fernet

            # Generate new key
            new_key = Fernet.generate_key()
            timestamp = datetime.now(UTC).strftime("%Y_%m_%d")
            new_key_id = f"key_{timestamp}"

            logger.info("Starting key rotation: %s", new_key_id)

            # Create keys directory and save new key
            keys_dir = self._secrets_dir / "keys"
            keys_dir.mkdir(exist_ok=True)

            key_file = keys_dir / f"{new_key_id}.txt"
            with open(key_file, "wb") as f:
                f.write(new_key)

            # Set restrictive permissions on new key file
            from overblick.shared.platform import set_restrictive_permissions

            set_restrictive_permissions(key_file)

            # Create Fernet instance for new key
            new_fernet = Fernet(new_key)

            # Re-encrypt all existing secrets
            rotated_count = 0
            failed_secrets = []

            for identity_file in self._secrets_dir.glob("*.yaml"):
                if identity_file.name == "current_key.txt":
                    continue

                try:
                    encrypted_data = identity_file.read_bytes()

                    # Try decrypting with old keys first
                    decrypted = self.decrypt_with_all_keys(encrypted_data.decode())

                    if decrypted is not None:
                        # Re-encrypt with new key
                        new_encrypted = new_fernet.encrypt(decrypted)
                        identity_file.write_bytes(new_encrypted)
                        rotated_count += 1
                        logger.debug("Rotated secrets for %s", identity_file.name)
                    else:
                        failed_secrets.append(identity_file.name)

                except Exception as e:
                    logger.error(
                        "Failed to rotate secrets for %s: %s",
                        identity_file.name,
                        e,
                        exc_info=True,
                    )
                    failed_secrets.append(identity_file.name)

            if rotated_count == 0 and not failed_secrets:
                # No existing secrets - just add the new key to our in-memory store
                self._keys[new_key_id] = new_fernet

            if failed_secrets:
                logger.warning(
                    "Key rotation completed with %d failures. Failed identities: %s",
                    len(failed_secrets),
                    ", ".join(failed_secrets),
                )

            # Update active key reference
            self._keys[new_key_id] = new_fernet
            self._active_key_id = new_key_id

            # Save current key ID for status tracking
            with open(self._secrets_dir / "current_key.txt", "w") as f:
                f.write(new_key_id)

            logger.info(
                "Key rotation completed: %d secrets rotated, %d failures",
                rotated_count,
                len(failed_secrets),
            )

            return new_key_id

        except Exception as e:
            logger.error("Key rotation failed: %s", e, exc_info=True)
            raise RuntimeError(f"Key rotation failed: {e}")

    def get(self, identity: str, key: str) -> str | None:
        """
        Get a decrypted secret.

        Args:
            identity: Identity name (e.g. "anomal")
            key: Secret key (e.g. "api_key")

        Returns:
            Decrypted secret value or None
        """
        # Check cache
        if identity in self._cache and key in self._cache[identity]:
            return self._cache[identity][key]

        # Load from file
        secrets = self._load_secrets_file(identity)
        if key not in secrets:
            return None

        # Decrypt
        encrypted = secrets[key]
        try:
            fernet = self._get_fernet()
            decrypted = fernet.decrypt(encrypted.encode()).decode()
            self._cache.setdefault(identity, {})[key] = decrypted
            return decrypted
        except Exception as e:
            logger.error(f"Failed to decrypt secret '{key}' for '{identity}': {e}", exc_info=True)
            return None

    def set(self, identity: str, key: str, value: str) -> None:
        """
        Encrypt and store a secret.

        Args:
            identity: Identity name
            key: Secret key
            value: Plaintext secret value
        """
        fernet = self._get_fernet()
        encrypted = fernet.encrypt(value.encode()).decode()

        # Load existing secrets
        secrets = self._load_secrets_file(identity)
        secrets[key] = encrypted

        # Save
        secrets_path = self._secrets_dir / f"{identity}.yaml"
        with open(secrets_path, "w") as f:
            yaml.safe_dump(secrets, f)
        from overblick.shared.platform import set_restrictive_permissions

        set_restrictive_permissions(secrets_path)

        # Update cache
        self._cache.setdefault(identity, {})[key] = value
        logger.info(f"Secret '{key}' stored for identity '{identity}'")

    def has(self, identity: str, key: str) -> bool:
        """Check if a secret exists."""
        secrets = self._load_secrets_file(identity)
        return key in secrets

    def list_keys(self, identity: str) -> list[str]:
        """List all secret keys for an identity."""
        secrets = self._load_secrets_file(identity)
        return list(secrets.keys())

    def _load_secrets_file(self, identity: str) -> dict:
        """Load encrypted secrets YAML for an identity."""
        secrets_path = self._secrets_dir / f"{identity}.yaml"
        if not secrets_path.exists():
            return {}
        with open(secrets_path) as f:
            return yaml.safe_load(f) or {}

    def load_plaintext_secrets(self, identity: str, data: dict[str, str]) -> None:
        """
        Import plaintext secrets (for migration from unencrypted config).

        Args:
            identity: Identity name
            data: Dict of key -> plaintext value
        """
        for key, value in data.items():
            self.set(identity, key, value)
        logger.info(f"Imported {len(data)} secrets for identity '{identity}'")
