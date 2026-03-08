"""
Input validation utilities for Whallet.

Implements fail-closed validation patterns for all user inputs.
SECURITY: Reject on ANY ambiguity - never assume input is valid.
"""
import re
from typing import Union
from decimal import Decimal
from web3 import Web3


# Constants for validation limits
MAX_UINT256 = (1 << 256) - 1
MAX_SAFE_AMOUNT_WEI = 10**24  # 1 million ETH in wei - reasonable upper bound
MIN_AMOUNT_WEI = 1  # Minimum 1 wei

MIN_SLIPPAGE_PERCENT = 0.01  # 0.01% minimum
MAX_SLIPPAGE_PERCENT = 50.0  # 50% maximum (already very dangerous)
DEFAULT_SLIPPAGE_PERCENT = 1.0  # 1% default

MIN_GAS_PRICE_WEI = 1_000_000_000  # 1 gwei minimum
MAX_GAS_PRICE_WEI = 10_000_000_000_000  # 10,000 gwei maximum (extreme congestion)

MAX_ETH_AMOUNT = 10000.0  # 10,000 ETH maximum per transaction
MIN_ETH_AMOUNT = 0.0001  # 0.0001 ETH minimum (dust threshold)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"

# Ethereum address pattern (40 hex chars with 0x prefix)
ETH_ADDRESS_PATTERN = re.compile(r'^0x[a-fA-F0-9]{40}$')


class ValidationError(ValueError):
    """Custom exception for validation failures with detailed context."""

    def __init__(self, field: str, message: str, value: any = None):
        self.field = field
        self.value = "[REDACTED]" if "key" in field.lower() else value
        super().__init__(f"{field}: {message}")


class InputValidator:
    """
    Validates all user inputs with fail-closed security patterns.

    Usage:
        validator = InputValidator()
        validator.validate_transaction_amount(amount_wei)
        validator.validate_token_address(token_address)
        validator.validate_slippage(slippage_percent)
    """

    @staticmethod
    def validate_transaction_amount(amount_wei: Union[int, str, Decimal]) -> int:
        """
        Validate transaction amount in wei.

        Args:
            amount_wei: Amount in wei (must be positive integer)

        Returns:
            Validated amount as int

        Raises:
            ValidationError: If amount is invalid
        """
        # Type coercion
        try:
            if isinstance(amount_wei, str):
                amount_wei = int(amount_wei)
            elif isinstance(amount_wei, Decimal):
                amount_wei = int(amount_wei)
            elif isinstance(amount_wei, float):
                # Floats are dangerous for wei - reject
                raise ValidationError(
                    "amount_wei",
                    "Float values not allowed for wei amounts (precision loss)",
                    amount_wei
                )
        except (ValueError, TypeError) as e:
            raise ValidationError(
                "amount_wei",
                f"Must be a valid integer: {e}",
                amount_wei
            )

        # Range validation
        if amount_wei is None:
            raise ValidationError("amount_wei", "Cannot be None")

        if not isinstance(amount_wei, int):
            raise ValidationError(
                "amount_wei",
                f"Must be integer, got {type(amount_wei).__name__}",
                amount_wei
            )

        if amount_wei <= 0:
            raise ValidationError(
                "amount_wei",
                f"Must be positive (got {amount_wei})",
                amount_wei
            )

        if amount_wei > MAX_SAFE_AMOUNT_WEI:
            raise ValidationError(
                "amount_wei",
                f"Exceeds maximum safe amount ({MAX_SAFE_AMOUNT_WEI} wei)",
                amount_wei
            )

        if amount_wei > MAX_UINT256:
            raise ValidationError(
                "amount_wei",
                "Exceeds MAX_UINT256 (overflow risk)",
                amount_wei
            )

        return amount_wei

    @staticmethod
    def validate_token_address(address: str) -> str:
        """
        Validate Ethereum token address.

        Args:
            address: Token address (0x-prefixed hex string)

        Returns:
            Checksummed address

        Raises:
            ValidationError: If address is invalid
        """
        if address is None:
            raise ValidationError("token_address", "Cannot be None")

        if not isinstance(address, str):
            raise ValidationError(
                "token_address",
                f"Must be string, got {type(address).__name__}",
                address
            )

        # Strip whitespace
        address = address.strip()

        # Check format
        if not ETH_ADDRESS_PATTERN.match(address):
            raise ValidationError(
                "token_address",
                "Invalid format (must be 0x followed by 40 hex characters)",
                address
            )

        # Check for zero address
        if address.lower() == ZERO_ADDRESS.lower():
            raise ValidationError(
                "token_address",
                "Zero address (0x0) is not allowed",
                address
            )

        # Check for dead address (common burn address)
        if address.lower() == DEAD_ADDRESS.lower():
            raise ValidationError(
                "token_address",
                "Dead address (0x...dEaD) is not allowed",
                address
            )

        # Convert to checksum address (validates checksum if mixed case)
        try:
            checksummed = Web3.to_checksum_address(address)
        except ValueError as e:
            raise ValidationError(
                "token_address",
                f"Invalid checksum: {e}",
                address
            )

        return checksummed

    @staticmethod
    def validate_slippage(slippage_percent: Union[float, int, str, Decimal]) -> float:
        """
        Validate slippage tolerance percentage.

        Args:
            slippage_percent: Slippage tolerance (0.01 to 50.0)

        Returns:
            Validated slippage as float

        Raises:
            ValidationError: If slippage is invalid
        """
        # Type coercion
        try:
            if isinstance(slippage_percent, str):
                slippage_percent = float(slippage_percent)
            elif isinstance(slippage_percent, (int, Decimal)):
                slippage_percent = float(slippage_percent)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                "slippage_percent",
                f"Must be a valid number: {e}",
                slippage_percent
            )

        if slippage_percent is None:
            raise ValidationError("slippage_percent", "Cannot be None")

        if not isinstance(slippage_percent, (int, float)):
            raise ValidationError(
                "slippage_percent",
                f"Must be numeric, got {type(slippage_percent).__name__}",
                slippage_percent
            )

        # Check for NaN/Inf
        if slippage_percent != slippage_percent:  # NaN check
            raise ValidationError("slippage_percent", "Cannot be NaN")

        if abs(slippage_percent) == float('inf'):
            raise ValidationError("slippage_percent", "Cannot be infinite")

        # Range validation
        if slippage_percent < MIN_SLIPPAGE_PERCENT:
            raise ValidationError(
                "slippage_percent",
                f"Must be at least {MIN_SLIPPAGE_PERCENT}% (got {slippage_percent}%)",
                slippage_percent
            )

        if slippage_percent > MAX_SLIPPAGE_PERCENT:
            raise ValidationError(
                "slippage_percent",
                f"Cannot exceed {MAX_SLIPPAGE_PERCENT}% (got {slippage_percent}%)",
                slippage_percent
            )

        return float(slippage_percent)

    @staticmethod
    def validate_gas_price(gas_price_wei: Union[int, str]) -> int:
        """
        Validate gas price in wei.

        Args:
            gas_price_wei: Gas price in wei

        Returns:
            Validated gas price as int

        Raises:
            ValidationError: If gas price is invalid
        """
        # Type coercion
        try:
            if isinstance(gas_price_wei, str):
                gas_price_wei = int(gas_price_wei)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                "gas_price_wei",
                f"Must be a valid integer: {e}",
                gas_price_wei
            )

        if gas_price_wei is None:
            raise ValidationError("gas_price_wei", "Cannot be None")

        if not isinstance(gas_price_wei, int):
            raise ValidationError(
                "gas_price_wei",
                f"Must be integer, got {type(gas_price_wei).__name__}",
                gas_price_wei
            )

        if gas_price_wei < MIN_GAS_PRICE_WEI:
            raise ValidationError(
                "gas_price_wei",
                f"Below minimum ({MIN_GAS_PRICE_WEI} wei = 1 gwei)",
                gas_price_wei
            )

        if gas_price_wei > MAX_GAS_PRICE_WEI:
            raise ValidationError(
                "gas_price_wei",
                f"Exceeds maximum ({MAX_GAS_PRICE_WEI} wei = 10,000 gwei)",
                gas_price_wei
            )

        return gas_price_wei

    @staticmethod
    def validate_eth_amount(amount_eth: Union[float, int, str, Decimal]) -> float:
        """
        Validate ETH amount (human-readable format).

        Args:
            amount_eth: Amount in ETH (e.g., 0.1, 1.5)

        Returns:
            Validated amount as float

        Raises:
            ValidationError: If amount is invalid
        """
        # Type coercion
        try:
            if isinstance(amount_eth, str):
                amount_eth = float(amount_eth)
            elif isinstance(amount_eth, (int, Decimal)):
                amount_eth = float(amount_eth)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                "amount_eth",
                f"Must be a valid number: {e}",
                amount_eth
            )

        if amount_eth is None:
            raise ValidationError("amount_eth", "Cannot be None")

        # Check for NaN/Inf
        if amount_eth != amount_eth:  # NaN check
            raise ValidationError("amount_eth", "Cannot be NaN")

        if abs(amount_eth) == float('inf'):
            raise ValidationError("amount_eth", "Cannot be infinite")

        # Range validation
        if amount_eth < MIN_ETH_AMOUNT:
            raise ValidationError(
                "amount_eth",
                f"Below dust threshold ({MIN_ETH_AMOUNT} ETH)",
                amount_eth
            )

        if amount_eth > MAX_ETH_AMOUNT:
            raise ValidationError(
                "amount_eth",
                f"Exceeds maximum ({MAX_ETH_AMOUNT} ETH)",
                amount_eth
            )

        return float(amount_eth)

    @staticmethod
    def validate_percentage(value: Union[float, int], field_name: str = "percentage") -> float:
        """
        Validate a percentage value (0-100).

        Args:
            value: Percentage value
            field_name: Name of the field for error messages

        Returns:
            Validated percentage as float

        Raises:
            ValidationError: If percentage is invalid
        """
        if value is None:
            raise ValidationError(field_name, "Cannot be None")

        try:
            value = float(value)
        except (ValueError, TypeError) as e:
            raise ValidationError(field_name, f"Must be numeric: {e}", value)

        if value < 0 or value > 100:
            raise ValidationError(
                field_name,
                f"Must be between 0 and 100 (got {value})",
                value
            )

        return value


# Convenience instance
validator = InputValidator()


def validate_amount(amount_wei: int) -> int:
    """Convenience function for amount validation."""
    return validator.validate_transaction_amount(amount_wei)


def validate_address(address: str) -> str:
    """Convenience function for address validation."""
    return validator.validate_token_address(address)


def validate_slippage(slippage: float) -> float:
    """Convenience function for slippage validation."""
    return validator.validate_slippage(slippage)


__all__ = [
    "InputValidator",
    "ValidationError",
    "validator",
    "validate_amount",
    "validate_address",
    "validate_slippage",
    "MAX_UINT256",
    "MAX_SAFE_AMOUNT_WEI",
    "MIN_SLIPPAGE_PERCENT",
    "MAX_SLIPPAGE_PERCENT",
    "ZERO_ADDRESS",
]
