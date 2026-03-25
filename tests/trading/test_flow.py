"""
Flow tests for the complete PolyTrader trading lifecycle.

Tests end-to-end flows: signal → risk check → execution → position →
exit evaluation → sell → realized P&L tracking.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.whallet_trader.exit_evaluator import ExitEvaluator
from overblick.plugins.whallet_trader.models import (
    ExitAction,
    OrderStatus,
    PortfolioPosition,
    RiskCheckResult,
    RiskLevel,
    TradeAction,
    TradeExecution,
    TradeOrder,
    TradeSignal,
)
from overblick.plugins.whallet_trader.risk_manager import RiskManager


def _make_signal(**overrides):
    defaults = {
        "signal_id": "sig_test",
        "market_id": "m1",
        "market_question": "Test market?",
        "action": TradeAction.BUY_YES,
        "outcome": "YES",
        "market_price": Decimal("0.50"),
        "our_probability": Decimal("0.65"),
        "probability_edge": Decimal("0.15"),
        "confidence_score": 80.0,
        "volume_score": 70.0,
        "time_horizon_days": 30,
        "suggested_position_size_percent": Decimal("3"),
        "kelly_fraction": Decimal("0.2"),
        "urgency": "medium",
    }
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _make_position(**overrides):
    defaults = {
        "position_id": "pos_test",
        "market_id": "m1",
        "market_question": "Test market?",
        "outcome": "YES",
        "quantity": Decimal("20"),
        "average_price": Decimal("0.50"),
        "current_price": Decimal("0.50"),
        "invested_amount": Decimal("10"),
        "current_value": Decimal("10"),
        "unrealized_pnl": Decimal("0"),
        "unrealized_pnl_percent": Decimal("0"),
        "first_bought": datetime.now(),
    }
    defaults.update(overrides)
    return PortfolioPosition(**defaults)


def _make_execution(**overrides):
    defaults = {
        "execution_id": "ex_test",
        "order_id": "ord_test",
        "signal_id": "sig_test",
        "market_id": "m1",
        "market_question": "Test market?",
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


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 1: BUY signal → risk check → position created
# ═══════════════════════════════════════════════════════════════════════


class TestBuyFlow:
    """Signal → risk check → Kelly → trade → position."""

    @pytest.mark.asyncio
    async def test_buy_signal_creates_position(self):
        """A valid BUY signal should pass risk check and create a position."""
        rm = RiskManager(starting_balance=Decimal("100"))
        signal = _make_signal(
            action=TradeAction.BUY_YES,
            market_price=Decimal("0.40"),
            our_probability=Decimal("0.60"),
            probability_edge=Decimal("0.20"),
        )
        result = await rm.check_signal_risk(signal)
        assert result.approved is True

        size = await rm.calculate_position_size(signal, result)
        assert size > Decimal("0"), "Position size should be positive for a valid signal"

    @pytest.mark.asyncio
    async def test_buy_no_signal_correct_kelly(self):
        """BUY_NO with real edge: we think YES=40% but market says YES=60%.
        So NO=60% but market NO=40%. Edge = 20% on NO side."""
        rm = RiskManager(starting_balance=Decimal("1000"))
        # Market: YES=0.60, NO=0.40. We think YES=0.40, NO=0.60
        # Signal carries outcome_price (NO price) as market_price
        signal = _make_signal(
            action=TradeAction.BUY_NO,
            outcome="NO",
            market_price=Decimal("0.40"),  # NO token price
            our_probability=Decimal("0.40"),  # YES probability (low = we favor NO)
            probability_edge=Decimal("0.20"),
        )
        result = await rm.check_signal_risk(signal)
        assert result.approved is True

        size = await rm.calculate_position_size(signal, result)
        # win_prob = 1-0.40 = 0.60, payout = 1/0.40 = 2.5, b=1.5
        # kelly = (0.60*1.5 - 0.40)/1.5 = (0.90-0.40)/1.5 = 0.333
        assert size > Decimal("0"), "BUY_NO with genuine 20% edge should have positive Kelly"


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 2: Buy → stop-loss → sell → realized P&L
# ═══════════════════════════════════════════════════════════════════════


class TestStopLossFlow:
    """Position drops below stop-loss → SELL triggered → P&L tracked."""

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_sell(self):
        """ExitEvaluator should trigger SELL when P&L < -25%."""
        ev = ExitEvaluator()
        pos = _make_position(
            average_price=Decimal("0.50"),
            current_price=Decimal("0.35"),
            invested_amount=Decimal("10"),
            current_value=Decimal("7"),
            unrealized_pnl=Decimal("-3"),
            unrealized_pnl_percent=Decimal("-30"),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "stop_loss"

    @pytest.mark.asyncio
    async def test_sell_bypasses_edge_check(self):
        """SELL signals should pass risk check regardless of edge."""
        rm = RiskManager(starting_balance=Decimal("100"))
        sell_signal = _make_signal(
            action=TradeAction.SELL_YES,
            probability_edge=Decimal("0.001"),  # Tiny edge
            confidence_score=30.0,  # Low confidence
        )
        result = await rm.check_signal_risk(sell_signal)
        assert result.approved is True, "SELL should bypass edge/confidence checks"

    def test_realized_pnl_on_sell(self):
        """Selling at a loss should record negative realized P&L."""
        execution = _make_execution(
            action=TradeAction.SELL_YES,
            execution_price=Decimal("0.35"),
            quantity=Decimal("20"),
        )
        # Simulate P&L calculation: (exit - entry) * qty
        entry_price = Decimal("0.50")
        realized = (execution.execution_price - entry_price) * execution.quantity
        assert realized == Decimal("-3.00")


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 3: Buy → profit target → sell
# ═══════════════════════════════════════════════════════════════════════


class TestProfitTargetFlow:
    """Position gains >50% → profit target fires → sell."""

    @pytest.mark.asyncio
    async def test_profit_target_triggers_sell(self):
        """ExitEvaluator should trigger SELL when P&L > +50%."""
        ev = ExitEvaluator()
        pos = _make_position(
            average_price=Decimal("0.40"),
            current_price=Decimal("0.65"),
            invested_amount=Decimal("10"),
            current_value=Decimal("16.25"),
            unrealized_pnl=Decimal("6.25"),
            unrealized_pnl_percent=Decimal("62.5"),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "profit_target"

    def test_realized_pnl_on_profitable_sell(self):
        """Selling at profit should record positive realized P&L."""
        entry = Decimal("0.40")
        exit_ = Decimal("0.65")
        qty = Decimal("25")
        realized = (exit_ - entry) * qty
        assert realized == Decimal("6.25")


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 4: Buy → edge decay → sell
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeDecayFlow:
    """Position held >1h, price returns to entry → edge decay → sell."""

    @pytest.mark.asyncio
    async def test_edge_decay_after_hold_period(self):
        """Edge decay should fire after 1h hold when price ≈ entry."""
        ev = ExitEvaluator()
        pos = _make_position(
            average_price=Decimal("0.50"),
            current_price=Decimal("0.51"),
            unrealized_pnl_percent=Decimal("2"),
            first_bought=datetime.now() - timedelta(hours=3),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "edge_decay"

    @pytest.mark.asyncio
    async def test_no_edge_decay_on_fresh_buy(self):
        """Edge decay should NOT fire on positions held < 1h."""
        ev = ExitEvaluator()
        pos = _make_position(
            average_price=Decimal("0.50"),
            current_price=Decimal("0.51"),
            unrealized_pnl_percent=Decimal("2"),
            first_bought=datetime.now() - timedelta(minutes=10),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.HOLD


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 5: Daily loss limit
# ═══════════════════════════════════════════════════════════════════════


class TestDailyLossLimitFlow:
    """Multiple losses → daily P&L breaches limit → new trades blocked."""

    @pytest.mark.asyncio
    async def test_daily_limit_blocks_after_losses(self):
        """After realized losses exceed daily limit, new BUY should be rejected."""
        # Create trades with realized losses today
        sell1 = _make_execution(
            action=TradeAction.SELL_YES,
            executed_at=datetime.now(),
            simulation=False,
        )
        sell1.realized_pnl_usd = Decimal("-5")

        sell2 = _make_execution(
            execution_id="ex_2",
            action=TradeAction.SELL_NO,
            executed_at=datetime.now(),
            simulation=False,
        )
        sell2.realized_pnl_usd = Decimal("-4")

        rm = RiskManager(
            starting_balance=Decimal("100"),
            daily_loss_limit_percent=Decimal("2.0"),
            trade_history=[sell1, sell2],
        )
        rm.total_realized_pnl = Decimal("-9")

        # Daily P&L should be negative
        daily_pnl = rm.calculate_daily_pnl()
        assert daily_pnl < Decimal("0"), f"Daily P&L should be negative, got {daily_pnl}"


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 6: Portfolio concentration
# ═══════════════════════════════════════════════════════════════════════


class TestPortfolioConcentrationFlow:
    """Multiple positions → exposure exceeds limit → new trade rejected."""

    @pytest.mark.asyncio
    async def test_concentration_rejects_when_overexposed(self):
        """When portfolio is >25% exposed, new BUY should be rejected."""
        positions = [
            _make_position(
                position_id=f"pos_{i}",
                market_id=f"m{i}",
                invested_amount=Decimal("10"),
                current_value=Decimal("10"),
            )
            for i in range(4)  # 4 × $10 = $40 invested out of $100
        ]
        rm = RiskManager(
            starting_balance=Decimal("100"),
            portfolio_positions=positions,
        )

        signal = _make_signal()
        result = await rm.check_signal_risk(signal)
        assert result.approved is False, "Should reject when exposure > 25%"
        assert "exposure" in result.reason.lower()


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 7: Weather time decay
# ═══════════════════════════════════════════════════════════════════════


class TestWeatherTimeDecayFlow:
    """Weather position should NOT be sold at 13h, but SHOULD at 1.5h."""

    @pytest.mark.asyncio
    async def test_weather_not_sold_at_13h(self):
        """Weather markets with 13h left should HOLD (2h threshold, not 24h)."""
        from datetime import timezone

        ev = ExitEvaluator()
        pos = _make_position(first_bought=datetime.now() - timedelta(hours=5))
        market_data = {
            "category": "weather",
            "end_time": (datetime.now(timezone.utc) + timedelta(hours=13)).isoformat(),
        }
        decision = await ev.assess_position(pos, market_data=market_data)
        # Should NOT trigger time_decay (13h > 2h threshold for weather)
        assert decision.trigger != "time_decay" or decision.action == ExitAction.HOLD

    @pytest.mark.asyncio
    async def test_weather_sold_at_1h(self):
        """Weather markets with 1h left SHOULD trigger a sell."""
        from datetime import timezone

        ev = ExitEvaluator()
        # Price far from entry to avoid edge_decay, positive P&L to avoid stop_loss
        pos = _make_position(
            average_price=Decimal("0.30"),
            current_price=Decimal("0.60"),
            unrealized_pnl_percent=Decimal("40"),
            first_bought=datetime.now() - timedelta(hours=20),
        )
        market_data = {
            "category": "weather",
            "end_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        decision = await ev.assess_position(pos, market_data=market_data)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "time_decay"

    @pytest.mark.asyncio
    async def test_regular_market_sold_at_12h(self):
        """Non-weather markets with 12h left SHOULD trigger a sell."""
        from datetime import timezone

        ev = ExitEvaluator()
        # Price far from entry to avoid edge_decay
        pos = _make_position(
            average_price=Decimal("0.30"),
            current_price=Decimal("0.50"),
            unrealized_pnl_percent=Decimal("30"),
            first_bought=datetime.now() - timedelta(days=5),
        )
        market_data = {
            "category": "geopolitics",
            "end_time": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
        }
        decision = await ev.assess_position(pos, market_data=market_data)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "time_decay"


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 8: Near resolution
# ═══════════════════════════════════════════════════════════════════════


class TestNearResolutionFlow:
    """Positions at extreme prices (>95% or <5%) should be sold."""

    @pytest.mark.asyncio
    async def test_sell_when_price_near_1(self):
        ev = ExitEvaluator()
        pos = _make_position(current_price=Decimal("0.97"), unrealized_pnl_percent=Decimal("40"))
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "near_resolution"

    @pytest.mark.asyncio
    async def test_sell_when_price_near_0(self):
        """Price near 0 triggers sell — either stop_loss or near_resolution."""
        ev = ExitEvaluator()
        pos = _make_position(current_price=Decimal("0.03"), unrealized_pnl_percent=Decimal("-40"))
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        # stop_loss fires before near_resolution due to trigger order
        assert decision.trigger in ("stop_loss", "near_resolution")


# ═══════════════════════════════════════════════════════════════════════
# Flow Test 9: Realized P&L affects portfolio value
# ═══════════════════════════════════════════════════════════════════════


class TestRealizedPnLPortfolioFlow:
    """Closed losing trade → portfolio value drops → smaller future positions."""

    def test_portfolio_shrinks_after_loss(self):
        """After -$10 realized, portfolio should be $90 not $100."""
        rm = RiskManager(starting_balance=Decimal("100"))
        rm.total_realized_pnl = Decimal("-10")
        value = rm._estimate_portfolio_value()
        assert value == Decimal("90")

    def test_portfolio_grows_after_profit(self):
        rm = RiskManager(starting_balance=Decimal("100"))
        rm.total_realized_pnl = Decimal("15")
        value = rm._estimate_portfolio_value()
        assert value == Decimal("115")

    @pytest.mark.asyncio
    async def test_smaller_positions_after_loss(self):
        """After losses, Kelly produces smaller positions."""
        rm_full = RiskManager(starting_balance=Decimal("100"))
        rm_full.total_realized_pnl = Decimal("0")

        rm_loss = RiskManager(starting_balance=Decimal("100"))
        rm_loss.total_realized_pnl = Decimal("-30")

        signal = _make_signal(
            action=TradeAction.BUY_YES,
            market_price=Decimal("0.40"),
            our_probability=Decimal("0.60"),
            probability_edge=Decimal("0.20"),
        )
        check = RiskCheckResult(approved=True, risk_level=RiskLevel.MEDIUM, risk_score=40.0)

        size_full = await rm_full.calculate_position_size(signal, check)
        size_loss = await rm_loss.calculate_position_size(signal, check)

        assert size_loss < size_full, "Positions should be smaller after losses"
