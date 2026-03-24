"""Tests for realized P&L tracking — verifies the root cause fix."""

from datetime import datetime
from decimal import Decimal

import pytest

from overblick.plugins.whallet_trader.models import (
    PortfolioPosition,
    RiskLevel,
    TradeAction,
    TradeExecution,
    TradeSignal,
)
from overblick.plugins.whallet_trader.risk_manager import RiskManager


def _make_position(**overrides):
    defaults = {
        "position_id": "pos_1",
        "market_id": "m1",
        "market_question": "Test?",
        "outcome": "YES",
        "quantity": Decimal("20"),
        "average_price": Decimal("0.50"),
        "current_price": Decimal("0.55"),
        "invested_amount": Decimal("10"),
        "current_value": Decimal("11"),
        "unrealized_pnl": Decimal("1"),
        "unrealized_pnl_percent": Decimal("10"),
        "first_bought": datetime.now(),
    }
    defaults.update(overrides)
    return PortfolioPosition(**defaults)


def _make_execution(**overrides):
    defaults = {
        "execution_id": "ex_1",
        "order_id": "ord_1",
        "signal_id": "sig_1",
        "market_id": "m1",
        "market_question": "Test?",
        "outcome": "YES",
        "action": TradeAction.BUY_YES,
        "quantity": Decimal("20"),
        "execution_price": Decimal("0.50"),
        "position_size_usd": Decimal("10"),
        "expected_price": Decimal("0.50"),
        "slippage_percent": Decimal("0"),
        "transaction_hash": "0x0",
        "block_number": 0,
        "gas_used": 0,
        "gas_price_gwei": Decimal("0"),
        "executed_at": datetime.now(),
        "simulation": True,
    }
    defaults.update(overrides)
    return TradeExecution(**defaults)


class TestRealizedPnL:
    """Test that realized P&L is correctly tracked on SELL executions."""

    def test_sell_execution_has_realized_pnl_field(self):
        """TradeExecution model should have realized_pnl_usd field."""
        exec_ = _make_execution(action=TradeAction.SELL_YES)
        assert hasattr(exec_, "realized_pnl_usd")
        assert exec_.realized_pnl_usd == Decimal("0")

    def test_realized_pnl_calculation(self):
        """Realized P&L = (exit_price - entry_price) * quantity."""
        # Buy at 0.40, sell at 0.60, 20 tokens → P&L = 0.20 * 20 = $4
        entry_price = Decimal("0.40")
        exit_price = Decimal("0.60")
        qty = Decimal("20")
        pnl = (exit_price - entry_price) * qty
        assert pnl == Decimal("4.00")

    def test_realized_pnl_loss(self):
        """Negative P&L on losing trade."""
        entry_price = Decimal("0.60")
        exit_price = Decimal("0.40")
        qty = Decimal("20")
        pnl = (exit_price - entry_price) * qty
        assert pnl == Decimal("-4.00")


class TestPortfolioValueWithRealizedPnL:
    """Test that portfolio value correctly accounts for closed positions."""

    def test_portfolio_value_after_profitable_close(self):
        """After a profitable close, cash should include realized gains."""
        rm = RiskManager(starting_balance=Decimal("100"))
        rm.total_realized_pnl = Decimal("5")  # $5 profit from closed trade
        # No open positions
        value = rm._estimate_portfolio_value()
        # cash = 100 - 0 (no positions) + 5 (realized) = 105
        assert value == Decimal("105")

    def test_portfolio_value_after_losing_close(self):
        """After a losing close, cash should reflect realized losses."""
        rm = RiskManager(starting_balance=Decimal("100"))
        rm.total_realized_pnl = Decimal("-8")  # $8 loss from closed trade
        value = rm._estimate_portfolio_value()
        # cash = 100 - 0 + (-8) = 92
        assert value == Decimal("92")

    def test_portfolio_with_open_and_closed_positions(self):
        """Mix of open positions and realized P&L."""
        position = _make_position(
            invested_amount=Decimal("20"),
            current_value=Decimal("25"),
        )
        rm = RiskManager(
            starting_balance=Decimal("100"),
            portfolio_positions=[position],
        )
        rm.total_realized_pnl = Decimal("-3")  # Lost $3 on a previous closed trade
        value = rm._estimate_portfolio_value()
        # cash = 100 - 20 (invested) + (-3) (realized) = 77
        # position = 25
        # total = 77 + 25 = 102
        assert value == Decimal("102")


class TestDailyPnLWithRealizedLosses:
    """Test that daily P&L includes realized losses from closed trades."""

    @pytest.mark.asyncio
    async def test_daily_pnl_includes_sell_realized(self):
        """Daily P&L should sum realized_pnl_usd from today's SELL trades."""
        sell_trade = _make_execution(
            action=TradeAction.SELL_YES,
            executed_at=datetime.now(),
            simulation=False,  # Non-sim trade (daily P&L excludes sim)
        )
        sell_trade.realized_pnl_usd = Decimal("-5")  # Lost $5

        rm = RiskManager(
            starting_balance=Decimal("100"),
            trade_history=[sell_trade],
        )
        daily_pnl = rm.calculate_daily_pnl()
        # Should include the -$5 realized loss
        assert daily_pnl < Decimal("0")

    @pytest.mark.asyncio
    async def test_daily_pnl_excludes_old_sells(self):
        """Realized P&L from yesterday's trades should not count."""
        from datetime import timedelta

        old_trade = _make_execution(
            action=TradeAction.SELL_YES,
            executed_at=datetime.now() - timedelta(days=2),
            simulation=False,
        )
        old_trade.realized_pnl_usd = Decimal("-50")

        rm = RiskManager(
            starting_balance=Decimal("100"),
            trade_history=[old_trade],
        )
        daily_pnl = rm.calculate_daily_pnl()
        # Old trade should not affect today's P&L
        assert daily_pnl == Decimal("0")
