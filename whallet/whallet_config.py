import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for Whallet - simple Ethereum wallet."""

    rpc_url: str
    etherscan_api_key: str
    infura_api_key: str  # Infura API key for token metadata
    infura_rpc_url: str  # Backup RPC URL
    default_public_address: str | None
    default_private_key: str | None
    simulation_enabled_by_default: bool = False
    api_key: str | None = None  # API key for authentication
    chain_id: int = 1  # Ethereum mainnet
    uniswap_router_address: str = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
    weth_address: str = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    default_slippage_bps: int = 500  # 5% default slippage
    max_slippage_bps: int = 2000  # 20% maximum slippage
    simulation_default_eth: float = 0.1
    min_eth_reserve: float = 0.002  # Minimum ETH to keep for gas fees

    @property
    def default_slippage(self) -> float:
        return self.default_slippage_bps / 10_000

    @property
    def max_slippage(self) -> float:
        return self.max_slippage_bps / 10_000


def _get_env(name: str, fallback: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    return fallback


def _load_default_config() -> dict:
    config_path = Path(__file__).with_name("config_defaults.json")
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {config_path}: {exc}") from exc


def _env_bool(name: str, fallback: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, fallback: float) -> float:
    value = os.getenv(name)
    if value is None:
        return fallback
    try:
        return float(value)
    except ValueError:
        return fallback


def _env_int(name: str, fallback: int) -> int:
    value = os.getenv(name)
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    file_defaults = _load_default_config()

    # Infura API key
    infura_api_key = str(
        _get_env(
            "WHALLET_INFURA_API_KEY",
            "",  # No default - must be provided via environment
        )
    )
    # Alchemy API key for backup RPC (fallback)
    alchemy_api_key = str(
        _get_env(
            "WHALLET_ALCHEMY_API_KEY",
            "",  # No default - must be provided via environment
        )
    )

    if not infura_api_key:
        raise RuntimeError("WHALLET_INFURA_API_KEY must be set in environment")

    infura_primary_url = f"https://mainnet.infura.io/v3/{infura_api_key}"

    # Use Infura RPC as primary (always), and allow legacy overrides to act as backup.
    # WHALLET_RPC_URL is treated as a legacy backup override if it's not Infura.
    legacy_rpc_url = _get_env("WHALLET_RPC_URL")
    backup_rpc_url = _get_env("WHALLET_BACKUP_RPC_URL")
    if not backup_rpc_url and legacy_rpc_url and "infura.io" not in legacy_rpc_url.lower():
        backup_rpc_url = legacy_rpc_url

    # Primary RPC is always Infura
    rpc_url = infura_primary_url

    # Backup RPC URL (Alchemy default when not overridden)
    # Note: Variable named infura_rpc_url for Settings compatibility (legacy name)
    infura_rpc_url = backup_rpc_url or f"https://eth-mainnet.g.alchemy.com/v2/{alchemy_api_key}"
    etherscan_api_key = str(
        _get_env(
            "WHALLET_ETHERSCAN_KEY",
            "",  # No default - must be provided via environment
        )
    )
    default_public = _get_env(
        "WHALLET_PUBLIC_ADDRESS",
        file_defaults.get("public_address"),
    )
    default_private = _get_env(
        "WHALLET_PRIVATE_KEY",
        file_defaults.get("private_key"),
    )
    simulation_default = _env_bool(
        "WHALLET_SIMULATION_ENABLED",
        bool(file_defaults.get("simulation_enabled", False)),
    )
    api_key_value = os.getenv("WHALLET_API_KEY")
    api_key = api_key_value if api_key_value else None
    min_eth_reserve = _env_float("WHALLET_MIN_ETH_RESERVE", 0.002)

    if not rpc_url:
        raise RuntimeError("WHALLET_RPC_URL must be configured.")
    if not etherscan_api_key:
        raise RuntimeError("WHALLET_ETHERSCAN_KEY must be configured.")
    if not infura_api_key:
        raise RuntimeError("WHALLET_INFURA_API_KEY must be configured.")
    if not infura_rpc_url:
        raise RuntimeError("WHALLET_BACKUP_RPC_URL (or legacy WHALLET_RPC_URL) must be configured.")
    return Settings(
        rpc_url=rpc_url,
        etherscan_api_key=etherscan_api_key,
        infura_api_key=infura_api_key,
        infura_rpc_url=infura_rpc_url,
        default_public_address=default_public,
        default_private_key=default_private,
        simulation_enabled_by_default=simulation_default,
        api_key=api_key,
        min_eth_reserve=min_eth_reserve,
    )
