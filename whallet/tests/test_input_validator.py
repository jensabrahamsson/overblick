"""
Unit tests for InputValidator.

Tests cover:
1. Transaction amount validation (wei values)
2. Token address validation (Ethereum addresses)
3. Slippage validation (percentage range)
4. Gas price validation
5. ETH amount validation
6. Percentage validation
7. Edge cases (None, invalid types, overflow)

SECURITY: Validates fail-closed patterns - reject on ANY ambiguity.
"""

import os
from decimal import Decimal

import pytest

# Set test environment
os.environ["PYTEST_RUNNING"] = "1"
os.environ["WHALLET_SIMULATION_ENABLED"] = "true"

from whallet.input_validator import (
    DEAD_ADDRESS,
    MAX_ETH_AMOUNT,
    MAX_GAS_PRICE_WEI,
    MAX_SAFE_AMOUNT_WEI,
    MAX_SLIPPAGE_PERCENT,
    MAX_UINT256,
    MIN_GAS_PRICE_WEI,
    MIN_SLIPPAGE_PERCENT,
    ZERO_ADDRESS,
    InputValidator,
    ValidationError,
    validate_address,
    validate_amount,
    validate_slippage,
    validator,
)


class TestTransactionAmountValidation:
    """Test transaction amount validation in wei."""

    def test_valid_amount_int(self):
        """Test valid integer amount passes."""
        result = InputValidator.validate_transaction_amount(1000000000000000000)  # 1 ETH
        assert result == 1000000000000000000

    def test_valid_amount_string(self):
        """Test valid string amount is converted."""
        result = InputValidator.validate_transaction_amount("1000000000000000000")
        assert result == 1000000000000000000

    def test_valid_amount_decimal(self):
        """Test valid Decimal amount is converted."""
        result = InputValidator.validate_transaction_amount(Decimal("1000000000000000000"))
        assert result == 1000000000000000000

    def test_rejects_float_wei(self):
        """Test that float values are rejected for wei (precision loss risk)."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount(1.5)
        assert "Float values not allowed" in str(exc_info.value)

    def test_rejects_zero_amount(self):
        """Test that zero amount is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount(0)
        assert "Must be positive" in str(exc_info.value)

    def test_rejects_negative_amount(self):
        """Test that negative amount is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount(-1000)
        assert "Must be positive" in str(exc_info.value)

    def test_rejects_none_amount(self):
        """Test that None is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount(None)
        assert "Cannot be None" in str(exc_info.value)

    def test_rejects_exceeds_max_safe(self):
        """Test that amounts exceeding MAX_SAFE_AMOUNT_WEI are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount(MAX_SAFE_AMOUNT_WEI + 1)
        assert "maximum safe amount" in str(exc_info.value)

    def test_rejects_exceeds_uint256(self):
        """Test that amounts exceeding MAX_UINT256 are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount(MAX_UINT256 + 1)
        # Will fail on MAX_SAFE first, but let's test the pattern
        assert "Exceeds" in str(exc_info.value)

    def test_rejects_invalid_string(self):
        """Test that non-numeric strings are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_transaction_amount("not_a_number")
        assert "valid integer" in str(exc_info.value)

    def test_minimum_valid_amount(self):
        """Test that minimum 1 wei is valid."""
        result = InputValidator.validate_transaction_amount(1)
        assert result == 1


class TestTokenAddressValidation:
    """Test Ethereum token address validation."""

    def test_valid_address_lowercase(self):
        """Test valid lowercase address is checksummed."""
        result = InputValidator.validate_token_address("0x742d35cc6634c0532925a3b844bc9e7595f0beb1")
        # Web3 produces this checksum for this address
        assert result == "0x742d35cC6634c0532925A3b844bc9E7595F0beB1"

    def test_valid_address_checksummed(self):
        """Test valid checksummed address passes."""
        result = InputValidator.validate_token_address("0x742d35cC6634c0532925A3b844bc9E7595F0beB1")
        assert result == "0x742d35cC6634c0532925A3b844bc9E7595F0beB1"

    def test_rejects_none_address(self):
        """Test that None address is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address(None)
        assert "Cannot be None" in str(exc_info.value)

    def test_rejects_zero_address(self):
        """Test that zero address (0x0) is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address(ZERO_ADDRESS)
        assert "Zero address" in str(exc_info.value)

    def test_rejects_dead_address(self):
        """Test that dead/burn address is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address(DEAD_ADDRESS)
        assert "Dead address" in str(exc_info.value)

    def test_rejects_invalid_format_too_short(self):
        """Test that addresses too short are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address("0x742d35Cc6634C0532925a3b844Bc")
        assert "Invalid format" in str(exc_info.value)

    def test_rejects_invalid_format_too_long(self):
        """Test that addresses too long are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address("0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1ab")
        assert "Invalid format" in str(exc_info.value)

    def test_rejects_invalid_format_no_prefix(self):
        """Test that addresses without 0x prefix are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address("742d35Cc6634C0532925a3b844Bc9e7595f0bEb1")
        assert "Invalid format" in str(exc_info.value)

    def test_rejects_non_hex_characters(self):
        """Test that addresses with non-hex characters are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_token_address("0xZZZd35Cc6634C0532925a3b844Bc9e7595f0bEb1")
        assert "Invalid format" in str(exc_info.value)

    def test_strips_whitespace(self):
        """Test that whitespace is stripped from address."""
        result = InputValidator.validate_token_address(
            "  0x742d35cc6634c0532925a3b844bc9e7595f0beb1  "
        )
        assert result == "0x742d35cC6634c0532925A3b844bc9E7595F0beB1"


class TestSlippageValidation:
    """Test slippage percentage validation."""

    def test_valid_slippage_float(self):
        """Test valid float slippage passes."""
        result = InputValidator.validate_slippage(1.5)
        assert result == 1.5

    def test_valid_slippage_int(self):
        """Test valid integer slippage is converted to float."""
        result = InputValidator.validate_slippage(2)
        assert result == 2.0

    def test_valid_slippage_string(self):
        """Test valid string slippage is converted."""
        result = InputValidator.validate_slippage("1.5")
        assert result == 1.5

    def test_valid_slippage_decimal(self):
        """Test valid Decimal slippage is converted."""
        result = InputValidator.validate_slippage(Decimal("1.5"))
        assert result == 1.5

    def test_valid_slippage_min_boundary(self):
        """Test minimum valid slippage passes."""
        result = InputValidator.validate_slippage(MIN_SLIPPAGE_PERCENT)
        assert result == MIN_SLIPPAGE_PERCENT

    def test_valid_slippage_max_boundary(self):
        """Test maximum valid slippage passes."""
        result = InputValidator.validate_slippage(MAX_SLIPPAGE_PERCENT)
        assert result == MAX_SLIPPAGE_PERCENT

    def test_rejects_below_minimum(self):
        """Test that slippage below minimum is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_slippage(0.001)
        assert f"at least {MIN_SLIPPAGE_PERCENT}" in str(exc_info.value)

    def test_rejects_above_maximum(self):
        """Test that slippage above maximum is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_slippage(51.0)
        assert f"Cannot exceed {MAX_SLIPPAGE_PERCENT}" in str(exc_info.value)

    def test_rejects_none_slippage(self):
        """Test that None slippage is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_slippage(None)
        assert "Cannot be None" in str(exc_info.value)

    def test_rejects_nan_slippage(self):
        """Test that NaN slippage is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_slippage(float("nan"))
        assert "Cannot be NaN" in str(exc_info.value)

    def test_rejects_infinite_slippage(self):
        """Test that infinite slippage is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_slippage(float("inf"))
        assert "Cannot be infinite" in str(exc_info.value)


class TestGasPriceValidation:
    """Test gas price validation in wei."""

    def test_valid_gas_price(self):
        """Test valid gas price passes."""
        result = InputValidator.validate_gas_price(50_000_000_000)  # 50 gwei
        assert result == 50_000_000_000

    def test_valid_gas_price_string(self):
        """Test valid string gas price is converted."""
        result = InputValidator.validate_gas_price("50000000000")
        assert result == 50_000_000_000

    def test_valid_gas_price_min_boundary(self):
        """Test minimum valid gas price passes."""
        result = InputValidator.validate_gas_price(MIN_GAS_PRICE_WEI)
        assert result == MIN_GAS_PRICE_WEI

    def test_valid_gas_price_max_boundary(self):
        """Test maximum valid gas price passes."""
        result = InputValidator.validate_gas_price(MAX_GAS_PRICE_WEI)
        assert result == MAX_GAS_PRICE_WEI

    def test_rejects_below_minimum(self):
        """Test that gas price below minimum is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_gas_price(1_000_000)  # 0.001 gwei
        assert "Below minimum" in str(exc_info.value)

    def test_rejects_above_maximum(self):
        """Test that gas price above maximum is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_gas_price(MAX_GAS_PRICE_WEI + 1)
        assert "Exceeds maximum" in str(exc_info.value)

    def test_rejects_none_gas_price(self):
        """Test that None gas price is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_gas_price(None)
        assert "Cannot be None" in str(exc_info.value)


class TestEthAmountValidation:
    """Test ETH amount validation (human-readable format)."""

    def test_valid_eth_amount_float(self):
        """Test valid float ETH amount passes."""
        result = InputValidator.validate_eth_amount(0.5)
        assert result == 0.5

    def test_valid_eth_amount_int(self):
        """Test valid integer ETH amount is converted."""
        result = InputValidator.validate_eth_amount(1)
        assert result == 1.0

    def test_valid_eth_amount_string(self):
        """Test valid string ETH amount is converted."""
        result = InputValidator.validate_eth_amount("0.5")
        assert result == 0.5

    def test_valid_eth_amount_decimal(self):
        """Test valid Decimal ETH amount is converted."""
        result = InputValidator.validate_eth_amount(Decimal("0.5"))
        assert result == 0.5

    def test_rejects_below_dust_threshold(self):
        """Test that amounts below dust threshold are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_eth_amount(0.00001)
        assert "dust threshold" in str(exc_info.value)

    def test_rejects_above_maximum(self):
        """Test that amounts above maximum are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_eth_amount(MAX_ETH_AMOUNT + 1)
        assert "Exceeds maximum" in str(exc_info.value)

    def test_rejects_none_eth_amount(self):
        """Test that None ETH amount is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_eth_amount(None)
        assert "Cannot be None" in str(exc_info.value)

    def test_rejects_nan_eth_amount(self):
        """Test that NaN ETH amount is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_eth_amount(float("nan"))
        assert "Cannot be NaN" in str(exc_info.value)

    def test_rejects_infinite_eth_amount(self):
        """Test that infinite ETH amount is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_eth_amount(float("inf"))
        assert "Cannot be infinite" in str(exc_info.value)


class TestPercentageValidation:
    """Test percentage validation (0-100)."""

    def test_valid_percentage(self):
        """Test valid percentage passes."""
        result = InputValidator.validate_percentage(50.0)
        assert result == 50.0

    def test_valid_percentage_zero(self):
        """Test zero percentage is valid."""
        result = InputValidator.validate_percentage(0)
        assert result == 0.0

    def test_valid_percentage_hundred(self):
        """Test 100% is valid."""
        result = InputValidator.validate_percentage(100)
        assert result == 100.0

    def test_rejects_negative_percentage(self):
        """Test that negative percentage is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_percentage(-1)
        assert "Must be between 0 and 100" in str(exc_info.value)

    def test_rejects_above_hundred(self):
        """Test that percentage above 100 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_percentage(101)
        assert "Must be between 0 and 100" in str(exc_info.value)

    def test_rejects_none_percentage(self):
        """Test that None percentage is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_percentage(None)
        assert "Cannot be None" in str(exc_info.value)

    def test_custom_field_name_in_error(self):
        """Test that custom field name appears in error."""
        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_percentage(-1, field_name="tax_rate")
        assert "tax_rate" in str(exc_info.value)


class TestConvenienceFunctions:
    """Test convenience functions and validator instance."""

    def test_validate_amount_function(self):
        """Test convenience function for amount validation."""
        result = validate_amount(1000000000000000000)
        assert result == 1000000000000000000

    def test_validate_address_function(self):
        """Test convenience function for address validation."""
        result = validate_address("0x742d35cc6634c0532925a3b844bc9e7595f0beb1")
        assert result == "0x742d35cC6634c0532925A3b844bc9E7595F0beB1"

    def test_validate_slippage_function(self):
        """Test convenience function for slippage validation."""
        result = validate_slippage(1.5)
        assert result == 1.5

    def test_validator_instance_exists(self):
        """Test that convenience validator instance exists."""
        assert validator is not None
        assert isinstance(validator, InputValidator)


class TestValidationErrorDetails:
    """Test ValidationError exception details."""

    def test_error_includes_field_name(self):
        """Test that ValidationError includes field name."""
        err = ValidationError("my_field", "Something wrong")
        assert "my_field" in str(err)

    def test_error_includes_message(self):
        """Test that ValidationError includes message."""
        err = ValidationError("my_field", "Something wrong")
        assert "Something wrong" in str(err)

    def test_error_redacts_key_in_value(self):
        """Test that values containing 'key' are redacted."""
        err = ValidationError("private_key", "Invalid format", value="0xabcdef")
        assert err.value == "[REDACTED]"

    def test_error_preserves_normal_value(self):
        """Test that normal values are preserved."""
        err = ValidationError("amount", "Invalid", value=12345)
        assert err.value == 12345


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
