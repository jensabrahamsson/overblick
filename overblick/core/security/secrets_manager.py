"""
Secrets manager — Fernet-encrypted secrets with keyring master key.

Secrets are stored encrypted in YAML files under config/secrets/<identity>.yaml.
The master encryption key is stored in the system keyring (macOS Keychain).

Security properties:
- Secrets never stored in plaintext on disk
- Master key protected by OS keyring
- Each identity has its own secrets file
- No environment variable fallback for secrets (intentional)
"""

import base64
import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Service name for keyring
_KEYRING_SERVICE = "overblick-secrets"


class SecretsManager:
    """
    Encrypted secrets manager.

    Usage:
        sm = SecretsManager(secrets_dir=Path("config/secrets"))
        sm.get("anomal", "api_key")  # Returns decrypted value
        sm.set("anomal", "api_key", "sk_xxx")  # Encrypts and saves
    """

    def __init__(self, secrets_dir: Path):
        self._secrets_dir = secrets_dir
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = None
        self._cache: dict[str, dict[str, str]] = {}

    def _get_fernet(self):
        """Lazy-initialize Fernet with master key from keyring."""
        if self._fernet is not None:
            return self._fernet

        from cryptography.fernet import Fernet

        master_key = self._get_or_create_master_key()
        self._fernet = Fernet(master_key)
        return self._fernet

    def _get_or_create_master_key(self) -> bytes:
        """Get master key from keyring, or create one if missing."""
        try:
            import keyring
            stored = keyring.get_password(_KEYRING_SERVICE, "master_key")
            if stored:
                return stored.encode()
        except Exception as e:
            logger.warning(f"Keyring unavailable ({e}), falling back to file-based key")

        # Fallback: key file in secrets directory
        key_file = self._secrets_dir / ".master_key"
        if key_file.exists():
            return key_file.read_bytes().strip()

        # Generate new master key
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key()

        # Try to store in keyring
        try:
            import keyring
            keyring.set_password(_KEYRING_SERVICE, "master_key", new_key.decode())
            logger.info("Master key stored in system keyring")
        except Exception:
            # Fallback to file
            key_file.write_bytes(new_key)
            key_file.chmod(0o600)
            logger.info("Master key stored in file (keyring unavailable)")

        return new_key

    def get(self, identity: str, key: str) -> Optional[str]:
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
            logger.error(f"Failed to decrypt secret '{key}' for '{identity}': {e}")
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
        secrets_path.chmod(0o600)

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
