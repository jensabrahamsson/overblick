# Whallet - Simple EVM Wallet

Copyright © Jens Abrahamsson 2025-2026

A minimalist EVM wallet library for sending ETH and ERC20 tokens.

## Features

- Send ETH to any address
- Send ERC20 tokens (auto-detects decimals)
- Check ETH balance
- Wait for transaction confirmation
- Simple configuration via environment variables

## Installation

```bash
cd whallet
pip install -e .
```

## Quick Example

```python
from whallet import SimpleWallet
from decimal import Decimal

wallet = SimpleWallet(
    rpc_url="https://mainnet.infura.io/v3/YOUR_KEY",
    private_key="0xYOUR_PRIVATE_KEY"
)

# Send ETH
tx_hash = wallet.send_eth(
    to_address="0xRecipientAddress",
    amount_eth=Decimal("0.01")
)

# Send ERC20 token
tx_hash = wallet.send_erc20(
    token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    to_address="0xRecipientAddress",
    amount=Decimal("10.0")
)
```

## License

Whallet is released under the **GNU General Public License v3.0 (GPL v3)**.

This is free software: you are free to change and redistribute it under the terms of the GPL v3 license. See the LICENSE file in the project root for the complete license text.