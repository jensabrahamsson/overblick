"""
Basic import tests for Polymarket plugins.

Tests that the plugins can be imported and instantiated without
requiring external dependencies (APIs, blockchain, etc.).
"""

import sys
from unittest.mock import Mock


def test_polymarket_monitor_import():
    """Test that polymarket_monitor plugin can be imported."""
    from overblick.core.plugin_base import PluginContext
    from overblick.plugins.polymarket_monitor.plugin import PolymarketMonitorPlugin

    # Create a mock context
    ctx = Mock(spec=PluginContext)
    ctx.identity_name = "polytrader"
    ctx.data_dir = Mock()
    ctx.data_dir.mkdir = Mock()
    ctx.identity = Mock()
    ctx.identity.raw_config = {}
    ctx.audit_log = None
    ctx.llm_pipeline = None

    # Instantiate plugin
    plugin = PolymarketMonitorPlugin(ctx)
    assert plugin is not None
    assert plugin.ctx == ctx


def test_polymarket_monitor_models_import():
    """Test that polymarket_monitor models can be imported."""
    import datetime

    from overblick.plugins.polymarket_monitor.models import (
        Alert,
        AlertCondition,
        MarketCategory,
        MarketStatus,
        PolymarketMarket,
        TradingOpportunity,
    )

    # Test that models can be instantiated
    market = PolymarketMarket(
        id="test_market",
        slug="test-market",
        question="Test market?",
        category=MarketCategory.POLITICS,
        status=MarketStatus.OPEN,
        created_time=datetime.datetime.now(),
    )
    assert market.id == "test_market"
    assert market.category == MarketCategory.POLITICS


def test_polymarket_client_import():
    """Test that polymarket_client can be imported."""
    from overblick.plugins.polymarket_monitor.polymarket_client import (
        PolymarketAPIError,
        PolymarketClient,
        RateLimitExceeded,
    )

    # Test that classes exist
    assert PolymarketClient is not None
    assert PolymarketAPIError is not None
    assert RateLimitExceeded is not None


def test_whallet_trader_import():
    """Test that whallet_trader plugin can be imported."""
    from overblick.core.plugin_base import PluginContext
    from overblick.plugins.whallet_trader.plugin import WhalletTraderPlugin

    # Create a mock context
    ctx = Mock(spec=PluginContext)
    ctx.identity_name = "polytrader"
    ctx.data_dir = Mock()
    ctx.data_dir.mkdir = Mock()
    ctx.identity = Mock()
    ctx.identity.raw_config = {}
    ctx.audit_log = None
    ctx.get_secret = Mock(return_value=None)

    # Instantiate plugin
    plugin = WhalletTraderPlugin(ctx)
    assert plugin is not None
    assert plugin.ctx == ctx


def test_whallet_trader_models_import():
    """Test that whallet_trader models can be imported."""
    from decimal import Decimal

    from overblick.plugins.whallet_trader.models import (
        OrderStatus,
        OrderType,
        PortfolioPosition,
        RiskLevel,
        RiskParameters,
        TradeAction,
        TradeExecution,
        TradeOrder,
        TradeSignal,
    )

    # Test that models can be instantiated
    signal = TradeSignal(
        market_id="test_market",
        market_question="Test market?",
        action=TradeAction.BUY_YES,
        outcome="YES",
        market_price=Decimal("0.65"),
        our_probability=Decimal("0.72"),
        probability_edge=Decimal("0.07"),
        confidence_score=85.0,
        volume_score=75.0,
        time_horizon_days=30.0,
        suggested_position_size_percent=Decimal("2.5"),
        kelly_fraction=Decimal("0.1"),
        urgency="medium",
    )
    assert signal.market_id == "test_market"
    assert signal.action == TradeAction.BUY_YES


def test_whallet_trader_executor_import():
    """Test that trading_executor can be imported."""
    from overblick.plugins.whallet_trader.trading_executor import (
        InsufficientBalance,
        TradingError,
        TradingExecutor,
        TransactionFailed,
    )

    # Test that classes exist
    assert TradingExecutor is not None
    assert TradingError is not None
    assert InsufficientBalance is not None
    assert TransactionFailed is not None


def test_whallet_trader_risk_manager_import():
    """Test that risk_manager can be imported."""
    from overblick.plugins.whallet_trader.risk_manager import RiskManager

    # Test that class exists
    assert RiskManager is not None


if __name__ == "__main__":
    # Run tests manually if needed
    test_polymarket_monitor_import()
    test_polymarket_monitor_models_import()
    test_polymarket_client_import()
    test_whallet_trader_import()
    test_whallet_trader_models_import()
    test_whallet_trader_executor_import()
    test_whallet_trader_risk_manager_import()
    print("All import tests passed!")
