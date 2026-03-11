# Whallet Trader Plugin

Trade execution plugin for Polymarket prediction markets. Integrates with the simplified Whallet library to execute trades based on signals from the `polymarket_monitor` plugin.

## Features

- **Trade Execution**: Executes BUY_YES, BUY_NO, SELL_YES, SELL_NO orders on Polymarket
- **Risk Management**: Position sizing using Kelly criterion with safety constraints
- **Portfolio Tracking**: Maintains portfolio positions with real-time P&L calculation
- **Stop-Loss/Take-Profit**: Automated risk management with configurable levels
- **Simulation Mode**: Test trading strategies without real money (enabled by default)
- **Gas Optimization**: Smart gas pricing and transaction batching
- **Audit Logging**: Comprehensive logging of all trade attempts and outcomes

## Installation

1. Ensure dependencies are installed:
   ```bash
   pip install web3 eth-account
   ```

2. The plugin is automatically discovered by Överblick's plugin registry.

## Configuration

Add to your identity's `personality.yaml`:

```yaml
whallet_trader:
  # Trading mode (default: true = simulation only)
  simulation_mode: true
  
  # Maximum position size as % of portfolio (default: 5)
  max_position_size_percent: 5
  
  # Maximum daily loss as % of portfolio (default: 2)
  daily_loss_limit_percent: 2
  
  # Gas price multiplier (default: 1.1 = 10% above market)
  gas_price_multiplier: 1.1
  
  # Check interval in seconds (default: 60)
  check_interval_seconds: 60
```

## Secrets Required

For real trading (when `simulation_mode: false`), add to `config/secrets/<identity>.yaml`:

```yaml
ethereum_rpc_url: "https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"
ethereum_private_key: "0xYOUR_PRIVATE_KEY"
```

**⚠️ SECURITY WARNING**: Never commit secrets to version control. The secrets file is Fernet-encrypted at rest.

## Data Models

### `TradeSignal`
Trading signal from `polymarket_monitor`:
- Market ID, question, and outcome (YES/NO)
- Action (BUY_YES, BUY_NO, SELL_YES, SELL_NO)
- Market price vs our probability estimate
- Probability edge, confidence score, urgency level
- Suggested position size and Kelly fraction

### `TradeOrder`
Executable trade order:
- Quantity, price limits, order type (market/limit)
- Risk parameters (stop-loss, take-profit)
- Status tracking (pending, submitted, completed, failed)
- Blockchain transaction details

### `TradeExecution`
Completed trade execution:
- Actual execution price and quantity
- Transaction hash, gas used, gas cost
- Slippage and fees
- Simulation flag

### `PortfolioPosition`
Current portfolio position:
- Token quantity and average purchase price
- Current market price and value
- Unrealized P&L calculations
- Risk management levels (stop-loss, take-profit)

### `RiskParameters`
Risk management configuration:
- Position size limits (max 5% per trade, 25% total exposure)
- Loss limits (2% daily, 5% weekly, 10% max drawdown)
- Trading constraints (min edge, min confidence, max slippage)
- Time-based limits (max trades per day, cooloff periods)

## Trading Pipeline

1. **Signal Reception**: Receives `TradeSignal` from `polymarket_monitor`
2. **Risk Validation**: Checks against risk parameters and portfolio limits
3. **Position Sizing**: Calculates optimal position size using half-Kelly criterion
4. **Order Creation**: Creates `TradeOrder` with calculated parameters
5. **Execution**: Submits order to blockchain (or simulates)
6. **Portfolio Update**: Updates portfolio positions with execution details
7. **Monitoring**: Trades are monitored for completion and risk triggers

## Risk Management

### Position Sizing
- **Kelly Criterion**: Mathematical optimal position sizing based on edge
- **Half-Kelly Safety**: Uses 50% of Kelly recommendation for robustness
- **Maximum Limits**: Configurable max position size (default 5% of portfolio)
- **Portfolio Concentration**: Maximum total exposure limit (default 25%)

### Loss Limits
- **Daily Loss Limit**: Stops trading after 2% daily loss (configurable)
- **Weekly Loss Limit**: 5% weekly loss limit
- **Maximum Drawdown**: 10% peak-to-trough limit

### Trade Validation
- **Minimum Edge**: 3% probability edge required
- **Minimum Confidence**: 60/100 confidence score required
- **Minimum Volume**: $1,000 24h trading volume required
- **Maximum Slippage**: 2% maximum allowed slippage

## Simulation Mode

When `simulation_mode: true` (default):
- All trades are simulated with realistic slippage and gas costs
- No real blockchain transactions are sent
- Portfolio tracking works identically to real trading
- Perfect for strategy testing and development

To enable real trading:
1. Set `simulation_mode: false` in configuration
2. Configure Ethereum RPC URL and private key in secrets
3. Start with small position sizes to test

## Integration with Polymarket Monitor

The plugin provides a public API for `polymarket_monitor`:

```python
# In polymarket_monitor plugin:
signal = TradeSignal(
    market_id="0x...",
    market_question="Will BTC reach $100k by EOY?",
    action=TradeAction.BUY_YES,
    outcome="YES",
    market_price=Decimal("0.65"),
    our_probability=Decimal("0.72"),
    probability_edge=Decimal("0.07"),
    confidence_score=85.0,
    # ... other fields
)

success = await whallet_trader.submit_trading_signal(signal)
```

## State Persistence

Plugin state is saved to `data/<identity>/whallet_trader/whallet_trader_state.json`:
- Portfolio positions
- Trade history (last 500 trades)
- Active orders
- Last check timestamp

## Gas Optimization

- **Gas Price Multiplier**: Configurable multiplier over market gas price
- **Transaction Batching**: Multiple orders in single transaction (future)
- **Gas Estimation**: Accurate gas estimation for Polymarket contracts
- **Gas Price Caching**: Caches gas prices to avoid frequent RPC calls

## Error Handling

- **Insufficient Balance**: Checks ETH balance for gas and token balance for trades
- **Transaction Failures**: Monitors transactions and retries if appropriate
- **Network Issues**: Handles RPC timeouts and connection problems
- **Contract Errors**: Validates contract calls and handles revert reasons

## Security Considerations

- **Private Keys**: Stored encrypted in secrets, never in code or config
- **Simulation Default**: Defaults to simulation mode for safety
- **Position Limits**: Hard limits prevent overexposure
- **Audit Logging**: All trade attempts logged for review
- **Input Validation**: All inputs validated before processing

## Testing

Start with simulation mode:
```bash
python -m overblick run polytrader
```

Check logs for trade execution:
```
WhalletTrader: executing order ord_abc123 — BUY_YES 153.85 YES tokens @ ~$0.65
WhalletTrader: executed trade ex_def456 — BUY_YES 153.85 @ $0.648 (size: $100.00)
```

## Performance Monitoring

The plugin tracks:
- Win rate and profit factor
- Average position size and holding period
- Sharpe ratio and max drawdown
- Daily, weekly, and total P&L
- Gas costs and trading fees

## Related Plugins

- `polymarket_monitor`: Generates trading signals for this plugin
- `compass`: Monitors trading performance and psychology
- `ai_digest`: Provides news context for market analysis
- `host_health`: Monitors system health for reliable trading

## Future Enhancements

1. **Advanced Order Types**: Stop-loss, take-profit, trailing stops
2. **Portfolio Rebalancing**: Automated position adjustment
3. **Multi-Market Arbitrage**: Cross-market opportunity detection
4. **LP Integration**: Provide liquidity to Polymarket pools
5. **Advanced Analytics**: Machine learning for probability estimation