"""
Simulation test for Polymarket trading agent.

Tests that the PolyTrader identity can be loaded and plugins initialized
in simulation mode without external dependencies.
"""

import asyncio
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from overblick.core.plugin_base import PluginContext
from overblick.core.plugin_registry import PluginRegistry
from overblick.identities import Identity


async def test_polytrader_identity_load():
    """Test that PolyTrader identity can be loaded."""
    from overblick.identities import load_identity

    identity = load_identity("polytrader")
    assert identity is not None
    assert identity.name == "polytrader"
    assert identity.display_name == "PolyTrader"

    # Check that plugins are configured
    assert "polymarket_monitor" in identity.plugins
    assert "whallet_trader" in identity.plugins

    print("✓ PolyTrader identity loaded successfully")


async def test_plugins_registration():
    """Test that Polymarket plugins are registered in the plugin registry."""
    registry = PluginRegistry()

    # Check that plugins are registered
    available = registry.available_plugins()
    assert "polymarket_monitor" in available
    assert "whallet_trader" in available

    print("✓ Plugins registered in plugin registry")


async def test_plugin_initialization_simulation():
    """Test that plugins can be initialized in simulation mode."""
    # Create temporary data directory
    temp_dir = Path(tempfile.mkdtemp())
    data_dir = temp_dir / "data"
    log_dir = temp_dir / "logs"

    try:
        # Create mock context
        ctx = Mock(spec=PluginContext)
        ctx.identity_name = "polytrader"
        ctx.data_dir = data_dir
        ctx.log_dir = log_dir
        ctx.identity = Mock()

        # Load PolyTrader identity config
        from overblick.identities import load_identity

        identity = load_identity("polytrader")
        ctx.identity.raw_config = identity.raw_config

        # Mock required context methods and attributes
        ctx.audit_log = None
        ctx.llm_pipeline = None
        ctx.get_secret = Mock(return_value=None)
        # data_dir is a Path object - don't mock mkdir, let it create directory

        # Initialize polymarket_monitor plugin
        from overblick.plugins.polymarket_monitor.plugin import PolymarketMonitorPlugin

        monitor_plugin = PolymarketMonitorPlugin(ctx)

        # Should start in simulation mode by default
        assert monitor_plugin._config["simulation_mode"] == True

        # Call setup (async)
        await monitor_plugin.setup()

        # Check that setup completed
        assert monitor_plugin._state_file is not None

        # Initialize whallet_trader plugin
        from overblick.plugins.whallet_trader.plugin import WhalletTraderPlugin

        trader_plugin = WhalletTraderPlugin(ctx)

        # Should start in simulation mode by default
        assert trader_plugin._config["simulation_mode"] == True

        # Call setup (async)
        await trader_plugin.setup()

        # Check that setup completed
        assert trader_plugin._state_file is not None
        assert trader_plugin._trading_executor is not None
        assert trader_plugin._risk_manager is not None

        print("✓ Plugins initialized in simulation mode")

    finally:
        # Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


async def test_trading_signal_flow():
    """Test the trading signal flow from monitor to trader."""
    # Create temporary data directory
    temp_dir = Path(tempfile.mkdtemp())
    data_dir = temp_dir / "data"

    try:
        # Create mock context
        ctx = Mock(spec=PluginContext)
        ctx.identity_name = "polytrader"
        ctx.data_dir = data_dir
        ctx.log_dir = temp_dir / "logs"
        ctx.identity = Mock()

        # Load identity config
        from overblick.identities import load_identity

        identity = load_identity("polytrader")
        ctx.identity.raw_config = identity.raw_config

        # Mock context
        ctx.audit_log = None
        ctx.llm_pipeline = None
        ctx.get_secret = Mock(return_value=None)
        # data_dir is a Path object - don't mock mkdir, let it create directory

        # Initialize plugins
        from overblick.plugins.polymarket_monitor.plugin import PolymarketMonitorPlugin
        from overblick.plugins.whallet_trader.models import TradeAction, TradeSignal
        from overblick.plugins.whallet_trader.plugin import WhalletTraderPlugin

        monitor = PolymarketMonitorPlugin(ctx)
        trader = WhalletTraderPlugin(ctx)

        await monitor.setup()
        await trader.setup()

        # Create a mock trading signal
        from decimal import Decimal

        signal = TradeSignal(
            market_id="test_market_123",
            market_question="Will test pass?",
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

        # Test signal submission
        success = await trader.submit_trading_signal(signal)
        assert success == True

        # Signal should be in pending list
        assert len(trader._pending_signals) == 1
        assert trader._pending_signals[0].signal_id == signal.signal_id

        print("✓ Trading signal flow test passed")

    finally:
        # Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


async def test_risk_management():
    """Test basic risk management functionality."""
    from decimal import Decimal

    from overblick.plugins.whallet_trader.models import RiskLevel, TradeAction, TradeSignal
    from overblick.plugins.whallet_trader.risk_manager import RiskManager

    # Create risk manager with empty portfolio
    risk_manager = RiskManager(
        max_position_size_percent=Decimal("5.0"),
        daily_loss_limit_percent=Decimal("2.0"),
    )

    # Create a test signal
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
        risk_level=RiskLevel.MEDIUM,
    )

    # Check signal risk
    risk_check = await risk_manager.check_signal_risk(signal)

    # Signal should be approved (meets minimum criteria)
    assert risk_check.approved == True

    # Calculate position size
    position_size = await risk_manager.calculate_position_size(signal, risk_check)
    assert position_size > Decimal("0")

    print("✓ Risk management test passed")


async def main():
    """Run all simulation tests."""
    print("Running Polymarket trading agent simulation tests...")
    print("=" * 60)

    try:
        await test_polytrader_identity_load()
        await test_plugins_registration()
        await test_plugin_initialization_simulation()
        await test_trading_signal_flow()
        await test_risk_management()

        print("=" * 60)
        print("✅ All simulation tests passed!")

    except Exception as e:
        print("=" * 60)
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    # Run async tests
    import asyncio

    exit_code = asyncio.run(main())
    exit(exit_code)
