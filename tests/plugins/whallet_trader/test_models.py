"""Tests for whallet_trader models — covers TradeOrder.from_signal edge cases."""

from decimal import Decimal

from overblick.plugins.whallet_trader.models import (
    TradeAction,
    TradeOrder,
    TradeSignal,
)


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
    }
    defaults.update(overrides)
    return TradeSignal(**defaults)


class TestTradeOrderFromSignal:
    def test_should_calculate_quantity_when_price_positive(self):
        signal = _make_signal(market_price=Decimal("0.50"))
        order = TradeOrder.from_signal(signal, Decimal("100"))
        assert order.quantity == Decimal("200")  # 100 / 0.50
        assert order.market_id == "m1"
        assert order.signal_id == signal.signal_id

    def test_should_set_quantity_zero_when_market_price_zero(self):
        signal = _make_signal(market_price=Decimal("0"))
        order = TradeOrder.from_signal(signal, Decimal("100"))
        assert order.quantity == Decimal("0")

    def test_should_propagate_order_type_and_limit_price(self):
        from overblick.plugins.whallet_trader.models import OrderType

        signal = _make_signal(
            order_type=OrderType.LIMIT,
            limit_price=Decimal("0.45"),
        )
        order = TradeOrder.from_signal(signal, Decimal("50"))
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == Decimal("0.45")
