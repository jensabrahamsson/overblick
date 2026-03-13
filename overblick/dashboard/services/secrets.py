"""
Secrets service — read-only access to secrets (existence checks and non-sensitive values).
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SecretsService:
    """Read-only access to secrets for the dashboard."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._secrets_dir = base_dir / "config" / "secrets"

    def has_secret(self, identity: str, key: str) -> bool:
        """
        Check if a secret key exists for an identity (without reading its value).

        This method reads the raw YAML file, not the decrypted value.
        """
        secrets_file = self._secrets_dir / f"{identity}.yaml"
        if not secrets_file.exists():
            return False
        try:
            import yaml

            with open(secrets_file) as f:
                data = yaml.safe_load(f) or {}
            return key in data
        except Exception:
            return False

    def get_readable_secret(self, identity: str, key: str) -> Optional[str]:
        """
        Get a non-sensitive secret value for pre-filling forms.

        Only safe for non-sensitive keys like 'gmail_address', 'telegram_chat_id',
        'principal_name', 'principal_email'. Sensitive keys (tokens, passwords)
        should never be exposed via this method.

        Returns the decrypted value if available, otherwise None.
        """
        # List of keys considered safe for read-only display
        safe_keys = {
            "gmail_address",
            "telegram_chat_id",
            "principal_name",
            "principal_email",
        }
        if key not in safe_keys:
            logger.warning(f"Attempt to read sensitive key '{key}' via get_readable_secret blocked")
            return None

        try:
            from overblick.core.security.secrets_manager import SecretsManager

            sm = SecretsManager(self._secrets_dir)
            return sm.get(identity, key)
        except Exception as e:
            logger.debug(f"Failed to read secret {identity}.{key}: {e}")
            return None

    def list_identities_with_secrets(self) -> list[str]:
        """
        List identities that have a secrets file.
        """
        if not self._secrets_dir.exists():
            return []
        identities = []
        for sf in self._secrets_dir.iterdir():
            if sf.suffix == ".yaml" and not sf.stem.startswith("."):
                identities.append(sf.stem)
        return identities
