"""Tests for ExitEvaluator — mechanical triggers and LLM exit decisions."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from overblick.plugins.whallet_trader.exit_evaluator import ExitEvaluator
from overblick.plugins.whallet_trader.models import (
    ExitAction,
    PortfolioPosition,
)


def _make_position(**overrides):
    defaults = {
        "position_id": "pos_test",
        "market_id": "m1",
        "market_question": "Will it rain tomorrow?",
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


class TestMechanicalTriggers:
    """Test fast, deterministic exit triggers."""

    @pytest.mark.asyncio
    async def test_profit_target_triggers_sell(self):
        """Positions with >50% profit should trigger sell."""
        ev = ExitEvaluator()
        pos = _make_position(
            unrealized_pnl_percent=Decimal("55"),
            unrealized_pnl=Decimal("5.5"),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "profit_target"

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_sell(self):
        """Positions with >25% loss should trigger sell."""
        ev = ExitEvaluator()
        pos = _make_position(
            unrealized_pnl_percent=Decimal("-30"),
            unrealized_pnl=Decimal("-3"),
            current_price=Decimal("0.35"),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "stop_loss"

    @pytest.mark.asyncio
    async def test_edge_decay_triggers_sell(self):
        """When price returns to entry after >1h, edge is gone."""
        ev = ExitEvaluator()
        pos = _make_position(
            average_price=Decimal("0.50"),
            current_price=Decimal("0.51"),  # Only 1% from entry
            unrealized_pnl_percent=Decimal("2"),
            first_bought=datetime.now() - timedelta(hours=3),  # Held >1h
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "edge_decay"

    @pytest.mark.asyncio
    async def test_time_decay_triggers_sell(self):
        """Markets expiring within 24h should trigger sell."""
        ev = ExitEvaluator()
        pos = _make_position()
        market_data = {
            "end_time": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
        }
        decision = await ev.assess_position(pos, market_data=market_data)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "time_decay"

    @pytest.mark.asyncio
    async def test_low_liquidity_triggers_sell(self):
        """Markets with <$200 volume should trigger sell."""
        ev = ExitEvaluator()
        pos = _make_position()
        market_data = {"volume_24h": 100}
        decision = await ev.assess_position(pos, market_data=market_data)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "low_liquidity"

    @pytest.mark.asyncio
    async def test_near_resolution_triggers_sell(self):
        """Positions at extreme prices (>95% or <5%) should trigger sell."""
        ev = ExitEvaluator()
        pos = _make_position(
            current_price=Decimal("0.97"),
            unrealized_pnl_percent=Decimal("40"),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.SELL
        assert decision.trigger == "near_resolution"

    @pytest.mark.asyncio
    async def test_healthy_position_holds(self):
        """A normal position with decent P&L should HOLD."""
        ev = ExitEvaluator()
        pos = _make_position(
            average_price=Decimal("0.40"),
            current_price=Decimal("0.55"),
            unrealized_pnl_percent=Decimal("30"),
        )
        decision = await ev.assess_position(pos)
        assert decision.action == ExitAction.HOLD


class TestLLMThrottling:
    """Test LLM rate limiting."""

    def test_can_do_llm_within_limit(self):
        ev = ExitEvaluator(max_llm_assessments_per_hour=3)
        assert ev._can_do_llm_assessment() is True

    def test_cannot_exceed_limit(self):
        ev = ExitEvaluator(max_llm_assessments_per_hour=2)
        import time

        ev._llm_timestamps = [time.time(), time.time()]
        assert ev._can_do_llm_assessment() is False

    def test_old_timestamps_expire(self):
        ev = ExitEvaluator(max_llm_assessments_per_hour=2)
        import time

        ev._llm_timestamps = [time.time() - 7200, time.time() - 7200]
        assert ev._can_do_llm_assessment() is True


class TestLLMResponseParsing:
    """Test JSON response parsing from LLM."""

    def test_parse_sell_response(self):
        ev = ExitEvaluator()
        content = '{"action": "sell", "reasoning": "Edge gone", "confidence": 80, "sell_fraction": 1.0}'
        decision = ev._parse_llm_response(content, "m1")
        assert decision.action == ExitAction.SELL
        assert decision.confidence == 80.0
        assert "Edge gone" in decision.reasoning

    def test_parse_hold_response(self):
        ev = ExitEvaluator()
        content = '{"action": "hold", "reasoning": "Still good", "confidence": 70}'
        decision = ev._parse_llm_response(content, "m1")
        assert decision.action == ExitAction.HOLD

    def test_parse_partial_sell(self):
        ev = ExitEvaluator()
        content = '{"action": "partial_sell", "reasoning": "Take some profit", "confidence": 65, "sell_fraction": 0.5}'
        decision = ev._parse_llm_response(content, "m1")
        assert decision.action == ExitAction.PARTIAL_SELL
        assert decision.sell_fraction == Decimal("0.5")

    def test_parse_invalid_json_returns_hold(self):
        ev = ExitEvaluator()
        decision = ev._parse_llm_response("not json at all", "m1")
        assert decision.action == ExitAction.HOLD

    def test_parse_json_embedded_in_text(self):
        ev = ExitEvaluator()
        content = 'Here is my analysis: {"action": "sell", "reasoning": "Time to go", "confidence": 90}'
        decision = ev._parse_llm_response(content, "m1")
        assert decision.action == ExitAction.SELL
