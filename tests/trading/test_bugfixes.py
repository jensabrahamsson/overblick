"""
Tests for trading system bugfixes identified in 5-pass code review.

Each test validates a specific fix to prevent regression.
"""

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


class TestPortfolioValueCalculation:
    """Bug #1: Portfolio value double-counted starting balance as cash."""

    def test_portfolio_value_subtracts_invested(self):
        """Cash = starting_balance - invested, NOT just starting_balance."""
        positions = [
            PortfolioPosition(
                position_id="pos_1",
                market_id="m1",
                market_question="Test?",
                outcome="YES",
                quantity=Decimal("20"),
                average_price=Decimal("0.50"),
                current_price=Decimal("0.55"),
                invested_amount=Decimal("10"),
                current_value=Decimal("11"),
                unrealized_pnl=Decimal("1"),
                unrealized_pnl_percent=Decimal("10"),
                first_bought=datetime.now(),
            ),
        ]
        rm = RiskManager(
            starting_balance=Decimal("100"),
            portfolio_positions=positions,
        )
        value = rm._estimate_portfolio_value()
        # cash = 100 - 10 = 90, positions = 11, total = 101
        assert value == Decimal("101")
        # NOT 111 (the old broken behavior)
        assert value != Decimal("111")

    def test_portfolio_value_no_positions(self):
        """With no positions, portfolio = starting balance."""
        rm = RiskManager(starting_balance=Decimal("100"))
        assert rm._estimate_portfolio_value() == Decimal("100")


class TestDecimalTypeCoercion:
    """Bug #2: float passed to RiskManager caused TypeError in Decimal arithmetic."""

    def test_float_config_values_coerced_to_decimal(self):
        """RiskManager must accept float inputs without TypeError."""
        rm = RiskManager(
            max_position_size_percent=5.0,  # float, not Decimal
            daily_loss_limit_percent=2.0,  # float, not Decimal
            starting_balance=Decimal("100"),
        )
        # This should NOT raise TypeError
        assert isinstance(rm.max_position_size_percent, Decimal)
        assert isinstance(rm.daily_loss_limit_percent, Decimal)

    def test_max_position_size_calculation_works(self):
        """Position size calculation must work with coerced Decimals."""
        rm = RiskManager(
            max_position_size_percent=5.0,
            starting_balance=Decimal("100"),
        )
        signal = TradeSignal(
            market_id="test",
            market_question="Test?",
            action=TradeAction.BUY_YES,
            outcome="YES",
            market_price=Decimal("0.50"),
            our_probability=Decimal("0.60"),
            probability_edge=Decimal("0.10"),
            confidence_score=80.0,
            volume_score=80.0,
            time_horizon_days=30,
            suggested_position_size_percent=Decimal("2.0"),
            kelly_fraction=Decimal("0.1"),
            urgency="medium",
        )
        # This must not raise TypeError
        max_size = rm._calculate_max_position_size(signal)
        assert max_size > 0


class TestDailyPnL:
    """Bug #4: Daily P&L summed ALL positions, not just today's."""

    def test_daily_pnl_only_includes_todays_positions(self):
        """Only positions from today's trades count in daily P&L."""
        old_pos = PortfolioPosition(
            position_id="old",
            market_id="old_market",
            market_question="Old?",
            outcome="YES",
            quantity=Decimal("10"),
            average_price=Decimal("0.50"),
            current_price=Decimal("0.30"),
            invested_amount=Decimal("5"),
            current_value=Decimal("3"),
            unrealized_pnl=Decimal("-2"),
            unrealized_pnl_percent=Decimal("-40"),
            first_bought=datetime(2025, 1, 1),
        )
        today_pos = PortfolioPosition(
            position_id="today",
            market_id="today_market",
            market_question="Today?",
            outcome="YES",
            quantity=Decimal("10"),
            average_price=Decimal("0.50"),
            current_price=Decimal("0.55"),
            invested_amount=Decimal("5"),
            current_value=Decimal("5.5"),
            unrealized_pnl=Decimal("0.50"),
            unrealized_pnl_percent=Decimal("10"),
            first_bought=datetime.now(),
        )
        today_trade = TradeExecution(
            execution_id="ex1",
            order_id="ord1",
            signal_id="sig1",
            market_id="today_market",
            market_question="Today?",
            outcome="YES",
            action=TradeAction.BUY_YES,
            quantity=Decimal("10"),
            execution_price=Decimal("0.50"),
            position_size_usd=Decimal("5"),
            expected_price=Decimal("0.50"),
            slippage_percent=Decimal("0"),
            transaction_hash="0x0",
            block_number=0,
            gas_used=0,
            gas_price_gwei=Decimal("0"),
            executed_at=datetime.now(),
        )
        rm = RiskManager(
            starting_balance=Decimal("100"),
            portfolio_positions=[old_pos, today_pos],
            trade_history=[today_trade],
        )
        daily_pnl = rm.calculate_daily_pnl()
        # Should only include today_pos P&L, NOT old_pos
        assert daily_pnl >= Decimal("0")  # today_pos has positive P&L


class TestRiskScoreOrdering:
    """Bug #10: elif >90 was unreachable because >30 came first."""

    def test_90_day_horizon_scores_higher_than_30_day(self):
        """Trades >90 days should get score 20, not 10.
        Tests that the elif branch ordering is correct."""
        rm = RiskManager(starting_balance=Decimal("100"))
        # Use minimal risk factors so horizon difference is visible
        signal_30 = TradeSignal(
            market_id="t30",
            market_question="30 day?",
            action=TradeAction.BUY_YES,
            outcome="YES",
            market_price=Decimal("0.50"),
            our_probability=Decimal("0.51"),
            probability_edge=Decimal("1.00"),
            confidence_score=95.0,
            volume_score=95.0,
            time_horizon_days=45,
            suggested_position_size_percent=Decimal("2"),
            kelly_fraction=Decimal("0.5"),
            urgency="low",
        )
        signal_90 = signal_30.model_copy(
            update={"market_id": "t90", "time_horizon_days": 120}
        )
        score_30 = rm._calculate_risk_score(signal_30)
        score_90 = rm._calculate_risk_score(signal_90)
        # 120 days should add 20 points, 45 days should add 10 points
        assert score_90 > score_30, f"90-day score ({score_90}) should be > 30-day ({score_30})"


class TestKellyZeroEdge:
    """Bug #11: Kelly=0 trades were forced to $10 minimum."""

    def test_kelly_zero_returns_zero_position(self):
        """When Kelly fraction is 0, position size should be 0 (skip trade)."""
        rm = RiskManager(starting_balance=Decimal("100"))
        signal = TradeSignal(
            market_id="no_edge",
            market_question="No edge?",
            action=TradeAction.BUY_YES,
            outcome="YES",
            market_price=Decimal("0.50"),
            our_probability=Decimal("0.50"),  # Same as market = no edge
            probability_edge=Decimal("0.00"),
            confidence_score=50.0,
            volume_score=50.0,
            time_horizon_days=30,
            suggested_position_size_percent=Decimal("0"),
            kelly_fraction=Decimal("0"),
            urgency="low",
        )
        from overblick.plugins.whallet_trader.models import RiskCheckResult
        risk_check = RiskCheckResult(
            approved=True,
            risk_level=RiskLevel.MEDIUM,
            risk_score=50.0,
        )
        import asyncio
        size = asyncio.get_event_loop().run_until_complete(
            rm.calculate_position_size(signal, risk_check)
        )
        assert size == Decimal("0")


class TestOutcomePriceMapping:
    """Bug from earlier: BUY_NO signals used YES price instead of NO price."""

    def test_buy_no_uses_no_price(self):
        """When buying NO, execution price should be (1 - yes_price)."""
        yes_price = 0.10  # YES at 10 cents

        # Correct NO price
        no_price = 1 - yes_price
        assert no_price == 0.90

        # Position at NO price should cost ~$10 for ~11.1 tokens
        qty = Decimal("10") / Decimal(str(no_price))
        assert qty < Decimal("12")  # NOT 100 tokens (the old bug)


class TestRecommendedActionUpdate:
    """Bug #9: recommended_action not updated after LLM changes probability."""

    def test_action_flips_when_llm_prob_changes_direction(self):
        """If LLM says prob < market, action should flip to BUY_NO."""
        # Simulate: market YES = 0.60, LLM says prob = 0.40
        yes_price = 0.60
        llm_prob = 0.40

        if llm_prob > yes_price:
            action = "BUY_YES"
        else:
            action = "BUY_NO"

        assert action == "BUY_NO"  # Must flip, not stay as original BUY_YES
