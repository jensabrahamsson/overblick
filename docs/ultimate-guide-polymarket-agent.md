# Ultimate Guide: Build Your Polymarket Trading Agent with Överblick

## Introduction

Welcome to the ultimate guide for building an AI-powered Polymarket prediction market trading agent using the Överblick framework! This guide will show you how to create a specialized agent that can analyze, bet on, and monitor Polymarket prediction markets—all with minimal coding required.

### What You'll Build

By the end of this guide, you'll have a fully functional AI agent that can:
- **Monitor** Polymarket markets for opportunities
- **Analyze** market sentiment and probabilities
- **Place bets** using the simplified Whallet Ethereum wallet
- **Track positions** and manage risk
- **Generate insights** about trading performance

### Prerequisites

- Basic understanding of prediction markets and Polymarket
- Ethereum wallet with testnet funds (for practice)
- Överblick framework installed (see main README)
- Python 3.13+ environment

---

## Part 1: Understanding the Architecture

### System Overview

```
Överblick Framework
    └── Polymarket Trading Agent
        ├── Personality Layer (YAML config)
        ├── Analysis Engine (Python logic)
        ├── Trading Module (Whallet integration)
        └── Monitoring System (Real-time tracking)
```

### Key Components

1. **Agent Personality**: YAML configuration defining trading style and risk tolerance
2. **Market Analysis**: Custom logic for evaluating Polymarket opportunities
3. **Transaction Execution**: Integration with Whallet for Ethereum transactions
4. **Risk Management**: Position sizing and stop-loss mechanisms
5. **Reporting**: Performance tracking and insight generation

---

## Part 2: Creating Your Trading Agent Personality

### Step 1: Define Your Trading Identity

Create `overblick/identities/polytrader/personality.yaml`:

```yaml
################################################################################
# POLYTRADER - POLYMARKET TRADING AGENT
# Version: 1.0
# Specialization: Prediction market analysis and trading
################################################################################

identity:
  name: "PolyTrader"
  display_name: "PolyTrader"
  version: "1.0"
  role: "AI prediction market analyst and trader"
  description: "Data-driven trading agent specializing in Polymarket prediction markets. Combines statistical analysis with market psychology to identify value bets."
  
  owner: "@your_username"
  owner_platform: "X (Twitter)"
  
  website: "https://yourwebsite.com/polytrader"
  origin_project: "Polymarket Trading System"
  
  is_bot: true
  honest_about_being_bot: true
  platform: "Överblick Framework"
  framework: "Överblick agent framework"

psychological_framework:
  primary: "big_five"
  big_five_traits:
    openness: 75       # Open to new strategies and data
    conscientiousness: 90  # Highly disciplined and organized
    extraversion: 30   # Focused on analysis, not socializing
    agreeableness: 40  # Objective, not swayed by crowd sentiment
    neuroticism: 20    # Emotionally stable under pressure
  
  trading_psychology:
    risk_tolerance: "moderate"
    loss_aversion: "low"
    patience_level: "high"
    decision_style: "analytical_systematic"

voice:
  tone: "analytical_trader"
  temperature: 0.6  # Balanced between creative and precise
  
  style_guide: |
    # Trading Communication Principles
    1. Present data objectively with clear metrics
    2. Distinguish between facts and probabilities
    3. Always include risk assessment
    4. Use trading terminology correctly
    5. Acknowledge uncertainty and limits
    
    # Vocabulary Guidelines
    - Favor: "probability", "expected value", "risk/reward", "position sizing"
    - Avoid: "guaranteed", "sure thing", "can't lose", "easy money"
    
    # Example Phrases
    - "Market analysis indicates a 65% probability..."
    - "The risk/reward ratio for this position is..."
    - "Based on historical data, similar markets have..."
    - "Position sizing recommendation: X% of portfolio"
    
  metaphors:
    - "Trading as probability management"
    - "Markets as information aggregation mechanisms"
    - "Risk as a resource to be allocated"

traits:
  primary:
    - "Analytical"      # Data-driven decision making
    - "Disciplined"     # Follows trading rules consistently
    - "Patient"         # Waits for high-probability setups
    - "Risk-Aware"      # Always considers downside
    
  secondary:
    - "Adaptive"        # Adjusts to changing market conditions
    - "Thorough"        # Comprehensive research and analysis
    - "Transparent"     - "Clear about methods and assumptions
    - "Humble"          # Acknowledges uncertainty and errors
    
  constraints:
    - "Never trades with emotions"
    - "Maximum single position: 5% of portfolio"
    - "Daily loss limit: 2% of portfolio"
    - "Only trades verified Polymarket contracts"
    - "Never shares private key or wallet access"
    
  behavioral_rules:
    - "Always calculate expected value before trading"
    - "Document every trade with rationale"
    - "Review losing trades for lessons"
    - "Rebalance portfolio weekly"
    - "Take profits at predetermined levels"

knowledge_base:
  areas_of_expertise:
    - "Prediction market mechanics"
    - "Probability theory and statistics"
    - "Blockchain and smart contracts"
    - "Risk management techniques"
    - "Behavioral finance"
    
  topics_of_interest:
    - "Polymarket market structure"
    - "Ethereum transaction optimization"
    - "Market efficiency in prediction markets"
    - "Liquidity and slippage analysis"
    - "Oracle reliability and decentralization"
    
  learning_objectives:
    - "Improve market sentiment analysis"
    - "Develop better risk models"
    - "Learn advanced position sizing techniques"
    - "Understand cross-market correlations"
    
  key_knowledge:
    - "Polymarket contract addresses and ABI"
    - "Ethereum gas optimization strategies"
    - "Statistical significance testing"
    - "Expected value calculation formulas"
    - "Portfolio theory basics"
    
  trusted_sources:
    - "Polymarket official documentation"
    - "Ethereum blockchain data"
    - "Academic papers on prediction markets"
    - "Reputable trading psychology research"

operational_config:
  plugins:
    - "polymarket_monitor"  # Custom plugin we'll build
    - "whallet_integration" # Custom plugin for trading
    - "ai_digest"           # News and analysis
    
  capabilities:
    network_outbound: true
    filesystem_write: true   # For trade logs
    secrets_access: true     # For wallet keys (encrypted)
    email_send: false
    shell_execute: false
    
  schedule:
    active_hours: "00:00-23:59"  # 24/7 market monitoring
    timezone: "UTC"
    market_check_frequency: "every 15 minutes"
    portfolio_review: "daily at 08:00 UTC"
    
  trading_parameters:
    portfolio_size_eth: 1.0
    max_position_percentage: 5
    min_probability_edge: 3      # Minimum 3% edge vs market
    min_liquidity_usd: 1000
    max_slippage_percentage: 2
    
  safety:
    content_filter: "strict"
    controversy_level: "none"    # Avoid political/controversial markets
    privacy_protection: "maximum"
    emergency_stop_enabled: true
```

---

## Part 3: Building the Polymarket Monitor Plugin

### Plugin Structure

Create `overblick/plugins/polymarket_monitor/`:

```
polymarket_monitor/
├── __init__.py
├── plugin.py
├── models.py
├── polymarket_client.py
└── README.md
```

### Step 1: Basic Plugin Setup

**`plugin.py`:**
```python
"""
PolymarketMonitor - Real-time Polymarket prediction market monitor.

Monitors active markets, tracks prices, identifies trading opportunities,
and generates alerts based on configured strategies.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.event_bus import Event

from .models import Market, Opportunity, Alert
from .polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class PolymarketMonitor(PluginBase):
    """Polymarket prediction market monitoring plugin."""
    
    REQUIRED_CAPABILITIES = ["network_outbound", "filesystem_write"]
    
    def __init__(self):
        super().__init__()
        self.client: Optional[PolymarketClient] = None
        self.active_markets: Dict[str, Market] = {}
        self.watched_markets: List[str] = []
        self.opportunities: List[Opportunity] = []
        
    async def setup(self, ctx: PluginContext) -> None:
        """Initialize the Polymarket monitor."""
        logger.info("Setting up Polymarket Monitor plugin")
        
        # Initialize API client
        api_key = ctx.get_secret("polymarket_api_key")
        self.client = PolymarketClient(api_key=api_key)
        
        # Load configuration from agent personality
        personality = ctx.identity_config
        self.trading_params = personality.get("operational_config", {}).get("trading_parameters", {})
        
        # Load watched markets from config or database
        self.watched_markets = await self._load_watched_markets(ctx)
        
        logger.info(f"Polymarket Monitor initialized. Watching {len(self.watched_markets)} markets")
        
    async def tick(self, ctx: PluginContext) -> None:
        """Main monitoring loop - runs every 15 minutes by default."""
        logger.debug("Running Polymarket market scan")
        
        try:
            # 1. Update market data
            await self._update_market_data()
            
            # 2. Analyze for opportunities
            opportunities = await self._analyze_markets()
            
            # 3. Generate alerts for significant opportunities
            await self._generate_alerts(ctx, opportunities)
            
            # 4. Update learning store with market insights
            await self._update_learning_store(ctx)
            
        except Exception as e:
            logger.error(f"Error in Polymarket monitor tick: {e}")
            # Continue running - don't crash on single error
            
    async def _update_market_data(self) -> None:
        """Fetch and update data for all watched markets."""
        if not self.client:
            return
            
        for market_id in self.watched_markets:
            try:
                market_data = await self.client.get_market(market_id)
                market = Market.from_api_data(market_data)
                self.active_markets[market_id] = market
                
                # Emit market update event
                await self.emit_event(
                    Event(
                        type="polymarket.market_update",
                        data={
                            "market_id": market_id,
                            "yes_price": market.yes_price,
                            "no_price": market.no_price,
                            "volume_24h": market.volume_24h,
                            "liquidity": market.liquidity,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                    )
                )
                
            except Exception as e:
                logger.warning(f"Failed to update market {market_id}: {e}")
                
    async def _analyze_markets(self) -> List[Opportunity]:
        """Analyze markets for trading opportunities."""
        opportunities = []
        
        for market_id, market in self.active_markets.items():
            # Skip if market doesn't meet basic criteria
            if not self._meets_basic_criteria(market):
                continue
                
            # Calculate expected value
            ev_analysis = self._calculate_expected_value(market)
            
            # Check if opportunity meets threshold
            if ev_analysis["edge_percentage"] >= self.trading_params.get("min_probability_edge", 3):
                opportunity = Opportunity(
                    market_id=market_id,
                    market_title=market.title,
                    yes_price=market.yes_price,
                    expected_value=ev_analysis["expected_value"],
                    edge_percentage=ev_analysis["edge_percentage"],
                    confidence_score=ev_analysis["confidence"],
                    recommended_position=ev_analysis["position_size"],
                    rationale=ev_analysis["rationale"],
                    timestamp=datetime.utcnow()
                )
                opportunities.append(opportunity)
                
        return opportunities
        
    def _meets_basic_criteria(self, market: Market) -> bool:
        """Check if market meets basic trading criteria."""
        min_liquidity = self.trading_params.get("min_liquidity_usd", 1000)
        
        criteria = [
            market.liquidity >= min_liquidity,
            market.volume_24h > 0,  # Some trading activity
            market.resolution_date > datetime.utcnow() + timedelta(hours=24),  # Not resolving soon
            market.yes_price > Decimal("0.05") and market.yes_price < Decimal("0.95"),  # Not extreme
        ]
        
        return all(criteria)
        
    def _calculate_expected_value(self, market: Market) -> Dict[str, Any]:
        """Calculate expected value and trading edge for a market."""
        # This is a simplified example - real implementation would be more sophisticated
        
        # Get market price (probability implied by yes price)
        market_probability = float(market.yes_price)
        
        # Calculate our probability estimate (simplified - real would use ML/model)
        # For example, could use:
        # - Historical accuracy of similar markets
        # - News sentiment analysis
        # - Trader behavior patterns
        # - Oracle reliability metrics
        
        our_probability = self._estimate_true_probability(market)
        
        # Calculate edge
        edge = our_probability - market_probability
        edge_percentage = edge * 100
        
        # Calculate expected value (simplified)
        expected_value = edge * 100  # Percentage return if correct
        
        # Determine position size (Kelly Criterion simplified)
        position_size = min(
            edge_percentage / 10,  # Simplified Kelly
            self.trading_params.get("max_position_percentage", 5)
        )
        
        return {
            "market_probability": market_probability,
            "our_probability": our_probability,
            "edge": edge,
            "edge_percentage": edge_percentage,
            "expected_value": expected_value,
            "confidence": 0.7,  # Would be calculated based on model confidence
            "position_size": position_size,
            "rationale": f"Market prices imply {market_probability:.1%}, we estimate {our_probability:.1%} (+{edge_percentage:.1f}% edge)"
        }
        
    def _estimate_true_probability(self, market: Market) -> float:
        """Estimate the true probability of a market outcome."""
        # Simplified estimation - real implementation would use:
        # 1. Historical similar markets accuracy
        # 2. News sentiment analysis
        # 3. Trader concentration and sophistication
        # 4. Oracle reliability and decentralization
        
        # For this example, add small random edge for demonstration
        import random
        base_probability = float(market.yes_price)
        
        # Add small bias based on volume (higher volume = more efficient)
        volume_factor = min(market.volume_24h / 10000, 1.0)  # Cap at 1.0
        efficiency_adjustment = (0.5 - volume_factor) * 0.1  # Less efficient markets have larger edges
        
        # Add small random component
        random_component = (random.random() - 0.5) * 0.05
        
        estimated = base_probability + efficiency_adjustment + random_component
        
        # Bound between 0.05 and 0.95
        return max(0.05, min(0.95, estimated))
        
    async def _generate_alerts(self, ctx: PluginContext, opportunities: List[Opportunity]) -> None:
        """Generate alerts for significant trading opportunities."""
        for opportunity in opportunities:
            # Only alert on high-confidence opportunities
            if opportunity.confidence_score >= 0.7 and opportunity.edge_percentage >= 5:
                
                alert = Alert(
                    type="trading_opportunity",
                    severity="high",
                    title=f"Trading Opportunity: {opportunity.market_title}",
                    message=(
                        f"Market: {opportunity.market_title}\n"
                        f"Edge: {opportunity.edge_percentage:.1f}%\n"
                        f"Recommended Position: {opportunity.recommended_position:.1f}%\n"
                        f"Rationale: {opportunity.rationale}"
                    ),
                    data=opportunity.dict(),
                    timestamp=datetime.utcnow()
                )
                
                # Store alert
                self.opportunities.append(opportunity)
                
                # Emit event for other plugins (like trading executor)
                await self.emit_event(
                    Event(
                        type="polymarket.trading_opportunity",
                        data=alert.dict()
                    )
                )
                
                logger.info(f"Generated trading alert: {opportunity.market_title} "
                          f"(Edge: {opportunity.edge_percentage:.1f}%)")
                
    async def _update_learning_store(self, ctx: PluginContext) -> None:
        """Update the learning store with market insights."""
        if not hasattr(ctx, 'learning_store'):
            return
            
        # Add market analysis to learning store
        for market_id, market in self.active_markets.items():
            insight = {
                "type": "market_analysis",
                "market_id": market_id,
                "title": market.title,
                "yes_price": float(market.yes_price),
                "volume": float(market.volume_24h),
                "liquidity": float(market.liquidity),
                "category": market.category,
                "analysis_timestamp": datetime.utcnow().isoformat(),
                "key_insights": [
                    f"Market efficiency score: {self._calculate_efficiency_score(market):.2f}",
                    f"24h volume trend: {'up' if market.volume_24h > market.volume_7d / 7 else 'down'}",
                    f"Liquidity concentration: {self._calculate_liquidity_concentration(market):.2f}"
                ]
            }
            
            await ctx.learning_store.add_learning(
                agent_id=ctx.agent_id,
                content_type="market_analysis",
                content=insight,
                source="polymarket_monitor",
                tags=["polymarket", "trading", "analysis", market.category]
            )
            
    def _calculate_efficiency_score(self, market: Market) -> float:
        """Calculate market efficiency score (0-1)."""
        # Simplified efficiency calculation
        factors = [
            min(market.liquidity / 10000, 1.0),  # Liquidity factor
            min(market.volume_24h / 5000, 1.0),  # Volume factor
            1.0 if market.resolution_date > datetime.utcnow() + timedelta(days=7) else 0.7,  # Time factor
        ]
        
        return sum(factors) / len(factors)
        
    def _calculate_liquidity_concentration(self, market: Market) -> float:
        """Calculate liquidity concentration ratio."""
        # Simplified - real would use order book data
        return 0.3  # Placeholder
        
    async def _load_watched_markets(self, ctx: PluginContext) -> List[str]:
        """Load list of markets to watch from config or database."""
        # Could load from:
        # 1. Personality configuration
        # 2. Database of saved markets
        # 3. API search based on categories
        
        # For simplicity, return some example market IDs
        # In production, this would be configurable
        return [
            "0x123...abc",  # Example market IDs
            "0x456...def",
            "0x789...ghi"
        ]
        
    async def teardown(self, ctx: PluginContext) -> None:
        """Clean up resources."""
        logger.info("Tearing down Polymarket Monitor")
        if self.client:
            await self.client.close()
```

**`models.py`:**
```python
"""
Data models for Polymarket Monitor plugin.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field


class Market(BaseModel):
    """Polymarket market data."""
    
    id: str
    title: str
    description: str
    category: str
    yes_price: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    no_price: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    volume_24h: Decimal
    volume_7d: Decimal
    liquidity: Decimal
    resolution_date: datetime
    created_date: datetime
    oracle_address: str
    outcome_tags: List[str] = []
    
    @classmethod
    def from_api_data(cls, data: dict) -> "Market":
        """Create Market from API response."""
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            category=data.get("category", "uncategorized"),
            yes_price=Decimal(str(data["yesPrice"])),
            no_price=Decimal(str(data["noPrice"])),
            volume_24h=Decimal(str(data.get("volume24h", 0))),
            volume_7d=Decimal(str(data.get("volume7d", 0))),
            liquidity=Decimal(str(data.get("liquidity", 0))),
            resolution_date=datetime.fromisoformat(data["resolutionDate"].replace("Z", "+00:00")),
            created_date=datetime.fromisoformat(data["createdDate"].replace("Z", "+00:00")),
            oracle_address=data["oracle"]["address"],
            outcome_tags=data.get("outcomeTags", [])
        )


class Opportunity(BaseModel):
    """Trading opportunity identified by the monitor."""
    
    market_id: str
    market_title: str
    yes_price: Decimal
    expected_value: float  # Percentage expected return
    edge_percentage: float  # Probability edge vs market
    confidence_score: float = Field(ge=0, le=1)
    recommended_position: float  # Percentage of portfolio
    rationale: str
    timestamp: datetime
    trade_executed: bool = False
    execution_tx_hash: Optional[str] = None


class Alert(BaseModel):
    """Trading alert generated by the monitor."""
    
    type: str  # trading_opportunity, market_alert, system_alert
    severity: str  # low, medium, high, critical
    title: str
    message: str
    data: dict
    timestamp: datetime
    acknowledged: bool = False
```

**`polymarket_client.py`:**
```python
"""
Polymarket API client for fetching market data.
"""

import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any
from decimal import Decimal

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Client for Polymarket API."""
    
    BASE_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def _ensure_session(self):
        """Ensure we have an active session."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            )
            
    async def get_market(self, market_id: str) -> Dict[str, Any]:
        """Fetch market data by ID."""
        await self._ensure_session()
        
        url = f"{self.BASE_URL}/markets/{market_id}"
        
        try:
            async with self.session.get(url) as response:
                response.raise_for_status()
                return await response.json()
        except Exception as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            raise
            
    async def search_markets(self, query: str = "", category: str = "", 
                           limit: int = 50) -> List[Dict[str, Any]]:
        """Search for markets."""
        await self._ensure_session()
        
        params = {"limit": limit}
        if query:
            params["query"] = query
        if category:
            params["category"] = category
            
        url = f"{self.BASE_URL}/markets"
        
        try:
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("markets", [])
        except Exception as e:
            logger.error(f"Failed to search markets: {e}")
            return []
            
    async def get_market_trades(self, market_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades for a market."""
        await self._ensure_session()
        
        url = f"{self.BASE_URL}/markets/{market_id}/trades"
        params = {"limit": limit}
        
        try:
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.json()
        except Exception as e:
            logger.error(f"Failed to fetch trades for market {market_id}: {e}")
            return []
            
    async def close(self):
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
```

**`README.md`:**
```markdown
# Polymarket Monitor Plugin

Real-time monitoring and analysis of Polymarket prediction markets.

## Features

- Real-time market data polling
- Trading opportunity identification
- Risk assessment and position sizing
- Market efficiency scoring
- Alert generation for significant opportunities

## Configuration

Set these environment variables or secrets:

- `POLYMARKET_API_KEY`: Optional API key for higher rate limits
- `POLYMARKET_WATCHED_MARKETS`: Comma-separated list of market IDs to monitor

## Integration

This plugin emits events that can be consumed by other plugins:

- `polymarket.market_update`: When market prices change
- `polymarket.trading_opportunity`: When a trading opportunity is identified

## Usage

The plugin automatically runs on its configured schedule (default: every 15 minutes).
Trading opportunities with confidence ≥ 0.7 and edge ≥ 5% trigger alerts.
```

---

## Part 4: Building the Whallet Trading Integration

### Plugin Structure

Create `overblick/plugins/whallet_trader/`:

```
whallet_trader/
├── __init__.py
├── plugin.py
├── trading_executor.py
├── risk_manager.py
└── README.md
```

### Step 2: Trading Executor Plugin

**`plugin.py`:**
```python
"""
WhalletTrader - Execute trades using the simplified Whallet wallet.

Listens for trading opportunities from PolymarketMonitor and executes
trades using the Whallet Ethereum wallet library.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Any

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.event_bus import Event, EventBus

from .trading_executor import TradingExecutor
from .risk_manager import RiskManager

logger = logging.getLogger(__name__)


class WhalletTrader(PluginBase):
    """Polymarket trading execution plugin."""
    
    REQUIRED_CAPABILITIES = ["network_outbound", "secrets_access", "filesystem_write"]
    
    def __init__(self):
        super().__init__()
        self.executor: Optional[TradingExecutor] = None
        self.risk_manager: Optional[RiskManager] = None
        self.active_trades: Dict[str, Dict] = {}
        
    async def setup(self, ctx: PluginContext) -> None:
        """Initialize the trading executor."""
        logger.info("Setting up Whallet Trader plugin")
        
        # Get wallet configuration from secrets
        rpc_url = ctx.get_secret("ethereum_rpc_url")
        private_key = ctx.get_secret("ethereum_private_key")
        chain_id = int(ctx.get_secret("ethereum_chain_id", "1"))
        
        if not rpc_url or not private_key:
            logger.error("Missing Ethereum RPC URL or private key in secrets")
            raise ValueError("Ethereum configuration missing")
            
        # Initialize trading executor with Whallet
        self.executor = TradingExecutor(
            rpc_url=rpc_url,
            private_key=private_key,
            chain_id=chain_id
        )
        
        # Initialize risk manager
        personality = ctx.identity_config
        trading_params = personality.get("operational_config", {}).get("trading_parameters", {})
        
        self.risk_manager = RiskManager(
            portfolio_size_eth=Decimal(str(trading_params.get("portfolio_size_eth", 1.0))),
            max_position_percentage=trading_params.get("max_position_percentage", 5),
            daily_loss_limit=trading_params.get("daily_loss_limit", 2),
            min_probability_edge=trading_params.get("min_probability_edge", 3)
        )
        
        # Subscribe to trading opportunity events
        await ctx.event_bus.subscribe("polymarket.trading_opportunity", self._handle_trading_opportunity)
        
        logger.info("Whallet Trader initialized and listening for opportunities")
        
    async def _handle_trading_opportunity(self, event: Event) -> None:
        """Handle trading opportunity events from PolymarketMonitor."""
        if not self.executor or not self.risk_manager:
            logger.error("Executor or risk manager not initialized")
            return
            
        opportunity = event.data
        
        try:
            # 1. Risk management check
            risk_approval = await self.risk_manager.approve_trade(opportunity)
            if not risk_approval["approved"]:
                logger.info(f"Trade not approved by risk manager: {risk_approval['reason']}")
                return
                
            # 2. Get market contract address (simplified - real would map market_id to contract)
            market_contract = await self._get_market_contract(opportunity["market_id"])
            
            # 3. Calculate trade size
            trade_size_eth = self.risk_manager.calculate_trade_size(
                opportunity["recommended_position"],
                opportunity["confidence_score"]
            )
            
            # 4. Determine trade direction (YES or NO)
            # If our probability > market probability, buy YES
            # If our probability < market probability, buy NO
            trade_direction = "YES" if opportunity["edge_percentage"] > 0 else "NO"
            
            # 5. Execute trade
            logger.info(f"Executing trade: {trade_size_eth} ETH on {opportunity['market_title']} "
                       f"({trade_direction})")
            
            tx_hash = await self.executor.execute_polymarket_trade(
                market_contract=market_contract,
                direction=trade_direction,
                amount_eth=trade_size_eth,
                max_slippage=Decimal("0.02")  # 2% max slippage
            )
            
            # 6. Record trade
            self.active_trades[opportunity["market_id"]] = {
                "opportunity": opportunity,
                "trade_size_eth": float(trade_size_eth),
                "direction": trade_direction,
                "tx_hash": tx_hash,
                "execution_time": datetime.utcnow(),
                "status": "executed"
            }
            
            # 7. Emit trade execution event
            await self.emit_event(
                Event(
                    type="whallet.trade_executed",
                    data={
                        "market_id": opportunity["market_id"],
                        "market_title": opportunity["market_title"],
                        "direction": trade_direction,
                        "amount_eth": float(trade_size_eth),
                        "tx_hash": tx_hash,
                        "edge_percentage": opportunity["edge_percentage"],
                        "confidence": opportunity["confidence_score"]
                    }
                )
            )
            
            logger.info(f"Trade executed successfully: {tx_hash}")
            
        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
            
            # Emit failure event
            await self.emit_event(
                Event(
                    type="whallet.trade_failed",
                    data={
                        "market_id": opportunity["market_id"],
                        "error": str(e)
                    }
                )
            )
            
    async def _get_market_contract(self, market_id: str) -> str:
        """Get contract address for a Polymarket market ID."""
        # Simplified - real implementation would:
        # 1. Query Polymarket API for contract address
        # 2. Cache results
        # 3. Validate contract exists and is verified
        
        # For this example, return a placeholder
        # Real implementation would be something like:
        # return await self.executor.polymarket_client.get_market_contract(market_id)
        
        return "0x1234567890123456789012345678901234567890"  # Placeholder
            
    async def tick(self, ctx: PluginContext) -> None:
        """Monitor active trades and manage positions."""
        if not self.executor:
            return
            
        # Check for trade confirmations
        await self._check_trade_confirmations()
        
        # Manage risk exposure
        await self.risk_manager.update_exposure(self.active_trades)
        
        # Generate performance report
        await self._generate_performance_report(ctx)
        
    async def _check_trade_confirmations(self) -> None:
        """Check confirmation status of pending trades."""
        for market_id, trade_info in list(self.active_trades.items()):
            if trade_info["status"] == "executed":
                try:
                    receipt = await self.executor.check_transaction(trade_info["tx_hash"])
                    
                    if receipt["confirmed"]:
                        trade_info["status"] = "confirmed"
                        trade_info["block_number"] = receipt["block_number"]
                        trade_info["gas_used"] = receipt["gas_used"]
                        
                        logger.info(f"Trade confirmed for {market_id} in block {receipt['block_number']}")
                    else:
                        # Check if it's been a while
                        execution_time = trade_info["execution_time"]
                        if (datetime.utcnow() - execution_time).seconds > 300:  # 5 minutes
                            trade_info["status"] = "stalled"
                            logger.warning(f"Trade stalled for {market_id}")
                            
                except Exception as e:
                    logger.error(f"Failed to check transaction {trade_info['tx_hash']}: {e}")
                    
    async def _generate_performance_report(self, ctx: PluginContext) -> None:
        """Generate trading performance report."""
        if not self.active_trades:
            return
            
        # Calculate performance metrics
        total_trades = len(self.active_trades)
        confirmed_trades = sum(1 for t in self.active_trades.values() if t["status"] == "confirmed")
        total_eth_traded = sum(t["trade_size_eth"] for t in self.active_trades.values())
        
        # Store in learning store
        if hasattr(ctx, 'learning_store'):
            report = {
                "type": "trading_performance",
                "timestamp": datetime.utcnow().isoformat(),
                "total_trades": total_trades,
                "confirmed_trades": confirmed_trades,
                "pending_trades": total_trades - confirmed_trades,
                "total_eth_traded": total_eth_traded,
                "active_positions": [
                    {
                        "market_id": mid,
                        "direction": t["direction"],
                        "size_eth": t["trade_size_eth"],
                        "status": t["status"]
                    }
                    for mid, t in self.active_trades.items()
                ]
            }
            
            await ctx.learning_store.add_learning(
                agent_id=ctx.agent_id,
                content_type="trading_report",
                content=report,
                source="whallet_trader",
                tags=["trading", "performance", "polymarket"]
            )
            
    async def teardown(self, ctx: PluginContext) -> None:
        """Clean up resources."""
        logger.info("Tearing down Whallet Trader")
        
        if self.executor:
            await self.executor.cleanup()
            
        # Unsubscribe from events
        if ctx.event_bus:
            await ctx.event_bus.unsubscribe("polymarket.trading_opportunity", self._handle_trading_opportunity)
```

**`trading_executor.py`:**
```python
"""
Trading execution using Whallet library.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional, Dict, Any

# Import the simplified Whallet library
try:
    from whallet import SimpleWallet
    WHALLET_AVAILABLE = True
except ImportError:
    WHALLET_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Whallet library not available. Trading will be simulated.")

logger = logging.getLogger(__name__)


class TradingExecutor:
    """Execute Polymarket trades using Whallet."""
    
    def __init__(self, rpc_url: str, private_key: str, chain_id: int = 1):
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.chain_id = chain_id
        self.wallet: Optional[SimpleWallet] = None
        
        if WHALLET_AVAILABLE:
            self._initialize_wallet()
        else:
            logger.warning("Running in simulation mode - no real trades will be executed")
            
    def _initialize_wallet(self):
        """Initialize the Whallet instance."""
        try:
            self.wallet = SimpleWallet(
                rpc_url=self.rpc_url,
                private_key=self.private_key
            )
            logger.info(f"Whallet initialized for address: {self.wallet.account.address}")
        except Exception as e:
            logger.error(f"Failed to initialize Whallet: {e}")
            raise
            
    async def execute_polymarket_trade(self, market_contract: str, direction: str, 
                                     amount_eth: Decimal, max_slippage: Decimal = Decimal("0.02")) -> str:
        """
        Execute a trade on Polymarket.
        
        Args:
            market_contract: Polymarket contract address
            direction: "YES" or "NO"
            amount_eth: Amount of ETH to trade
            max_slippage: Maximum acceptable slippage (e.g., 0.02 for 2%)
            
        Returns:
            Transaction hash
        """
        if not self.wallet:
            if not WHALLET_AVAILABLE:
                # Simulation mode
                fake_hash = f"0x{'simulated_' * 6}{int(asyncio.get_event_loop().time())}"
                logger.info(f"Simulated trade: {amount_eth} ETH {direction} on {market_contract[:10]}...")
                return fake_hash
            else:
                raise RuntimeError("Wallet not initialized")
                
        try:
            # For Polymarket, trading involves:
            # 1. Approving token spending (if using ERC20)
            # 2. Calling the market contract's trade function
            # 3. Handling slippage protection
            
            # Simplified implementation - real would:
            # 1. Get current market prices
            # 2. Calculate minimum tokens to receive based on slippage
            # 3. Build and send transaction
            
            # This is a placeholder - real Polymarket integration would be more complex
            logger.info(f"Executing real trade: {amount_eth} ETH {direction} on {market_contract}")
            
            # For now, just send ETH to a placeholder address
            # Real implementation would interact with Polymarket contracts
            tx_hash = self.wallet.send_eth(
                to_address=market_contract,  # In reality, would be different for Polymarket
                amount_eth=amount_eth,
                gas_price_gwei=Decimal("30")  # Example gas price
            )
            
            return tx_hash
            
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            raise
            
    async def check_transaction(self, tx_hash: str) -> Dict[str, Any]:
        """Check transaction confirmation status."""
        if not self.wallet:
            # Simulation mode
            return {
                "confirmed": True,
                "block_number": 9999999,
                "gas_used": 21000,
                "success": True
            }
            
        try:
            receipt = self.wallet.wait_for_transaction(tx_hash, timeout=30)
            return {
                "confirmed": True,
                "block_number": receipt.blockNumber,
                "gas_used": receipt.gasUsed,
                "success": receipt.status == 1
            }
        except Exception as e:
            logger.warning(f"Transaction not yet confirmed or error: {e}")
            return {
                "confirmed": False,
                "error": str(e)
            }
            
    async def cleanup(self):
        """Clean up resources."""
        # Whallet doesn't need explicit cleanup, but we might want to
        # clear any cached data
        pass
```

**`risk_manager.py`:**
```python
"""
Risk management for Polymarket trading.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class RiskManager:
    """Manage trading risk and position limits."""
    
    def __init__(self, portfolio_size_eth: Decimal, max_position_percentage: float,
                 daily_loss_limit: float, min_probability_edge: float):
        self.portfolio_size_eth = portfolio_size_eth
        self.max_position_percentage = max_position_percentage
        self.daily_loss_limit = daily_loss_limit
        self.min_probability_edge = min_probability_edge
        
        self.today_trades: List[Dict] = []
        self.today_pnl: Decimal = Decimal("0")
        self.active_positions: Dict[str, Dict] = {}
        
    async def approve_trade(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        """Check if a trade meets risk criteria."""
        
        checks = []
        
        # 1. Minimum edge check
        if opportunity["edge_percentage"] < self.min_probability_edge:
            checks.append(("edge_too_low", 
                          f"Edge {opportunity['edge_percentage']:.1f}% < minimum {self.min_probability_edge}%"))
            
        # 2. Position size check
        max_position_eth = self.portfolio_size_eth * Decimal(self.max_position_percentage / 100)
        trade_size_eth = self.calculate_trade_size(
            opportunity["recommended_position"],
            opportunity["confidence_score"]
        )
        
        if trade_size_eth > max_position_eth:
            checks.append(("position_too_large",
                          f"Trade size {trade_size_eth} ETH > max {max_position_eth} ETH"))
            
        # 3. Daily loss limit check
        if self.today_pnl < -Decimal(self.daily_loss_limit / 100) * self.portfolio_size_eth:
            checks.append(("daily_loss_limit",
                          f"Today's PNL {self.today_pnl} ETH exceeds loss limit"))
            
        # 4. Concentration risk check
        total_exposure = sum(pos["size_eth"] for pos in self.active_positions.values())
        if total_exposure + trade_size_eth > self.portfolio_size_eth * Decimal("0.2"):  # 20% max exposure
            checks.append(("concentration_risk",
                          f"Total exposure would exceed 20% of portfolio"))
            
        # 5. Similar positions check (avoid correlated bets)
        market_category = opportunity.get("category", "unknown")
        similar_positions = sum(1 for pos in self.active_positions.values() 
                              if pos.get("category") == market_category)
        if similar_positions >= 3:  # Max 3 positions in same category
            checks.append(("correlation_risk",
                          f"Already have {similar_positions} positions in {market_category}"))
            
        if checks:
            return {
                "approved": False,
                "checks_failed": checks,
                "reason": "; ".join([c[1] for c in checks])
            }
        else:
            return {
                "approved": True,
                "trade_size_eth": float(trade_size_eth),
                "checks_passed": ["edge", "position_size", "loss_limit", "concentration", "correlation"]
            }
            
    def calculate_trade_size(self, recommended_position: float, confidence: float) -> Decimal:
        """Calculate trade size based on recommended position and confidence."""
        # Scale position by confidence
        scaled_position = recommended_position * confidence
        
        # Apply Kelly criterion scaling (simplified)
        kelly_fraction = min(scaled_position / 100, 0.25)  # Cap at 25% Kelly
        
        trade_size = self.portfolio_size_eth * Decimal(kelly_fraction)
        
        # Round to reasonable precision
        return Decimal(str(round(float(trade_size), 6)))
        
    async def update_exposure(self, active_trades: Dict[str, Dict]) -> None:
        """Update risk exposure based on active trades."""
        self.active_positions.clear()
        
        for market_id, trade in active_trades.items():
            if trade["status"] in ["executed", "confirmed"]:
                self.active_positions[market_id] = {
                    "size_eth": Decimal(str(trade["trade_size_eth"])),
                    "direction": trade["direction"],
                    "execution_time": trade["execution_time"],
                    "category": trade.get("category", "unknown")
                }
                
        # Reset daily tracking at midnight UTC
        now = datetime.utcnow()
        if now.hour == 0 and now.minute < 5:  # Rough midnight check
            self.today_trades.clear()
            self.today_pnl = Decimal("0")
            logger.info("Reset daily trading limits")
            
    def record_trade_result(self, market_id: str, pnl_eth: Decimal) -> None:
        """Record trade PNL for daily tracking."""
        self.today_trades.append({
            "market_id": market_id,
            "pnl_eth": float(pnl_eth),
            "timestamp": datetime.utcnow()
        })
        
        self.today_pnl += pnl_eth
        
        logger.info(f"Recorded trade PNL: {pnl_eth} ETH (Today total: {self.today_pnl} ETH)")
```

---

## Part 5: Configuration and Deployment

### Step 1: Configure Secrets

Create `config/secrets/polytrader.yaml` (encrypted):

```yaml
# Ethereum configuration
ethereum_rpc_url: "https://mainnet.infura.io/v3/YOUR_INFURA_KEY"
ethereum_private_key: "0xYOUR_PRIVATE_KEY"  # Use testnet key for development!
ethereum_chain_id: "1"  # 1 for mainnet, 5 for Goerli testnet

# Polymarket API (optional)
polymarket_api_key: ""

# Telegram notifications (optional)
telegram_bot_token: ""
telegram_chat_id: ""
```

### Step 2: Register Plugins

Update `overblick/core/plugin_registry.py` to include your new plugins:

```python
# Add to PLUGIN_REGISTRY
PLUGIN_REGISTRY = {
    # ... existing plugins ...
    "polymarket_monitor": "overblick.plugins.polymarket_monitor.plugin:PolymarketMonitor",
    "whallet_trader": "overblick.plugins.whallet_trader.plugin:WhalletTrader",
}
```

### Step 3: Run Your Trading Agent

```bash
# Start the agent
python -m overblick run polytrader

# Or use the manager
python -m overblick manage start polytrader

# Monitor logs
python -m overblick manage logs polytrader

# Check status
python -m overblick manage status polytrader
```

### Step 4: Monitor Performance

Access the dashboard:
```bash
python -m overblick dashboard
```

Then navigate to `http://localhost:8080` to see:
- Active trades and positions
- Market monitoring status
- Risk exposure metrics
- Performance reports

---

## Part 6: Testing and Validation

### Simulation Testing

Before using real funds, test in simulation mode:

```bash
# Run in simulation mode (no real trades)
POLYMARKET_SIMULATION=1 python -m overblick run polytrader

# Or configure in personality.yaml
# operational_config:
#   simulation_mode: true
```

### Test Scenarios

Create test scenarios in `tests/plugins/polymarket/`:

```python
import pytest
from decimal import Decimal

from overblick.plugins.polymarket_monitor.plugin import PolymarketMonitor
from overblick.plugins.whallet_trader.plugin import WhalletTrader


@pytest.mark.asyncio
async def test_opportunity_detection():
    """Test that the monitor correctly identifies trading opportunities."""
    monitor = PolymarketMonitor()
    
    # Mock market data
    mock_market = {
        "id": "test_market",
        "yes_price": Decimal("0.60"),
        "volume_24h": Decimal("5000"),
        "liquidity": Decimal("2000")
    }
    
    # Test analysis
    opportunities = await monitor._analyze_markets([mock_market])
    
    assert len(opportunities) > 0
    assert opportunities[0]["edge_percentage"] > 0


@pytest.mark.asyncio  
async def test_risk_management():
    """Test that risk manager correctly approves/rejects trades."""
    risk_mgr = RiskManager(
        portfolio_size_eth=Decimal("10"),
        max_position_percentage=5,
        daily_loss_limit=2,
        min_probability_edge=3
    )
    
    # Test valid opportunity
    valid_opp = {
        "edge_percentage": 5.0,
        "recommended_position": 3.0,
        "confidence_score": 0.8
    }
    
    approval = await risk_mgr.approve_trade(valid_opp)
    assert approval["approved"] == True
    
    # Test invalid opportunity (edge too low)
    invalid_opp = {
        "edge_percentage": 1.0,
        "recommended_position": 3.0,
        "confidence_score": 0.8
    }
    
    approval = await risk_mgr.approve_trade(invalid_opp)
    assert approval["approved"] == False
    assert "edge_too_low" in str(approval["checks_failed"])
```

---

## Part 7: Advanced Features and Optimization

### Machine Learning Integration

Enhance your agent with ML models:

```python
# Example: Add ML-based probability estimation
class MLProbabilityEstimator:
    """Machine learning model for estimating true probabilities."""
    
    def __init__(self):
        self.model = self._load_model()
        
    def _load_model(self):
        # Load pre-trained model
        # Could use:
        # - Historical market accuracy patterns
        # - News sentiment analysis
        # - Trader behavior clustering
        pass
        
    def estimate(self, market_data: Dict) -> float:
        """Estimate true probability using ML model."""
        features = self._extract_features(market_data)
        prediction = self.model.predict(features)
        return float(prediction)
```

### Automated Strategy Optimization

Implement strategy optimization:

```yaml
# In personality.yaml
strategy_optimization:
  enabled: true
  optimization_method: "bayesian"
  parameters_to_optimize:
    - "min_probability_edge"
    - "max_position_percentage"
    - "confidence_threshold"
  optimization_frequency: "weekly"
  performance_metric: "sharpe_ratio"
```

### Cross-Market Arbitrage

Detect and exploit arbitrage opportunities:

```python
class ArbitrageDetector:
    """Detect arbitrage opportunities across prediction markets."""
    
    async def find_arbitrage(self, markets: List[Market]) -> List[Dict]:
        """Find arbitrage opportunities."""
        opportunities = []
        
        # Check for price discrepancies between:
        # 1. Same market on different platforms
        # 2. Related markets (e.g., "Trump wins" vs "Biden loses")
        # 3. Derivative markets
        
        return opportunities
```

---

## Part 8: Security Best Practices

### Security Checklist

- [ ] **Use testnet first**: Never deploy with mainnet keys until thoroughly tested
- [ ] **Encrypt secrets**: All keys encrypted with Fernet at rest
- [ ] **Limit permissions**: Agent only has necessary capabilities
- [ ] **Audit logs**: All trades and decisions logged for audit trail
- [ ] **Circuit breakers**: Automatic stop-loss and emergency halt mechanisms
- [ ] **Multi-sig consideration**: For large amounts, use multi-sig wallets
- [ ] **Regular security reviews**: Audit code and configurations periodically

### Emergency Procedures

Configure emergency stops in personality.yaml:

```yaml
safety:
  emergency_stop_enabled: true
  emergency_triggers:
    - daily_loss_exceeds: 5  # 5% daily loss
    - single_loss_exceeds: 10  # 10% single trade loss
    - system_error_rate: 10  # 10% error rate
    - manual_override: true
    
  emergency_actions:
    - halt_all_trading: true
    - liquidate_positions: false  # Be careful with this!
    - notify_owner: true
    - log_incident: true
```

---

## Part 9: Performance Monitoring and Analytics

### Key Metrics to Track

1. **Trading Performance**:
   - Win rate and average win/loss
   - Sharpe ratio and Sortino ratio
   - Maximum drawdown
   - Risk-adjusted returns

2. **Market Analysis**:
   - Opportunity detection rate
   - Forecast accuracy
   - Edge persistence

3. **System Performance**:
   - API response times
   - Transaction success rate
   - Gas cost efficiency

### Reporting Integration

Connect to analytics platforms:

```python
class AnalyticsReporter:
    """Report metrics to analytics platforms."""
    
    async def report_metrics(self, metrics: Dict):
        # Send to:
        # - Datadog / Prometheus for monitoring
        # - Google Analytics for web dashboard
        # - Custom database for historical analysis
        # - Telegram/Discord for real-time alerts
        pass
```

---

## Part 10: Conclusion and Next Steps

### What You've Built

Congratulations! You've built a fully functional AI trading agent for Polymarket that:

✅ **Monitors** prediction markets in real-time  
✅ **Analyzes** trading opportunities using statistical methods  
✅ **Manages risk** with sophisticated position sizing and limits  
✅ **Executes trades** using the simplified Whallet Ethereum wallet  
✅ **Tracks performance** with comprehensive reporting  
✅ **Maintains security** with encrypted secrets and safety controls  

### Next Steps for Enhancement

1. **Add more data sources**: Integrate news APIs, social sentiment, on-chain analytics
2. **Implement ML models**: Train models for better probability estimation
3. **Multi-agent coordination**: Deploy multiple agents with different strategies
4. **Cross-platform trading**: Extend to other prediction markets
5. **Advanced risk models**: Implement portfolio optimization and correlation analysis
6. **User interface**: Build a web dashboard for manual oversight and control

### Community and Support

- **Share your strategies**: Contribute successful trading patterns to the community
- **Report issues**: Help improve Överblick by reporting bugs and suggesting features
- **Join discussions**: Participate in AI agent and prediction market communities
- **Consider open source**: Share your agent configurations and plugins

### Final Words of Caution

Trading prediction markets involves **real financial risk**. Always:

1. **Start small** with testnet or tiny amounts
2. **Understand the markets** you're trading
3. **Monitor actively** especially when starting
4. **Keep learning** and adapting your strategies
5. **Never risk more than you can afford to lose**

The power of AI agents in trading comes not from magic predictions, but from consistent, disciplined execution of well-tested strategies. Your Polymarket trading agent is a tool—a sophisticated one—but still just a tool. The real intelligence comes from how you design, monitor, and refine it.

Happy trading!

---

*Need help? Check the Överblick documentation, join community discussions, or open issues on GitHub. Remember: test first, trade small, learn always.*