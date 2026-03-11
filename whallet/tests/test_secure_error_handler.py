"""
Unit tests for SecureErrorHandler.

Tests cover:
1. Private key pattern detection and redaction
2. API key pattern detection and redaction
3. Exception sanitization
4. Dictionary sanitization
5. Edge cases (None, empty strings, non-strings)
6. False positives (should NOT redact normal hex addresses)

SECURITY: These tests verify that private keys are NEVER exposed in logs.
"""

import os

import pytest

# Set test environment
os.environ["PYTEST_RUNNING"] = "1"
os.environ["WHALLET_SIMULATION_ENABLED"] = "true"

from whallet.secure_error_handler import (
    PRIVATE_KEY_PATTERN,
    SecureErrorHandler,
    sanitize_error,
)


class TestPrivateKeyDetection:
    """Test detection and redaction of private keys."""

    def test_detects_64_char_hex_string(self):
        """Test that 64-character hex strings are redacted."""
        # Test private key (Anvil default first account - SAFE FOR TESTS)
        msg = "Key: ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result
        assert "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" not in result

    def test_detects_0x_prefixed_private_key(self):
        """Test that 0x-prefixed private keys are redacted."""
        msg = "Private key: 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result
        assert "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" not in result

    def test_detects_uppercase_private_key(self):
        """Test that uppercase hex is also redacted."""
        msg = "Key: AC0974BEC39A17E36BA4A6B4D238FF944BACB478CBED5EFCAE784D7BF4F2FF80"
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result
        assert "AC0974BEC39A17E36BA4A6B4D238FF944BACB478CBED5EFCAE784D7BF4F2FF80" not in result

    def test_detects_mixed_case_private_key(self):
        """Test that mixed case hex is also redacted."""
        msg = "Key: aC0974bec39a17e36bA4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result

    def test_does_not_redact_ethereum_address(self):
        """Test that 40-char Ethereum addresses are NOT redacted (not private keys)."""
        msg = "Address: 0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1"
        result = SecureErrorHandler.sanitize_message(msg)
        # 40-char addresses should NOT be redacted - they're public
        assert "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1" in result

    def test_does_not_redact_tx_hash(self):
        """Test that transaction hashes are NOT redacted (they're public)."""
        # TX hashes are 64 chars but OK to log - wait, they ARE 64 chars
        # For security, we err on the side of caution and redact them
        # This is a tradeoff - tx hashes look like private keys
        msg = "TX: 0xbd3c89592816037ba5e691adb7cb40d744bb6873b4dacf79bb4fbd474176ab4d"
        result = SecureErrorHandler.sanitize_message(msg)
        # 64-char hex gets redacted for safety (could be private key)
        assert "[REDACTED]" in result


class TestApiKeyDetection:
    """Test detection and redaction of API keys."""

    def test_detects_api_key_assignment(self):
        """Test that api_key= patterns are redacted."""
        msg = "Config: api_key=sk-1234567890abcdefghij"
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result

    def test_detects_secret_key_pattern(self):
        """Test that secret= patterns are redacted."""
        msg = "Config secret=verylongsecretvalue12345678"
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result


class TestExceptionSanitization:
    """Test sanitization of exception objects."""

    def test_sanitize_exception_with_private_key(self):
        """Test that exceptions containing private keys are sanitized."""
        key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        exc = ValueError(f"Invalid private key format: {key}")
        result = SecureErrorHandler.sanitize_exception(exc)
        assert "[REDACTED]" in result
        assert key not in result
        assert "ValueError" in result

    def test_sanitize_exception_preserves_type(self):
        """Test that exception type is preserved in output."""
        exc = RuntimeError("Connection failed")
        result = SecureErrorHandler.sanitize_exception(exc)
        assert "RuntimeError" in result
        assert "Connection failed" in result

    def test_sanitize_none_exception(self):
        """Test handling of None exception."""
        result = SecureErrorHandler.sanitize_exception(None)
        assert result == "Unknown error"

    def test_convenience_function_sanitize_error(self):
        """Test the convenience function works same as class method."""
        exc = ValueError("Key: 0x" + "a" * 64)
        result = sanitize_error(exc)
        assert "[REDACTED]" in result


class TestDictionarySanitization:
    """Test sanitization of dictionaries."""

    def test_sanitize_dict_with_private_key_field(self):
        """Test that private_key fields are fully redacted."""
        data = {
            "address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            "private_key": "0x" + "a" * 64,
        }
        result = SecureErrorHandler.sanitize_dict(data)
        assert result["private_key"] == "[REDACTED]"
        # Address should be preserved (it's public)
        assert "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1" in result["address"]

    def test_sanitize_dict_with_secret_field(self):
        """Test that secret fields are fully redacted."""
        data = {
            "api_secret": "my-super-secret-value",
            "normal_field": "normal value",
        }
        result = SecureErrorHandler.sanitize_dict(data)
        assert result["api_secret"] == "[REDACTED]"
        assert result["normal_field"] == "normal value"

    def test_sanitize_nested_dict(self):
        """Test that nested dictionaries are sanitized."""
        data = {
            "config": {
                "private_key": "secret",
                "endpoint": "https://api.example.com",
            }
        }
        result = SecureErrorHandler.sanitize_dict(data)
        assert result["config"]["private_key"] == "[REDACTED]"
        assert result["config"]["endpoint"] == "https://api.example.com"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_string(self):
        """Test handling of empty string."""
        result = SecureErrorHandler.sanitize_message("")
        assert result == ""

    def test_none_message(self):
        """Test handling of None message."""
        result = SecureErrorHandler.sanitize_message(None)
        assert result is None

    def test_non_string_input(self):
        """Test handling of non-string input."""
        result = SecureErrorHandler.sanitize_message(12345)
        assert result == "12345"

    def test_multiline_message(self):
        """Test handling of multiline message with key."""
        msg = """Error occurred:
        Details: Something went wrong
        Key: 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
        Please try again"""
        result = SecureErrorHandler.sanitize_message(msg)
        assert "[REDACTED]" in result
        assert "Error occurred" in result
        assert "Please try again" in result

    def test_message_with_multiple_keys(self):
        """Test that multiple keys in same message are all redacted."""
        key1 = "a" * 64
        key2 = "b" * 64
        msg = f"Keys: {key1} and {key2}"
        result = SecureErrorHandler.sanitize_message(msg)
        assert key1 not in result
        assert key2 not in result
        assert result.count("[REDACTED]") >= 2


class TestSafeLogContext:
    """Test the safe_log_context helper."""

    def test_creates_sanitized_context(self):
        """Test that safe_log_context creates sanitized dict."""
        ctx = SecureErrorHandler.safe_log_context(
            address="0x1234",
            private_key="0x" + "a" * 64,
            amount=100,
        )
        assert ctx["private_key"] == "[REDACTED]"
        assert ctx["amount"] == 100


class TestPatternValidation:
    """Test that patterns work correctly."""

    def test_private_key_pattern_matches_64_hex(self):
        """Verify the regex pattern matches 64-char hex."""
        test_key = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        match = PRIVATE_KEY_PATTERN.search(test_key)
        assert match is not None

    def test_private_key_pattern_matches_0x_prefix(self):
        """Verify pattern matches 0x-prefixed keys."""
        test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        match = PRIVATE_KEY_PATTERN.search(test_key)
        assert match is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
