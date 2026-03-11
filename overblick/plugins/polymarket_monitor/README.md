# Polymarket Monitor Plugin

Real-time monitoring and analysis of Polymarket prediction markets for the PolyTrader identity. This plugin scans markets, detects mispricings, and identifies trading opportunities using LLM-enhanced probability estimation.

## Features

- **Market Monitoring**: Fetches active markets from Polymarket API every 15 minutes (configurable)
- **Probability Estimation**: Combines LLM analysis with statistical models to estimate true probabilities
- **Opportunity Detection**: Identifies markets where market price diverges significantly from estimated probability
- **Alert System**: Triggers alerts for high-confidence opportunities and threshold breaches
- **Risk Management**: Calculates optimal position sizes using Kelly criterion (with half-Kelly safety factor)
- **Simulation Mode**: Test strategies without real money (enabled by default)

## Installation

1. Ensure dependencies are installed:
   ```bash
   pip install aiohttp pydantic
   ```

2. The plugin is automatically discovered by Överblick's plugin registry.

## Configuration

Add to your identity's `personality.yaml`:

```yaml
polymarket_monitor:
  # Scan interval in minutes (default: 15)
  check_interval_minutes: 15
  
  # Maximum number of markets to monitor (default: 50)
  max_markets: 50
  
  # Minimum probability edge to consider (default: 0.03 = 3%)
  min_probability_edge: 0.03
  
  # Minimum 24h volume in USD (default: 1000)
  min_volume_usd: 1000
  
  # Maximum position size as % of portfolio (default: 5)
  max_position_size_percent: 5
  
  # Start in simulation mode (default: true)
  simulation_mode: true
```

## Data Models

### `PolymarketMarket`
Represents a prediction market with:
- Basic info (ID, slug, question, category)
- Status (open, closed, resolved)
- Outcomes with current prices and volumes
- Trading metrics (volume, liquidity, open interest)
- Calculated fields (implied probability, probability edge)

### `TradingOpportunity`
A detected trading opportunity with:
- Market details and recommended outcome
- Market price vs our probability estimate
- Probability edge and expected value
- Kelly fraction and recommended position size
- Confidence and volume scores
- Urgency level (low, medium, high, critical)

### `AlertCondition` & `Alert`
Configurable alert conditions and triggered alerts for:
- Price threshold breaches
- Volume spikes
- Probability edge thresholds
- Time-based conditions (e.g., market closing soon)

## API Integration

Uses Polymarket's public Gamma API:
- Base URL: `https://gamma-api.polymarket.com/`
- Rate limiting: 5 concurrent requests max
- Caching: 5-minute TTL for market data
- Error handling: Automatic retries with exponential backoff

## Probability Estimation Pipeline

1. **Market Context Building**: Gathers question, description, category, timing, volumes
2. **LLM Analysis**: PolyTrader personality estimates probability (0-100%)
3. **Statistical Adjustment**: Adjusts based on liquidity, time to resolution, price consistency
4. **Confidence Scoring**: Calculates 0-100 confidence score
5. **Edge Calculation**: Compares estimated probability to market price

## Risk Management

- **Half-Kelly**: Uses 50% of Kelly criterion suggested position size for safety
- **Position Limits**: Configurable max position size (default 5% of portfolio)
- **Liquidity Filters**: Minimum volume requirements (default $1,000 24h volume)
- **Simulation Mode**: All trades are virtual until explicitly enabled

## Integration with Whallet Trader

Opportunities detected by this plugin can be forwarded to the `whallet_trader` plugin for execution. The handoff includes:
- Market ID and recommended action
- Position size and limit price
- Risk parameters and confidence score

## State Persistence

Plugin state is saved to `data/<identity>/polymarket_monitor/polymarket_state.json`:
- Monitored markets list
- Recent opportunities (last 50)
- Last check timestamp

## Security Considerations

- **API Keys**: None required for public Polymarket API
- **Rate Limiting**: Respects API constraints to avoid bans
- **Content Safety**: All external content wrapped in boundary markers
- **Simulation First**: Defaults to simulation mode for safety

## Usage Example

```python
# Plugin will automatically:
# 1. Fetch markets every 15 minutes
# 2. Analyze each market for opportunities
# 3. Trigger alerts for high-confidence edges
# 4. Persist state between runs
```

## Testing

Run with simulation mode enabled first:
```bash
python -m overblick run polytrader
```

Check logs for detected opportunities:
```
PolymarketMonitor: scan complete — 85 markets, 3 opportunities
PolymarketMonitor: triggered opportunity alert — Will BTC reach $100k by EOY? (edge: 7.2%)
```

## Troubleshooting

**No markets fetched**: Check network connectivity and Polymarket API status
**LLM failures**: Ensure LLM pipeline is available (Gateway or Ollama)
**High API errors**: Reduce `max_markets` or increase `check_interval_minutes`

## Related Plugins

- `whallet_trader`: Executes trades based on opportunities from this plugin
- `ai_digest`: Provides news context for probability estimation
- `compass`: Monitors PolyTrader's trading performance and psychology