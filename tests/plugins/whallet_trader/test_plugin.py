"""Tests for whallet_trader plugin — covers all uncovered lines."""

import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.plugins.whallet_trader.models import (
    OrderStatus,
    OrderType,
    PortfolioPosition,
    RiskCheckResult,
    RiskLevel,
    TradeAction,
    TradeExecution,
    TradeOrder,
    TradeSignal,
)
from overblick.plugins.whallet_trader.plugin import WhalletTraderPlugin


def _make_ctx(tmp_path):
    ctx = MagicMock(spec=PluginContext)
    ctx.identity_name = "polytrader"
    ctx.data_dir = tmp_path / "data"
    ctx.log_dir = tmp_path / "logs"
    ctx.identity = MagicMock()
    ctx.identity.raw_config = {}
    ctx.audit_log = MagicMock()
    ctx.audit_log.log = MagicMock()
    ctx.get_secret = MagicMock(return_value=None)
    return ctx


def _make_signal(**overrides):
    defaults = dict(
        market_id="m1",
        market_question="Will it rain?",
        action=TradeAction.BUY_YES,
        outcome="YES",
        market_price=Decimal("0.50"),
        our_probability=Decimal("0.60"),
        probability_edge=Decimal("0.10"),
        confidence_score=80.0,
        volume_score=70.0,
        time_horizon_days=14.0,
        suggested_position_size_percent=Decimal("3.0"),
        kelly_fraction=Decimal("0.15"),
        urgency="medium",
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _make_execution(**overrides):
    defaults = dict(
        order_id="ord_1",
        signal_id="sig_1",
        market_id="m1",
        market_question="Q?",
        outcome="YES",
        action=TradeAction.BUY_YES,
        quantity=Decimal("10"),
        execution_price=Decimal("0.50"),
        position_size_usd=Decimal("5"),
        transaction_hash="0xabc",
        block_number=100,
        gas_used=200000,
        gas_price_gwei=Decimal("30"),
        expected_price=Decimal("0.50"),
        slippage_percent=Decimal("0.01"),
        simulation=True,
    )
    defaults.update(overrides)
    return TradeExecution(**defaults)


def _make_position(**overrides):
    defaults = dict(
        market_id="m1",
        market_question="Q?",
        outcome="YES",
        quantity=Decimal("100"),
        average_price=Decimal("0.50"),
        current_price=Decimal("0.55"),
        invested_amount=Decimal("50"),
        current_value=Decimal("55"),
        unrealized_pnl=Decimal("5"),
        unrealized_pnl_percent=Decimal("10"),
        first_bought=datetime.now(),
    )
    defaults.update(overrides)
    return PortfolioPosition(**defaults)


class TestSetup:
    @pytest.mark.asyncio
    async def test_should_setup_plugin(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        assert plugin._trading_executor is not None
        assert plugin._risk_manager is not None
        assert plugin._state_file is not None

    @pytest.mark.asyncio
    async def test_should_load_config_from_identity(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.identity.raw_config = {
            "whallet_trader": {
                "simulation_mode": False,
                "max_position_size_percent": 10.0,
                "daily_loss_limit_percent": 5.0,
                "gas_price_multiplier": 1.2,
                "check_interval_seconds": 30,
            }
        }
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        assert plugin._config["simulation_mode"] is False
        assert plugin._config["max_position_size_percent"] == 10.0
        assert plugin._check_interval_seconds == 30


class TestTick:
    @pytest.mark.asyncio
    async def test_should_skip_when_interval_not_elapsed(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 9999999999.0  # Far future
        await plugin.tick()
        # Nothing should happen

    @pytest.mark.asyncio
    async def test_should_process_on_interval(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 0
        await plugin.tick()
        assert plugin._last_check_time > 0

    @pytest.mark.asyncio
    async def test_should_handle_tick_exception(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 0
        plugin._process_pending_signals = AsyncMock(side_effect=RuntimeError("boom"))
        await plugin.tick()
        ctx.audit_log.log.assert_called()


class TestProcessPendingSignals:
    @pytest.mark.asyncio
    async def test_should_skip_when_no_signals(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        await plugin._process_pending_signals()
        # No error

    @pytest.mark.asyncio
    async def test_should_process_signal(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        signal = _make_signal()
        plugin._pending_signals.append(signal)

        # Mock the process method
        plugin._process_trading_signal = AsyncMock()
        await plugin._process_pending_signals()
        plugin._process_trading_signal.assert_called_once_with(signal)

    @pytest.mark.asyncio
    async def test_should_retry_failed_signals(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        signal = _make_signal()
        signal.retry_count = 0
        plugin._pending_signals.append(signal)

        plugin._process_trading_signal = AsyncMock(side_effect=RuntimeError("err"))
        await plugin._process_pending_signals()
        # Signal should be re-queued
        assert len(plugin._pending_signals) == 1
        assert plugin._pending_signals[0].retry_count == 1

    @pytest.mark.asyncio
    async def test_should_drop_after_max_retries(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        signal = _make_signal()
        signal.retry_count = 2  # Will be incremented to 3
        plugin._pending_signals.append(signal)

        plugin._process_trading_signal = AsyncMock(side_effect=RuntimeError("err"))
        await plugin._process_pending_signals()
        assert len(plugin._pending_signals) == 0


class TestProcessTradingSignal:
    @pytest.mark.asyncio
    async def test_should_reject_without_executor(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._trading_executor = None
        signal = _make_signal()
        await plugin._process_trading_signal(signal)
        # Should log error and return

    @pytest.mark.asyncio
    async def test_should_reject_without_risk_manager(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._risk_manager = None
        signal = _make_signal()
        await plugin._process_trading_signal(signal)

    @pytest.mark.asyncio
    async def test_should_reject_signal_that_fails_risk(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        signal = _make_signal()
        check = RiskCheckResult(
            approved=False,
            reason="Too risky",
            risk_score=90.0,
            risk_level=RiskLevel.EXTREME,
        )
        plugin._risk_manager.check_signal_risk = AsyncMock(return_value=check)
        await plugin._process_trading_signal(signal)
        ctx.audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_should_execute_approved_signal(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        signal = _make_signal()
        check = RiskCheckResult(
            approved=True, risk_score=40.0, max_position_size_usd=Decimal("100")
        )
        plugin._risk_manager.check_signal_risk = AsyncMock(return_value=check)
        plugin._risk_manager.calculate_position_size = AsyncMock(return_value=Decimal("50"))

        execution = _make_execution()
        plugin._trading_executor.execute_order = AsyncMock(return_value=execution)

        await plugin._process_trading_signal(signal)
        assert len(plugin._trade_history) == 1
        ctx.audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_should_trim_trade_history(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._trade_history = [_make_execution() for _ in range(1001)]

        signal = _make_signal()
        check = RiskCheckResult(
            approved=True, risk_score=40.0, max_position_size_usd=Decimal("100")
        )
        plugin._risk_manager.check_signal_risk = AsyncMock(return_value=check)
        plugin._risk_manager.calculate_position_size = AsyncMock(return_value=Decimal("50"))
        plugin._trading_executor.execute_order = AsyncMock(return_value=_make_execution())

        await plugin._process_trading_signal(signal)
        assert len(plugin._trade_history) == 1000

    @pytest.mark.asyncio
    async def test_should_handle_trading_error(self, tmp_path):
        from overblick.plugins.whallet_trader.trading_executor import TradingError

        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        signal = _make_signal()
        check = RiskCheckResult(
            approved=True, risk_score=40.0, max_position_size_usd=Decimal("100")
        )
        plugin._risk_manager.check_signal_risk = AsyncMock(return_value=check)
        plugin._risk_manager.calculate_position_size = AsyncMock(return_value=Decimal("50"))
        plugin._trading_executor.execute_order = AsyncMock(side_effect=TradingError("fail"))

        await plugin._process_trading_signal(signal)
        assert len(plugin._active_orders) == 1
        assert plugin._active_orders[0].status == "failed"
        ctx.audit_log.log.assert_called()


class TestCheckActiveOrders:
    @pytest.mark.asyncio
    async def test_should_skip_when_no_orders(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        await plugin._check_active_orders()

    @pytest.mark.asyncio
    async def test_should_skip_when_no_executor(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._trading_executor = None
        plugin._active_orders = [MagicMock()]
        await plugin._check_active_orders()

    @pytest.mark.asyncio
    async def test_should_remove_completed_orders(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        order = MagicMock()
        order.status = "completed"
        order.order_id = "ord1"
        plugin._active_orders = [order]
        await plugin._check_active_orders()
        assert len(plugin._active_orders) == 0

    @pytest.mark.asyncio
    async def test_should_check_pending_order_status(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        order = MagicMock()
        order.status = "submitted"
        order.order_id = "ord1"
        order.market_question = "Test?"

        plugin._trading_executor.check_order_status = AsyncMock(return_value="completed")
        execution = _make_execution()
        plugin._trading_executor.get_order_execution = AsyncMock(return_value=execution)

        plugin._active_orders = [order]
        await plugin._check_active_orders()
        assert len(plugin._active_orders) == 0

    @pytest.mark.asyncio
    async def test_should_handle_failed_order(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        order = MagicMock()
        order.status = "submitted"
        order.order_id = "ord1"

        plugin._trading_executor.check_order_status = AsyncMock(return_value="failed")
        plugin._active_orders = [order]
        await plugin._check_active_orders()
        assert len(plugin._active_orders) == 0

    @pytest.mark.asyncio
    async def test_should_handle_check_exception(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        order = MagicMock()
        order.status = "submitted"
        order.order_id = "ord1"

        plugin._trading_executor.check_order_status = AsyncMock(side_effect=RuntimeError("err"))
        plugin._active_orders = [order]
        await plugin._check_active_orders()
        # Order still in list
        assert len(plugin._active_orders) == 1

    @pytest.mark.asyncio
    async def test_should_handle_no_execution_on_completed(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        order = MagicMock()
        order.status = "submitted"
        order.order_id = "ord1"

        plugin._trading_executor.check_order_status = AsyncMock(return_value="completed")
        plugin._trading_executor.get_order_execution = AsyncMock(return_value=None)
        plugin._active_orders = [order]
        await plugin._check_active_orders()
        # Order still in list since no execution returned
        assert len(plugin._active_orders) == 1


class TestUpdatePortfolioPrices:
    @pytest.mark.asyncio
    async def test_should_skip_when_no_positions(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        await plugin._update_portfolio_prices()

    @pytest.mark.asyncio
    async def test_should_skip_when_no_executor(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._trading_executor = None
        plugin._portfolio_positions = [_make_position()]
        await plugin._update_portfolio_prices()

    @pytest.mark.asyncio
    async def test_should_update_position_prices(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        pos = _make_position(invested_amount=Decimal("50"))
        plugin._portfolio_positions = [pos]
        plugin._trading_executor.get_current_price = AsyncMock(return_value=Decimal("0.60"))

        await plugin._update_portfolio_prices()
        assert pos.current_price == Decimal("0.60")
        assert pos.unrealized_pnl_percent != Decimal("0")

    @pytest.mark.asyncio
    async def test_should_handle_price_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        pos = _make_position()
        plugin._portfolio_positions = [pos]
        plugin._trading_executor.get_current_price = AsyncMock(side_effect=RuntimeError("err"))

        await plugin._update_portfolio_prices()
        # Should not raise

    @pytest.mark.asyncio
    async def test_should_skip_pnl_percent_when_zero_invested(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        pos = _make_position(invested_amount=Decimal("0"))
        plugin._portfolio_positions = [pos]
        plugin._trading_executor.get_current_price = AsyncMock(return_value=Decimal("0.60"))
        await plugin._update_portfolio_prices()
        # Should not divide by zero


class TestApplyRiskManagement:
    @pytest.mark.asyncio
    async def test_should_skip_when_no_risk_manager(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._risk_manager = None
        await plugin._apply_risk_management()

    @pytest.mark.asyncio
    async def test_should_cancel_orders_on_daily_loss(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        plugin._risk_manager.calculate_daily_pnl = MagicMock(return_value=Decimal("-5.0"))

        order = MagicMock()
        order.status = "pending"
        order.order_id = "ord1"
        plugin._active_orders = [order]
        plugin._trading_executor.cancel_order = AsyncMock()

        await plugin._apply_risk_management()
        plugin._trading_executor.cancel_order.assert_called()
        ctx.audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_should_handle_cancel_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        plugin._risk_manager.calculate_daily_pnl = MagicMock(return_value=Decimal("-5.0"))

        order = MagicMock()
        order.status = "submitted"
        order.order_id = "ord1"
        plugin._active_orders = [order]
        plugin._trading_executor.cancel_order = AsyncMock(side_effect=RuntimeError("err"))

        await plugin._apply_risk_management()
        # Should not raise

    @pytest.mark.asyncio
    async def test_should_detect_stop_loss(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        plugin._risk_manager.calculate_daily_pnl = MagicMock(return_value=Decimal("0"))

        pos = _make_position(
            current_price=Decimal("0.30"),
            stop_loss_price=Decimal("0.35"),
        )
        plugin._portfolio_positions = [pos]
        await plugin._apply_risk_management()
        # Just logs, no error

    @pytest.mark.asyncio
    async def test_should_detect_take_profit(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        plugin._risk_manager.calculate_daily_pnl = MagicMock(return_value=Decimal("0"))

        pos = _make_position(
            current_price=Decimal("0.80"),
            take_profit_price=Decimal("0.75"),
        )
        plugin._portfolio_positions = [pos]
        await plugin._apply_risk_management()
        # Just logs, no error

    @pytest.mark.asyncio
    async def test_should_skip_positions_without_sl_tp(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        plugin._risk_manager.calculate_daily_pnl = MagicMock(return_value=Decimal("0"))

        pos = _make_position(stop_loss_price=None, take_profit_price=None)
        plugin._portfolio_positions = [pos]
        await plugin._apply_risk_management()


class TestUpdatePortfolioWithExecution:
    def test_should_create_new_position_on_buy(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        execution = _make_execution(action=TradeAction.BUY_YES)
        plugin._update_portfolio_with_execution(execution)
        assert len(plugin._portfolio_positions) == 1

    def test_should_update_existing_position_on_buy(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)

        pos = _make_position(market_id="m1", outcome="YES")
        plugin._portfolio_positions = [pos]
        execution = _make_execution(action=TradeAction.BUY_YES)
        plugin._update_portfolio_with_execution(execution)
        assert len(plugin._portfolio_positions) == 1

    def test_should_reduce_position_on_sell(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)

        pos = _make_position(market_id="m1", outcome="YES", quantity=Decimal("100"))
        plugin._portfolio_positions = [pos]
        execution = _make_execution(
            action=TradeAction.SELL_YES,
            quantity=Decimal("50"),
        )
        plugin._update_portfolio_with_execution(execution)
        assert len(plugin._portfolio_positions) == 1
        assert plugin._portfolio_positions[0].quantity < Decimal("100")

    def test_should_remove_position_when_fully_sold(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)

        pos = _make_position(market_id="m1", outcome="YES", quantity=Decimal("10"))
        plugin._portfolio_positions = [pos]
        execution = _make_execution(
            action=TradeAction.SELL_YES,
            quantity=Decimal("10"),
        )
        plugin._update_portfolio_with_execution(execution)
        assert len(plugin._portfolio_positions) == 0

    def test_should_ignore_sell_without_existing_position(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)

        execution = _make_execution(action=TradeAction.SELL_YES)
        plugin._update_portfolio_with_execution(execution)
        assert len(plugin._portfolio_positions) == 0

    def test_should_handle_zero_quantity_after_buy(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)

        pos = _make_position(market_id="m1", outcome="YES", quantity=Decimal("0"))
        plugin._portfolio_positions = [pos]
        execution = _make_execution(
            action=TradeAction.BUY_YES,
            quantity=Decimal("0"),
        )
        plugin._update_portfolio_with_execution(execution)


class TestLogTradeExecution:
    def test_should_log_with_audit(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        execution = _make_execution()
        risk_check = MagicMock()
        risk_check.risk_level.value = "medium"
        plugin._log_trade_execution(execution, risk_check)
        ctx.audit_log.log.assert_called()

    def test_should_skip_without_audit_log(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.audit_log = None
        plugin = WhalletTraderPlugin(ctx)
        execution = _make_execution()
        plugin._log_trade_execution(execution, None)


class TestLoadSaveState:
    @pytest.mark.asyncio
    async def test_should_load_state(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        state_file = tmp_path / "data" / "whallet_trader_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({
            "portfolio_positions": [],
            "trade_history": [],
            "active_orders": [],
            "last_check_time": 123.0,
        }))
        plugin._state_file = state_file
        plugin._load_state()
        assert plugin._last_check_time == 123.0

    def test_should_handle_corrupt_state(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid json")
        plugin._state_file = state_file
        plugin._load_state()
        assert plugin._portfolio_positions == []

    def test_should_skip_load_when_no_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        plugin._state_file = tmp_path / "nonexistent.json"
        plugin._load_state()

    def test_should_skip_load_when_no_state_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        plugin._state_file = None
        plugin._load_state()

    def test_should_save_state(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        state_file = tmp_path / "state.json"
        plugin._state_file = state_file
        plugin._last_check_time = 100.0
        plugin._save_state()
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["last_check_time"] == 100.0

    def test_should_skip_save_when_no_state_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        plugin._state_file = None
        plugin._save_state()

    def test_should_handle_save_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        plugin._state_file = MagicMock()
        plugin._state_file.write_text = MagicMock(side_effect=OSError("disk full"))
        plugin._save_state()


class TestSubmitTradingSignal:
    @pytest.mark.asyncio
    async def test_should_accept_valid_signal(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        signal = _make_signal()
        result = await plugin.submit_trading_signal(signal)
        assert result is True
        assert len(plugin._pending_signals) == 1

    @pytest.mark.asyncio
    async def test_should_reject_invalid_signal(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        signal = _make_signal(signal_id="", market_id="")
        result = await plugin.submit_trading_signal(signal)
        assert result is False

    @pytest.mark.asyncio
    async def test_should_reject_non_simulation_in_sim_mode(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        signal = _make_signal(allow_simulation=False)
        result = await plugin.submit_trading_signal(signal)
        assert result is False


class TestGetPortfolioSummary:
    @pytest.mark.asyncio
    async def test_should_return_summary(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        pos = _make_position(invested_amount=Decimal("100"), current_value=Decimal("120"))
        plugin._portfolio_positions = [pos]

        summary = await plugin.get_portfolio_summary()
        assert summary["positions_count"] == 1
        assert summary["total_invested_usd"] == Decimal("100")
        assert summary["total_value_usd"] == Decimal("120")
        assert summary["total_pnl_usd"] == Decimal("20")
        assert summary["simulation_mode"] is True

    @pytest.mark.asyncio
    async def test_should_handle_empty_portfolio(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()

        summary = await plugin.get_portfolio_summary()
        assert summary["positions_count"] == 0
        assert summary["total_pnl_percent"] == 0

    @pytest.mark.asyncio
    async def test_should_handle_no_risk_manager(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = WhalletTraderPlugin(ctx)
        await plugin.setup()
        plugin._risk_manager = None

        summary = await plugin.get_portfolio_summary()
        assert summary["daily_pnl_percent"] == 0
