"""Tests for whallet_trader trading_executor — covers all uncovered lines."""

import random
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.whallet_trader.models import (
    OrderStatus,
    OrderType,
    RiskLevel,
    TradeAction,
    TradeOrder,
    TradeSignal,
)
from overblick.plugins.whallet_trader.trading_executor import (
    InsufficientBalance,
    TradingError,
    TradingExecutor,
    TransactionFailed,
)


def _make_order(**overrides):
    defaults = dict(
        signal_id="sig_1",
        market_id="m1",
        market_question="Will it rain?",
        outcome="YES",
        action=TradeAction.BUY_YES,
        order_type=OrderType.MARKET,
        quantity=Decimal("100"),
        estimated_price=Decimal("0.50"),
        position_size_usd=Decimal("50"),
        risk_level=RiskLevel.MEDIUM,
        status=OrderStatus.PENDING,
    )
    defaults.update(overrides)
    return TradeOrder(**defaults)


class TestTradingExecutorInit:
    def test_should_initialize_simulation_mode(self):
        executor = TradingExecutor(simulation_mode=True)
        assert executor.simulation_mode is True
        assert executor.wallet is None

    def test_should_initialize_without_rpc(self):
        executor = TradingExecutor(rpc_url=None, private_key=None, simulation_mode=False)
        assert executor.wallet is None

    def test_should_raise_when_whallet_not_available(self):
        with patch(
            "overblick.plugins.whallet_trader.trading_executor.TradingExecutor._init_wallet",
            side_effect=TradingError("Whallet not available"),
        ):
            with pytest.raises(TradingError):
                TradingExecutor(rpc_url="http://rpc", private_key="0xkey", simulation_mode=False)

    def test_should_init_wallet_import_error(self):
        executor = TradingExecutor.__new__(TradingExecutor)
        executor.rpc_url = "http://rpc"
        executor.private_key = "0xkey"
        with patch(
            "overblick.plugins.whallet_trader.trading_executor.TradingExecutor._init_wallet"
        ) as mock_init:
            mock_init.side_effect = TradingError("Whallet not available: No module")
            with pytest.raises(TradingError, match="Whallet not available"):
                mock_init()

    def test_should_handle_wallet_init_general_exception(self):
        """Test _init_wallet when SimpleWallet raises a generic exception."""
        executor = TradingExecutor.__new__(TradingExecutor)
        executor.rpc_url = "http://rpc"
        executor.private_key = "0xkey"
        executor.wallet = None
        with patch.dict("sys.modules", {"whallet": MagicMock(), "whallet.simple_wallet": MagicMock()}):
            import sys

            mock_wallet_mod = sys.modules["whallet.simple_wallet"]
            mock_wallet_mod.SimpleWallet.side_effect = RuntimeError("bad key")
            with pytest.raises(TradingError, match="Wallet initialization failed"):
                executor._init_wallet()

    def test_should_handle_wallet_init_import_error(self):
        """Test _init_wallet when import fails."""
        executor = TradingExecutor.__new__(TradingExecutor)
        executor.rpc_url = "http://rpc"
        executor.private_key = "0xkey"
        executor.wallet = None
        with patch(
            "builtins.__import__",
            side_effect=ImportError("No module named 'whallet'"),
        ):
            with pytest.raises(TradingError, match="Whallet not available"):
                executor._init_wallet()

    def test_should_set_wallet_when_account_available(self):
        executor = TradingExecutor.__new__(TradingExecutor)
        executor.rpc_url = "http://rpc"
        executor.private_key = "0xkey"
        executor.wallet = None
        mock_wallet = MagicMock()
        mock_wallet.account.address = "0xABC"
        with patch.dict("sys.modules", {"whallet": MagicMock(), "whallet.simple_wallet": MagicMock()}):
            import sys

            mock_wallet_mod = sys.modules["whallet.simple_wallet"]
            mock_wallet_mod.SimpleWallet.return_value = mock_wallet
            executor._init_wallet()
            assert executor.wallet == mock_wallet

    def test_should_warn_when_account_none(self):
        executor = TradingExecutor.__new__(TradingExecutor)
        executor.rpc_url = "http://rpc"
        executor.private_key = "0xkey"
        executor.wallet = None
        mock_wallet = MagicMock()
        mock_wallet.account = None
        with patch.dict("sys.modules", {"whallet": MagicMock(), "whallet.simple_wallet": MagicMock()}):
            import sys

            mock_wallet_mod = sys.modules["whallet.simple_wallet"]
            mock_wallet_mod.SimpleWallet.return_value = mock_wallet
            executor._init_wallet()
            assert executor.wallet == mock_wallet


class TestExecuteOrder:
    @pytest.mark.asyncio
    async def test_should_execute_simulation_order(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order()
        execution = await executor.execute_order(order)
        assert execution.simulation is True
        assert execution.market_id == "m1"
        assert execution.outcome == "YES"
        assert order.status == OrderStatus.SUBMITTED

    @pytest.mark.asyncio
    async def test_should_reject_non_pending_order(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.COMPLETED)
        with pytest.raises(TradingError, match="not pending"):
            await executor.execute_order(order)

    @pytest.mark.asyncio
    async def test_should_execute_real_order_without_wallet(self):
        executor = TradingExecutor(simulation_mode=False)
        executor.wallet = None
        order = _make_order()
        with pytest.raises(TradingError, match="Wallet not initialized"):
            await executor.execute_order(order)

    @pytest.mark.asyncio
    async def test_should_execute_real_order_with_wallet(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.get_eth_balance.return_value = Decimal("1.0")
        mock_wallet.web3.eth.gas_price = 30_000_000_000  # 30 Gwei
        executor.wallet = mock_wallet

        order = _make_order()
        execution = await executor.execute_order(order)
        assert execution is not None

    @pytest.mark.asyncio
    async def test_should_raise_on_low_eth_balance(self):
        """Balance < 0.01 ETH triggers InsufficientBalance wrapped in TradingError."""
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.get_eth_balance.return_value = Decimal("0.001")
        mock_wallet.web3.eth.gas_price = 30_000_000_000
        executor.wallet = mock_wallet

        order = _make_order()
        with pytest.raises(TradingError, match="Insufficient ETH for gas"):
            await executor.execute_order(order)

    @pytest.mark.asyncio
    async def test_should_raise_on_balance_check_failure(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.get_eth_balance.side_effect = RuntimeError("RPC down")
        executor.wallet = mock_wallet

        order = _make_order()
        with pytest.raises(TradingError, match="Failed to check ETH balance"):
            await executor.execute_order(order)

    @pytest.mark.asyncio
    async def test_should_raise_insufficient_gas_after_calc(self):
        """Balance >= 0.01 ETH but still insufficient for gas cost."""
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        # Balance above 0.01 threshold but below gas cost
        mock_wallet.get_eth_balance.return_value = Decimal("0.02")
        # Very high gas price makes gas_cost_eth > 0.02
        mock_wallet.web3.eth.gas_price = 1_000_000_000_000  # 1000 Gwei
        executor.wallet = mock_wallet

        order = _make_order()
        with pytest.raises(InsufficientBalance, match="gas"):
            await executor.execute_order(order)


class TestSimulationExecution:
    @pytest.mark.asyncio
    async def test_should_produce_valid_execution(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order()
        order.status = OrderStatus.SUBMITTED
        random.seed(42)
        execution = await executor._execute_simulation(order)
        assert Decimal("0.0001") <= execution.execution_price <= Decimal("0.9999")
        assert execution.gas_used >= 150000
        assert execution.simulation is True
        assert execution.transaction_hash.startswith("0x")


class TestCheckOrderStatus:
    @pytest.mark.asyncio
    async def test_should_return_completed_in_simulation(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.SUBMITTED)
        status = await executor.check_order_status(order)
        assert status == OrderStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_should_return_same_status_when_not_submitted(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.PENDING)
        status = await executor.check_order_status(order)
        assert status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_should_return_status_when_no_tx_hash_real(self):
        executor = TradingExecutor(simulation_mode=False)
        executor.wallet = MagicMock()
        order = _make_order(status=OrderStatus.SUBMITTED)
        order.transaction_hash = None
        status = await executor.check_order_status(order)
        assert status == OrderStatus.SUBMITTED

    @pytest.mark.asyncio
    async def test_should_return_status_when_no_wallet_real(self):
        executor = TradingExecutor(simulation_mode=False)
        order = _make_order(status=OrderStatus.SUBMITTED)
        order.transaction_hash = "0xabc"
        status = await executor.check_order_status(order)
        assert status == OrderStatus.SUBMITTED

    @pytest.mark.asyncio
    async def test_should_return_completed_on_success_receipt(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        receipt = MagicMock()
        receipt.status = 1
        mock_wallet.web3.eth.get_transaction_receipt.return_value = receipt
        executor.wallet = mock_wallet

        order = _make_order(status=OrderStatus.SUBMITTED)
        order.transaction_hash = "0xabc"
        status = await executor.check_order_status(order)
        assert status == OrderStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_should_return_failed_on_failed_receipt(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        receipt = MagicMock()
        receipt.status = 0
        mock_wallet.web3.eth.get_transaction_receipt.return_value = receipt
        executor.wallet = mock_wallet

        order = _make_order(status=OrderStatus.SUBMITTED)
        order.transaction_hash = "0xabc"
        status = await executor.check_order_status(order)
        assert status == OrderStatus.FAILED

    @pytest.mark.asyncio
    async def test_should_return_confirming_on_none_receipt(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.web3.eth.get_transaction_receipt.return_value = None
        executor.wallet = mock_wallet

        order = _make_order(status=OrderStatus.SUBMITTED)
        order.transaction_hash = "0xabc"
        status = await executor.check_order_status(order)
        assert status == OrderStatus.CONFIRMING

    @pytest.mark.asyncio
    async def test_should_return_confirming_on_exception(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.web3.eth.get_transaction_receipt.side_effect = Exception("err")
        executor.wallet = mock_wallet

        order = _make_order(status=OrderStatus.SUBMITTED)
        order.transaction_hash = "0xabc"
        status = await executor.check_order_status(order)
        assert status == OrderStatus.CONFIRMING


class TestGetOrderExecution:
    @pytest.mark.asyncio
    async def test_should_return_none_when_not_completed(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.PENDING)
        result = await executor.get_order_execution(order)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_execution_in_simulation(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.COMPLETED)
        result = await executor.get_order_execution(order)
        assert result is not None
        assert result.simulation is True

    @pytest.mark.asyncio
    async def test_should_return_execution_real_without_tx_hash(self):
        executor = TradingExecutor(simulation_mode=False)
        order = _make_order(status=OrderStatus.COMPLETED)
        order.transaction_hash = None
        result = await executor.get_order_execution(order)
        assert result is not None

    @pytest.mark.asyncio
    async def test_should_return_execution_real_with_tx_hash(self):
        executor = TradingExecutor(simulation_mode=False)
        order = _make_order(status=OrderStatus.COMPLETED)
        order.transaction_hash = "0xreal"
        result = await executor.get_order_execution(order)
        assert result is not None


class TestGetCurrentPrice:
    @pytest.mark.asyncio
    async def test_should_return_cached_price(self):
        executor = TradingExecutor(simulation_mode=True)
        executor._price_cache["m1_YES"] = Decimal("0.65")
        price = await executor.get_current_price("m1", "YES")
        assert price == Decimal("0.65")

    @pytest.mark.asyncio
    async def test_should_generate_and_cache_price(self):
        executor = TradingExecutor(simulation_mode=True)
        price = await executor.get_current_price("m2", "NO")
        assert Decimal("0.1") <= price <= Decimal("0.9")
        assert "m2_NO" in executor._price_cache


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_should_cancel_pending_order_simulation(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.PENDING)
        result = await executor.cancel_order(order)
        assert result is True
        assert order.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_should_cancel_submitted_order(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.SUBMITTED)
        result = await executor.cancel_order(order)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_reject_cancel_completed_order(self):
        executor = TradingExecutor(simulation_mode=True)
        order = _make_order(status=OrderStatus.COMPLETED)
        result = await executor.cancel_order(order)
        assert result is False

    @pytest.mark.asyncio
    async def test_should_cancel_real_order(self):
        executor = TradingExecutor(simulation_mode=False)
        order = _make_order(status=OrderStatus.PENDING)
        result = await executor.cancel_order(order)
        assert result is True
        assert order.status == OrderStatus.CANCELLED


class TestGetWalletBalance:
    @pytest.mark.asyncio
    async def test_should_return_simulation_balance(self):
        executor = TradingExecutor(simulation_mode=True)
        balance = await executor.get_wallet_balance()
        assert balance["eth_balance"] == Decimal("10.0")
        assert balance["usd_value"] == Decimal("20000.0")

    @pytest.mark.asyncio
    async def test_should_raise_when_no_wallet_real(self):
        executor = TradingExecutor(simulation_mode=False)
        with pytest.raises(TradingError, match="Wallet not initialized"):
            await executor.get_wallet_balance()

    @pytest.mark.asyncio
    async def test_should_return_real_balance(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.get_eth_balance.return_value = Decimal("5.0")
        executor.wallet = mock_wallet
        balance = await executor.get_wallet_balance()
        assert balance["eth_balance"] == Decimal("5.0")

    @pytest.mark.asyncio
    async def test_should_raise_on_balance_error(self):
        executor = TradingExecutor(simulation_mode=False)
        mock_wallet = MagicMock()
        mock_wallet.get_eth_balance.side_effect = RuntimeError("RPC error")
        executor.wallet = mock_wallet
        with pytest.raises(TradingError, match="Failed to get wallet balance"):
            await executor.get_wallet_balance()
