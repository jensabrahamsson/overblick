"""
Data models for the Polymarket monitor plugin.

Defines the structure for markets, positions, trading opportunities,
and alert conditions used throughout the monitoring system.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MarketCategory(str, Enum):
    """Categories of prediction markets."""

    POLITICS = "politics"
    FINANCE = "finance"
    TECHNOLOGY = "technology"
    CRYPTO = "crypto"
    SPORTS = "sports"
    ENTERTAINMENT = "entertainment"
    HEALTH = "health"
    SCIENCE = "science"
    OTHER = "other"


class MarketStatus(str, Enum):
    """Status of a prediction market."""

    OPEN = "open"
    CLOSED = "closed"
    RESOLVED = "resolved"
    INVALID = "invalid"
    PAUSED = "paused"


class MarketResolution(str, Enum):
    """How a market was resolved."""

    YES = "yes"
    NO = "no"
    CANCELED = "canceled"
    INVALID = "invalid"


class MarketOutcome(BaseModel):
    """A single outcome (option) in a prediction market."""

    name: str
    ticker: str  # e.g., "YES" or "NO" for binary markets
    price: float  # Current price in USD (0.00 - 1.00)
    volume_24h: float = 0.0  # Trading volume in USD
    last_updated: datetime


class PolymarketMarket(BaseModel):
    """Complete representation of a Polymarket prediction market."""

    id: str  # Polymarket market ID
    slug: str  # URL slug
    question: str  # Market question
    description: str | None = None
    category: MarketCategory
    status: MarketStatus
    resolution: MarketResolution | None = None
    created_time: datetime
    end_time: datetime | None = None  # Market closing time
    resolution_time: datetime | None = None  # When market was resolved

    outcomes: list[MarketOutcome] = Field(default_factory=list)

    # Trading metrics
    volume_24h: float = 0.0  # Total volume in USD
    liquidity: float = 0.0  # Total liquidity in USD
    open_interest: float = 0.0  # Total open interest in USD

    # Calculated fields
    implied_probability: float | None = None  # For binary markets
    probability_edge: float | None = None  # Our edge vs market price
    confidence_score: float | None = None  # 0-100 confidence in our probability

    model_config = ConfigDict(from_attributes=True)


class TradingOpportunity(BaseModel):
    """A detected trading opportunity."""

    market_id: str
    market_question: str
    recommended_outcome: str  # Which outcome to bet on
    market_price: float  # Current market price (0.00-1.00)
    our_probability: float  # Our estimated probability (0.00-1.00)
    probability_edge: float  # Absolute edge: |our_prob - market_price|
    expected_value: float  # Expected profit per dollar bet (considering fees)
    kelly_fraction: float  # Kelly criterion suggested position size (0-1)

    # Risk metrics
    confidence_score: float  # 0-100 confidence in our probability estimate
    volume_score: float  # 0-100 based on trading volume/liquidity
    time_horizon_days: float  # Expected days until resolution

    # Recommendation
    recommended_action: str  # "BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO", "HOLD"
    position_size_percent: float  # Recommended % of portfolio (0-5%)
    urgency: str  # "low", "medium", "high", "critical"

    # Metadata
    detected_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)


class AlertCondition(BaseModel):
    """Condition that triggers an alert."""

    name: str
    condition_type: str  # "price_threshold", "volume_spike", "edge_threshold", "time_based"
    market_id: str | None = None  # Specific market or None for all
    parameter: float  # Threshold value
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)


class Alert(BaseModel):
    """An alert triggered by a condition."""

    condition: AlertCondition
    market: PolymarketMarket | None = None
    current_value: float
    threshold_value: float
    message: str
    severity: str  # "info", "warning", "critical"
    triggered_at: datetime = Field(default_factory=datetime.now)
    acknowledged: bool = False
    acknowledged_at: datetime | None = None


class PortfolioPosition(BaseModel):
    """A position held in a market."""

    market_id: str
    outcome: str  # "YES" or "NO" token
    quantity: float  # Number of tokens
    average_price: float  # Average purchase price
    current_price: float  # Current market price
    unrealized_pnl: float  # Unrealized profit/loss in USD
    unrealized_pnl_percent: float  # Percentage gain/loss
    invested_amount: float  # Total USD invested
    current_value: float  # Current USD value

    # Risk metrics
    max_position_size: float  # Maximum allowed position size in USD
    position_size_percent: float  # % of portfolio
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

    # Metadata
    first_bought: datetime
    last_updated: datetime = Field(default_factory=datetime.now)
