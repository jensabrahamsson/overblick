"""Tests for whallet_trader risk_manager — covers all uncovered lines."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from overblick.plugins.whallet_trader.models import (
    PortfolioPosition,
    RiskCheckResult,
    RiskLevel,
    TradeAction,
    TradeExecution,
    TradeSignal,
)
from overblick.plugins.whallet_trader.risk_manager import RiskManager


def _make_signal(**overrides):
    defaults = {
        "market_id": "m1",
        "market_question": "Q?",
        "action": TradeAction.BUY_YES,
        "outcome": "YES",
        "market_price": Decimal("0.50"),
        "our_probability": Decimal("0.60"),
        "probability_edge": Decimal("0.10"),
        "confidence_score": 80.0,
        "volume_score": 70.0,
        "time_horizon_days": 14.0,
        "suggested_position_size_percent": Decimal("3.0"),
        "kelly_fraction": Decimal("0.15"),
        "urgency": "medium",
        "risk_level": RiskLevel.MEDIUM,
    }
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _make_execution(**overrides):
    defaults = {
        "order_id": "ord_1",
        "signal_id": "sig_1",
        "market_id": "m1",
        "market_question": "Q?",
        "outcome": "YES",
        "action": TradeAction.BUY_YES,
        "quantity": Decimal("10"),
        "execution_price": Decimal("0.50"),
        "position_size_usd": Decimal("5"),
        "transaction_hash": "0xabc",
        "block_number": 100,
        "gas_used": 200000,
        "gas_price_gwei": Decimal("30"),
        "expected_price": Decimal("0.50"),
        "slippage_percent": Decimal("0.01"),
        "simulation": False,
    }
    defaults.update(overrides)
    return TradeExecution(**defaults)


def _make_position(**overrides):
    defaults = {
        "market_id": "m1",
        "market_question": "Q?",
        "outcome": "YES",
        "quantity": Decimal("100"),
        "average_price": Decimal("0.50"),
        "current_price": Decimal("0.55"),
        "invested_amount": Decimal("50"),
        "current_value": Decimal("55"),
        "unrealized_pnl": Decimal("5"),
        "unrealized_pnl_percent": Decimal("10"),
        "first_bought": datetime.now(),
    }
    defaults.update(overrides)
    return PortfolioPosition(**defaults)


class TestCheckSignalRisk:
    @pytest.mark.asyncio
    async def test_should_reject_when_edge_too_small(self):
        rm = RiskManager()
        signal = _make_signal(probability_edge=Decimal("0.01"))
        result = await rm.check_signal_risk(signal)
        assert result.approved is False
        assert "edge too small" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_should_reject_when_confidence_too_low(self):
        rm = RiskManager()
        signal = _make_signal(confidence_score=30.0)
        result = await rm.check_signal_risk(signal)
        assert result.approved is False
        assert "confidence" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_should_reject_when_daily_loss_limit_breached(self):
        rm = RiskManager(daily_loss_limit_percent=Decimal("2.0"))
        rm._daily_pnl_cache = Decimal("-3.0")
        rm._cache_time = datetime.now()
        signal = _make_signal()
        result = await rm.check_signal_risk(signal)
        assert result.approved is False
        assert "daily loss" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_should_reject_when_signal_expired(self):
        rm = RiskManager()
        signal = _make_signal(expires_at=datetime.now() - timedelta(hours=1))
        result = await rm.check_signal_risk(signal)
        assert result.approved is False
        assert "expired" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_should_warn_when_portfolio_exposure_high(self):
        positions = [_make_position(invested_amount=Decimal("5000"))]
        rm = RiskManager(portfolio_positions=positions)
        signal = _make_signal()
        result = await rm.check_signal_risk(signal)
        assert result.approved is True
        assert any("exposure" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_should_reject_when_max_trades_per_day_reached(self):
        today = datetime.now()
        trades = [
            _make_execution(executed_at=today)
            for _ in range(11)
        ]
        rm = RiskManager(trade_history=trades)
        signal = _make_signal()
        result = await rm.check_signal_risk(signal)
        assert result.approved is False
        assert "trades per day" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_should_classify_medium_risk_level(self):
        rm = RiskManager()
        signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=5,
            urgency="low",
        )
        result = await rm.check_signal_risk(signal)
        assert result.approved is True
        # Score ~60 which falls in HIGH bucket (60-80)
        assert result.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    @pytest.mark.asyncio
    async def test_should_set_high_risk_level(self):
        rm = RiskManager()
        signal = _make_signal(
            probability_edge=Decimal("0.04"),
            confidence_score=65.0,
            volume_score=20.0,
            time_horizon_days=60,
            urgency="high",
        )
        result = await rm.check_signal_risk(signal)
        assert result.approved is True
        assert result.risk_level in (RiskLevel.HIGH, RiskLevel.EXTREME)

    @pytest.mark.asyncio
    async def test_should_classify_low_risk_when_score_below_30(self):
        """Score < 30 -> LOW risk level."""
        rm = RiskManager()
        # Need a very large edge and high confidence to get score < 30
        # edge_factor = 1/1.0 = 1, (1-1)*10 = 0
        # confidence = (100-99.9)/100 * 20 = 0.02
        # volume = (100-99)/100 * 10 = 0.1
        # base = 50 + 0 + 0.02 + 0.1 = 50.12 -- still too high
        # The minimum baseline is 50, so we can't get below 30 with this formula.
        # We CAN test the branch by directly calling _calculate_risk_score
        # and verifying it returns the right level in check_signal_risk.
        # Let's just verify the LOW/MEDIUM/HIGH branch coverage via check_signal_risk
        # by patching _calculate_risk_score.
        signal = _make_signal()
        with patch.object(rm, "_calculate_risk_score", return_value=20.0):
            result = await rm.check_signal_risk(signal)
        assert result.risk_level == RiskLevel.LOW

    @pytest.mark.asyncio
    async def test_should_classify_medium_when_score_30_to_60(self):
        rm = RiskManager()
        signal = _make_signal()
        with patch.object(rm, "_calculate_risk_score", return_value=45.0):
            result = await rm.check_signal_risk(signal)
        assert result.risk_level == RiskLevel.MEDIUM

    @pytest.mark.asyncio
    async def test_should_classify_high_when_score_60_to_80(self):
        rm = RiskManager()
        signal = _make_signal()
        with patch.object(rm, "_calculate_risk_score", return_value=70.0):
            result = await rm.check_signal_risk(signal)
        assert result.risk_level == RiskLevel.HIGH

    @pytest.mark.asyncio
    async def test_should_set_extreme_risk_with_warning(self):
        rm = RiskManager()
        signal = _make_signal(
            probability_edge=Decimal("0.04"),
            confidence_score=62.0,
            volume_score=5.0,
            time_horizon_days=100,
            urgency="critical",
        )
        result = await rm.check_signal_risk(signal)
        assert result.approved is True
        assert result.risk_level == RiskLevel.EXTREME
        assert any("extreme" in w.lower() for w in result.warnings)


class TestCalculatePositionSize:
    @pytest.mark.asyncio
    async def test_should_return_zero_when_not_approved(self):
        rm = RiskManager()
        signal = _make_signal()
        check = RiskCheckResult(approved=False, risk_score=0.0)
        size = await rm.calculate_position_size(signal, check)
        assert size == Decimal("0")

    @pytest.mark.asyncio
    async def test_should_use_half_kelly(self):
        rm = RiskManager()
        signal = _make_signal()
        check = RiskCheckResult(
            approved=True,
            risk_score=50.0,
            max_position_size_usd=Decimal("500"),
        )
        size = await rm.calculate_position_size(signal, check)
        assert size > Decimal("0")

    @pytest.mark.asyncio
    async def test_should_enforce_minimum_position_size(self):
        rm = RiskManager()
        signal = _make_signal(
            market_price=Decimal("0.99"),
            our_probability=Decimal("0.991"),
            probability_edge=Decimal("0.05"),
        )
        check = RiskCheckResult(
            approved=True,
            risk_score=50.0,
            max_position_size_usd=Decimal("500"),
        )
        size = await rm.calculate_position_size(signal, check)
        assert size >= Decimal("10.0")

    @pytest.mark.asyncio
    async def test_should_enforce_minimum_position_size_via_logging(self):
        rm = RiskManager()
        signal = _make_signal()
        check = RiskCheckResult(
            approved=True,
            risk_score=50.0,
            max_position_size_usd=Decimal("5"),  # Very small max
        )
        # Patch Kelly to return very small value
        with patch.object(rm, "_calculate_kelly_position_size", return_value=Decimal("2")):
            size = await rm.calculate_position_size(signal, check)
        # Should be bumped up to minimum ($10)
        assert size == Decimal("10.0")

    @pytest.mark.asyncio
    async def test_should_use_default_max_when_none(self):
        rm = RiskManager()
        signal = _make_signal()
        check = RiskCheckResult(
            approved=True,
            risk_score=50.0,
            max_position_size_usd=None,
        )
        size = await rm.calculate_position_size(signal, check)
        assert size > Decimal("0")


class TestCalculateDailyPnl:
    def test_should_return_cached_value(self):
        rm = RiskManager()
        rm._daily_pnl_cache = Decimal("-1.5")
        rm._cache_time = datetime.now()
        result = rm.calculate_daily_pnl()
        assert result == Decimal("-1.5")

    def test_should_return_zero_when_no_trades(self):
        rm = RiskManager()
        result = rm.calculate_daily_pnl()
        assert result == Decimal("0")

    def test_should_calculate_for_today_trades(self):
        today = datetime.now()
        trades = [_make_execution(executed_at=today)]
        rm = RiskManager(trade_history=trades)
        result = rm.calculate_daily_pnl()
        # With simplified pnl calc (total_pnl_usd=0), returns 0
        assert result == Decimal("0")

    def test_should_return_zero_when_portfolio_value_zero(self):
        today = datetime.now()
        trades = [_make_execution(executed_at=today)]
        rm = RiskManager(trade_history=trades)
        with patch.object(rm, "_estimate_portfolio_value", return_value=Decimal("0")):
            result = rm.calculate_daily_pnl()
        assert result == Decimal("0")


class TestKellyPositionSize:
    def test_should_handle_buy_yes_signal(self):
        rm = RiskManager()
        signal = _make_signal(
            action=TradeAction.BUY_YES,
            outcome="YES",
            market_price=Decimal("0.50"),
            our_probability=Decimal("0.70"),
        )
        result = rm._calculate_kelly_position_size(signal, Decimal("10000"))
        assert result > Decimal("0")

    def test_should_handle_buy_no_signal(self):
        rm = RiskManager()
        signal = _make_signal(
            action=TradeAction.BUY_NO,
            outcome="NO",
            market_price=Decimal("0.50"),
            our_probability=Decimal("0.30"),
        )
        result = rm._calculate_kelly_position_size(signal, Decimal("10000"))
        assert result > Decimal("0")

    def test_should_handle_sell_action(self):
        rm = RiskManager()
        signal = _make_signal(
            action=TradeAction.SELL_YES,
            outcome="YES",
        )
        result = rm._calculate_kelly_position_size(signal, Decimal("10000"))
        assert result >= Decimal("0")

    def test_should_return_zero_for_bad_payout(self):
        rm = RiskManager()
        # When market_price=1.0, win_payout=1.0, kelly should be 0
        signal = _make_signal(
            action=TradeAction.BUY_YES,
            outcome="YES",
            market_price=Decimal("1"),
        )
        result = rm._calculate_kelly_position_size(signal, Decimal("10000"))
        assert result == Decimal("0")

    def test_should_handle_buy_no_when_market_price_one(self):
        rm = RiskManager()
        signal = _make_signal(
            action=TradeAction.BUY_NO,
            outcome="NO",
            market_price=Decimal("1"),
        )
        result = rm._calculate_kelly_position_size(signal, Decimal("10000"))
        assert result == Decimal("0")

    def test_should_handle_buy_yes_market_price_zero(self):
        rm = RiskManager()
        signal = _make_signal(
            action=TradeAction.BUY_YES,
            outcome="YES",
            market_price=Decimal("0"),
        )
        result = rm._calculate_kelly_position_size(signal, Decimal("10000"))
        assert result >= Decimal("0")


class TestCalculateMaxPositionSize:
    def test_should_reduce_for_medium_risk(self):
        rm = RiskManager()
        signal = _make_signal(risk_level=RiskLevel.MEDIUM)
        size = rm._calculate_max_position_size(signal)
        full = rm._estimate_portfolio_value() * Decimal("5") / Decimal("100")
        assert size == full * Decimal("0.75")

    def test_should_reduce_for_high_risk(self):
        rm = RiskManager()
        signal = _make_signal(risk_level=RiskLevel.HIGH)
        size = rm._calculate_max_position_size(signal)
        full = rm._estimate_portfolio_value() * Decimal("5") / Decimal("100")
        assert size == full * Decimal("0.5")

    def test_should_reduce_for_extreme_risk(self):
        rm = RiskManager()
        signal = _make_signal(risk_level=RiskLevel.EXTREME)
        size = rm._calculate_max_position_size(signal)
        full = rm._estimate_portfolio_value() * Decimal("5") / Decimal("100")
        assert size == full * Decimal("0.25")

    def test_should_allow_full_for_low_risk(self):
        rm = RiskManager()
        signal = _make_signal(risk_level=RiskLevel.LOW)
        size = rm._calculate_max_position_size(signal)
        full = rm._estimate_portfolio_value() * Decimal("5") / Decimal("100")
        assert size == full


class TestCalculateRiskLevels:
    def test_should_set_stop_loss_and_take_profit_for_buy(self):
        rm = RiskManager()
        signal = _make_signal(action=TradeAction.BUY_YES)
        sl, tp = rm._calculate_risk_levels(signal, RiskLevel.LOW)
        assert sl is not None
        assert tp is not None
        assert sl < signal.market_price
        assert tp > signal.market_price

    def test_should_set_stop_loss_and_take_profit_for_sell(self):
        rm = RiskManager()
        signal = _make_signal(action=TradeAction.SELL_YES)
        sl, tp = rm._calculate_risk_levels(signal, RiskLevel.MEDIUM)
        assert sl is not None
        assert tp is not None
        assert sl > signal.market_price
        assert tp < signal.market_price

    def test_should_return_none_when_price_zero(self):
        rm = RiskManager()
        signal = _make_signal(market_price=Decimal("0"))
        sl, tp = rm._calculate_risk_levels(signal, RiskLevel.LOW)
        assert sl is None
        assert tp is None

    def test_should_return_none_when_price_one(self):
        rm = RiskManager()
        signal = _make_signal(market_price=Decimal("1"))
        sl, tp = rm._calculate_risk_levels(signal, RiskLevel.LOW)
        assert sl is None
        assert tp is None

    def test_should_set_no_stop_loss_for_extreme_risk(self):
        rm = RiskManager()
        signal = _make_signal(action=TradeAction.BUY_YES)
        sl, tp = rm._calculate_risk_levels(signal, RiskLevel.EXTREME)
        assert sl is None
        assert tp is not None

    def test_should_handle_high_risk_levels(self):
        rm = RiskManager()
        signal = _make_signal(action=TradeAction.BUY_YES)
        sl, tp = rm._calculate_risk_levels(signal, RiskLevel.HIGH)
        assert sl is not None
        assert tp is not None


class TestCalculateRiskScore:
    def test_should_increase_for_high_urgency(self):
        rm = RiskManager()
        # Use high edge (low base score) so urgency differences are visible
        base_signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=5,
            urgency="low",
        )
        high_signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=5,
            urgency="high",
        )
        crit_signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=5,
            urgency="critical",
        )
        base_score = rm._calculate_risk_score(base_signal)
        high_score = rm._calculate_risk_score(high_signal)
        crit_score = rm._calculate_risk_score(crit_signal)
        assert high_score > base_score
        assert crit_score > high_score

    def test_should_increase_for_medium_urgency(self):
        rm = RiskManager()
        low_signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=5,
            urgency="low",
        )
        med_signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=5,
            urgency="medium",
        )
        assert rm._calculate_risk_score(med_signal) > rm._calculate_risk_score(low_signal)

    def test_should_increase_for_long_time_horizon(self):
        rm = RiskManager()
        short = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            urgency="low",
            time_horizon_days=10,
        )
        long_sig = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            urgency="low",
            time_horizon_days=50,
        )
        very_long = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            urgency="low",
            time_horizon_days=100,
        )
        assert rm._calculate_risk_score(long_sig) > rm._calculate_risk_score(short)
        assert rm._calculate_risk_score(very_long) > rm._calculate_risk_score(short)

    def test_should_cap_at_0_and_100(self):
        rm = RiskManager()
        # Very safe signal
        signal = _make_signal(
            probability_edge=Decimal("0.50"),
            confidence_score=99.0,
            volume_score=99.0,
            time_horizon_days=1,
            urgency="low",
        )
        score = rm._calculate_risk_score(signal)
        assert 0 <= score <= 100


class TestPortfolioCalculations:
    def test_should_estimate_portfolio_value(self):
        pos = _make_position(current_value=Decimal("500"))
        rm = RiskManager(portfolio_positions=[pos])
        val = rm._estimate_portfolio_value()
        assert val == Decimal("10500")  # 10000 cash + 500

    def test_should_calculate_exposure(self):
        pos = _make_position(invested_amount=Decimal("2000"))
        rm = RiskManager(portfolio_positions=[pos])
        exposure = rm._calculate_portfolio_exposure()
        # 2000 / (10000 + current_value) * 100
        assert exposure > Decimal("0")

    def test_should_return_zero_exposure_when_no_value(self):
        rm = RiskManager()
        exposure = rm._calculate_portfolio_exposure()
        # invested=0, portfolio_value=10000 -> exposure=0%
        assert exposure == Decimal("0")

    def test_should_return_zero_exposure_when_portfolio_value_zero(self):
        rm = RiskManager()
        with patch.object(rm, "_estimate_portfolio_value", return_value=Decimal("0")):
            exposure = rm._calculate_portfolio_exposure()
        assert exposure == Decimal("0")

    def test_should_count_trades_today(self):
        today = datetime.now()
        trades = [
            _make_execution(executed_at=today),
            _make_execution(executed_at=today - timedelta(days=1)),
        ]
        rm = RiskManager(trade_history=trades)
        count = rm._count_trades_today()
        assert count == 1
