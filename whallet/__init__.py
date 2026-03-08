"""Simple Ethereum wallet for Polymarket betting."""

from __future__ import annotations

try:
    from .simple_wallet import SimpleWallet
except ImportError:
    # Direct import mode (when whallet/ is in sys.path)
    from simple_wallet import SimpleWallet

__all__ = ["SimpleWallet"]
