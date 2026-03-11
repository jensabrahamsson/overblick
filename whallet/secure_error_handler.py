"""
Secure error handling utilities for Whallet.

Provides sanitization of error messages to prevent private key exposure
in logs, exceptions, and error responses.

CRITICAL: This module implements fail-closed security patterns.
When in doubt, sanitize aggressively.
"""

import re
from typing import Any, Optional

# Patterns that match private keys or sensitive hex data
# Private keys are 64 hex characters (32 bytes), optionally prefixed with 0x
PRIVATE_KEY_PATTERN = re.compile(r"(?:0x)?[a-fA-F0-9]{64}\b", re.IGNORECASE)

# Additional patterns for API keys (various lengths)
API_KEY_PATTERN = re.compile(
    r'(?:api[_-]?key|secret|token)[=:\s]+[\'"]?[\w\-]{20,}[\'"]?', re.IGNORECASE
)

# Pattern for hex strings that look like private keys in error messages
HEX_IN_ERROR_PATTERN = re.compile(
    r"(?:key|secret|private)[^:]*:\s*(?:0x)?[a-fA-F0-9]{32,}", re.IGNORECASE
)


class SecureErrorHandler:
    """
    Handles error sanitization to prevent private key exposure.

    Usage:
        handler = SecureErrorHandler()
        safe_msg = handler.sanitize_exception(exc)
        logger.error("Operation failed: %s", safe_msg)
    """

    REDACTED = "[REDACTED]"

    @classmethod
    def sanitize_message(cls, message: str) -> str:
        """
        Sanitize a message by removing any potential private key data.

        Args:
            message: The message to sanitize

        Returns:
            Sanitized message with sensitive data replaced by [REDACTED]
        """
        if not message:
            return message

        if not isinstance(message, str):
            message = str(message)

        # Replace 64-char hex strings (private keys)
        result = PRIVATE_KEY_PATTERN.sub(cls.REDACTED, message)

        # Replace API key patterns
        result = API_KEY_PATTERN.sub(f"key={cls.REDACTED}", result)

        # Replace hex-in-error patterns
        result = HEX_IN_ERROR_PATTERN.sub(f"key: {cls.REDACTED}", result)

        return result

    @classmethod
    def sanitize_exception(cls, exc: BaseException) -> str:
        """
        Safely convert an exception to a log-safe string.

        Args:
            exc: The exception to sanitize

        Returns:
            A safe string representation of the exception
        """
        if exc is None:
            return "Unknown error"

        # Get the exception type and message
        exc_type = type(exc).__name__
        exc_msg = str(exc)

        # Sanitize the message
        safe_msg = cls.sanitize_message(exc_msg)

        return f"{exc_type}: {safe_msg}"

    @classmethod
    def sanitize_dict(cls, data: dict, sensitive_keys: set | None = None) -> dict:
        """
        Recursively sanitize a dictionary, redacting sensitive values.

        Args:
            data: Dictionary to sanitize
            sensitive_keys: Set of keys whose values should be fully redacted

        Returns:
            Sanitized dictionary copy
        """
        if sensitive_keys is None:
            sensitive_keys = {
                "private_key",
                "privateKey",
                "secret",
                "api_key",
                "apiKey",
                "password",
                "token",
                "key",
            }

        if not isinstance(data, dict):
            return data

        result = {}
        for key, value in data.items():
            # Check if key is sensitive
            key_lower = str(key).lower()
            if any(s in key_lower for s in ["key", "secret", "password", "token"]):
                result[key] = cls.REDACTED
            elif isinstance(value, dict):
                result[key] = cls.sanitize_dict(value, sensitive_keys)
            elif isinstance(value, str):
                result[key] = cls.sanitize_message(value)
            else:
                result[key] = value

        return result

    @classmethod
    def safe_log_context(cls, **kwargs: Any) -> dict:
        """
        Create a safe logging context dictionary.

        Usage:
            logger.info("Operation", extra=SecureErrorHandler.safe_log_context(
                address=address, private_key=key, amount=amount
            ))
        """
        return cls.sanitize_dict(kwargs)


def sanitize_error(exc: BaseException) -> str:
    """Convenience function for sanitizing exceptions."""
    return SecureErrorHandler.sanitize_exception(exc)


def sanitize_message(msg: str) -> str:
    """Convenience function for sanitizing messages."""
    return SecureErrorHandler.sanitize_message(msg)


__all__ = ["SecureErrorHandler", "sanitize_error", "sanitize_message"]
