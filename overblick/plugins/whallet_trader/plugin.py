"""
Whallet Trader Plugin — Trade execution for Polymarket.

Executes trades on Polymarket based on opportunities detected by the
polymarket_monitor plugin. Integrates with the simplified Whallet library
for Ethereum transaction signing and sending.

Features:
- Trade execution for Polymarket YES/NO tokens
- Risk-managed position sizing (Kelly criterion with caps)
- Portfolio tracking and P&L calculation
- Stop-loss and take-profit management
- Simulation mode for testing (no real transactions)
- Gas optimization and transaction batching

Security:
- Private keys stored in encrypted secrets
- Simulation mode enabled by default
- Maximum position size limits (configurable)
- Transaction confirmation waiting with timeouts
- Audit logging for all trade attempts
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content

from .models import (
    PortfolioPosition,
    RiskParameters,
    TradeExecution,
    TradeOrder,
    TradeSignal,
)
from .risk_manager import RiskManager
from .trading_executor import TradingError, TradingExecutor

logger = logging.getLogger(__name__)

# Default configuration
_DEFAULT_CHECK_INTERVAL_SECONDS = 60  # Check for new signals every minute
_DEFAULT_SIMULATION_MODE = True
_DEFAULT_MAX_POSITION_SIZE_PERCENT = 5.0
_DEFAULT_DAILY_LOSS_LIMIT_PERCENT = 2.0
_DEFAULT_GAS_PRICE_MULTIPLIER = 1.1  # 10% above market gas price


class WhalletTraderPlugin(PluginBase):
    """
    Polymarket trade execution plugin.

    Receives trading signals from polymarket_monitor, applies risk management,
    and executes trades via the Whallet library. Maintains portfolio tracking
    and performance monitoring.
    """

    # Required capabilities for this plugin
    REQUIRED_CAPABILITIES = [
        "network_outbound",  # Ethereum RPC calls
        "filesystem_write",  # Portfolio and trade history
        "secrets_access",  # Ethereum private keys
        "blockchain_transact",  # Send Ethereum transactions
    ]

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._check_interval_seconds = _DEFAULT_CHECK_INTERVAL_SECONDS
        self._last_check_time: float = 0
        self._state_file: Path | None = None

        # Core components
        self._trading_executor: TradingExecutor | None = None
        self._risk_manager: RiskManager | None = None

        # Plugin state
        self._pending_signals: list[TradeSignal] = []
        self._active_orders: list[TradeOrder] = []
        self._portfolio_positions: list[PortfolioPosition] = []
        self._trade_history: list[TradeExecution] = []

        # Configuration
        self._config = {
            "simulation_mode": _DEFAULT_SIMULATION_MODE,
            "max_position_size_percent": _DEFAULT_MAX_POSITION_SIZE_PERCENT,
            "daily_loss_limit_percent": _DEFAULT_DAILY_LOSS_LIMIT_PERCENT,
            "gas_price_multiplier": _DEFAULT_GAS_PRICE_MULTIPLIER,
            "rpc_url": None,  # Will be loaded from secrets
            "private_key": None,  # Will be loaded from secrets
        }

    async def setup(self) -> None:
        """Initialize plugin: load configuration, set up trading executor, load portfolio."""
        # Load configuration from identity
        raw = self.ctx.identity.raw_config if self.ctx.identity else {}
        plugin_config = raw.get("whallet_trader", {})

        # Apply configuration
        self._config.update(
            {
                "simulation_mode": plugin_config.get("simulation_mode", _DEFAULT_SIMULATION_MODE),
                "max_position_size_percent": plugin_config.get(
                    "max_position_size_percent", _DEFAULT_MAX_POSITION_SIZE_PERCENT
                ),
                "daily_loss_limit_percent": plugin_config.get(
                    "daily_loss_limit_percent", _DEFAULT_DAILY_LOSS_LIMIT_PERCENT
                ),
                "gas_price_multiplier": plugin_config.get(
                    "gas_price_multiplier", _DEFAULT_GAS_PRICE_MULTIPLIER
                ),
            }
        )

        # Load secrets (Ethereum credentials)
        self._config["rpc_url"] = self.ctx.get_secret("ethereum_rpc_url")
        self._config["private_key"] = self.ctx.get_secret("ethereum_private_key")

        # Set check interval
        check_interval_seconds = plugin_config.get(
            "check_interval_seconds", _DEFAULT_CHECK_INTERVAL_SECONDS
        )
        self._check_interval_seconds = check_interval_seconds

        # Initialize data directory
        self.ctx.data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.ctx.data_dir / "whallet_trader_state.json"

        # Load portfolio and trade history
        self._load_state()

        # Initialize trading executor
        self._trading_executor = TradingExecutor(
            rpc_url=self._config["rpc_url"],
            private_key=self._config["private_key"],
            simulation_mode=self._config["simulation_mode"],
        )

        # Initialize risk manager
        self._risk_manager = RiskManager(
            max_position_size_percent=self._config["max_position_size_percent"],
            daily_loss_limit_percent=self._config["daily_loss_limit_percent"],
            portfolio_positions=self._portfolio_positions,
            trade_history=self._trade_history,
        )

        logger.info(
            "WhalletTraderPlugin setup for '%s' (simulation: %s, max position: %.1f%%, RPC: %s)",
            self.ctx.identity_name,
            self._config["simulation_mode"],
            self._config["max_position_size_percent"],
            "configured" if self._config["rpc_url"] else "missing",
        )

    async def tick(self) -> None:
        """
        Main tick: check for new trading signals and process pending orders.

        Performs:
        1. Interval check (default 60 seconds)
        2. Process any pending trading signals
        3. Check active orders for completion
        4. Update portfolio positions with current prices
        5. Apply risk management (stop-loss, take-profit)
        6. Persist state
        """
        now = time.time()

        # Guard: check interval
        if now - self._last_check_time < self._check_interval_seconds:
            return

        self._last_check_time = now

        try:
            # Process pending signals
            await self._process_pending_signals()

            # Check active orders
            await self._check_active_orders()

            # Update portfolio with current prices
            await self._update_portfolio_prices()

            # Apply risk management
            await self._apply_risk_management()

            # Persist state
            self._save_state()

        except Exception as e:
            logger.error("WhalletTrader tick failed: %s", e, exc_info=True)
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "whallet_trader_tick_failed",
                    category="trading",
                    plugin="whallet_trader",
                    success=False,
                    error=str(e),
                )

    async def _process_pending_signals(self) -> None:
        """Process any pending trading signals."""
        if not self._pending_signals:
            return

        signals_to_process = self._pending_signals.copy()
        self._pending_signals.clear()

        for signal in signals_to_process:
            try:
                await self._process_trading_signal(signal)
            except Exception as e:
                logger.error("Failed to process signal %s: %s", signal.signal_id, e)
                # Re-queue failed signals for retry
                signal.retry_count += 1
                if signal.retry_count < 3:
                    self._pending_signals.append(signal)

    async def _process_trading_signal(self, signal: TradeSignal) -> None:
        """
        Process a single trading signal.

        Steps:
        1. Validate signal and market data
        2. Apply risk management checks
        3. Calculate position size
        4. Create and execute trade order
        5. Update portfolio and history
        """
        logger.info(
            "WhalletTrader: processing signal %s — %s %s (edge: %.1f%%, confidence: %.0f/100)",
            signal.signal_id,
            signal.action,
            signal.market_question[:50],
            signal.probability_edge * 100,
            signal.confidence_score,
        )

        # Validate we have trading executor
        if not self._trading_executor:
            logger.error("Trading executor not initialized")
            return

        # Validate we have risk manager
        if not self._risk_manager:
            logger.error("Risk manager not initialized")
            return

        # Apply risk management checks
        risk_check = await self._risk_manager.check_signal_risk(signal)
        if not risk_check.approved:
            logger.warning(
                "Signal %s rejected by risk manager: %s",
                signal.signal_id,
                risk_check.reason,
            )

            # Log rejection in audit log
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "trade_signal_rejected",
                    category="risk",
                    plugin="whallet_trader",
                    details={
                        "signal_id": signal.signal_id,
                        "market": signal.market_question[:100],
                        "action": signal.action,
                        "reason": risk_check.reason,
                        "risk_level": risk_check.risk_level.value,
                    },
                )
            return

        # Calculate position size based on risk parameters
        position_size = await self._risk_manager.calculate_position_size(signal, risk_check)

        # Create trade order
        order = TradeOrder.from_signal(signal, position_size)

        # Execute trade
        try:
            execution = await self._trading_executor.execute_order(order)

            # Update portfolio
            self._update_portfolio_with_execution(execution)

            # Add to trade history
            self._trade_history.append(execution)

            # Trim history
            if len(self._trade_history) > 1000:
                self._trade_history = self._trade_history[-1000:]

            logger.info(
                "WhalletTrader: executed trade %s — %s %s @ $%.4f (size: $%.2f)",
                execution.execution_id,
                execution.action,
                execution.market_question[:50],
                execution.execution_price,
                execution.position_size_usd,
            )

            # Audit log
            if self.ctx.audit_log:
                self._log_trade_execution(execution, risk_check)

        except TradingError as e:
            logger.error("Trade execution failed for signal %s: %s", signal.signal_id, e)

            # Update order status
            order.status = "failed"
            order.error_message = str(e)
            self._active_orders.append(order)

            # Audit log
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "trade_execution_failed",
                    category="trading",
                    plugin="whallet_trader",
                    details={
                        "signal_id": signal.signal_id,
                        "market": signal.market_question[:100],
                        "action": signal.action,
                        "error": str(e),
                    },
                    success=False,
                )

    async def _check_active_orders(self) -> None:
        """Check status of active orders and update portfolio."""
        if not self._active_orders:
            return

        if not self._trading_executor:
            return

        completed_orders = []
        for order in self._active_orders:
            if order.status in ["completed", "failed", "cancelled"]:
                completed_orders.append(order)
                continue

            try:
                # Check order status on-chain
                status = await self._trading_executor.check_order_status(order)
                order.status = status

                if status == "completed":
                    # Get execution details
                    execution = await self._trading_executor.get_order_execution(order)
                    if execution:
                        self._update_portfolio_with_execution(execution)
                        self._trade_history.append(execution)
                        completed_orders.append(order)

                        logger.debug(
                            "Order %s completed — %s",
                            order.order_id,
                            order.market_question[:50],
                        )

                elif status == "failed":
                    logger.warning("Order %s failed", order.order_id)
                    completed_orders.append(order)

            except Exception as e:
                logger.error("Failed to check order %s: %s", order.order_id, e)

        # Remove completed orders
        for order in completed_orders:
            if order in self._active_orders:
                self._active_orders.remove(order)

    async def _update_portfolio_prices(self) -> None:
        """Update portfolio positions with current market prices."""
        if not self._portfolio_positions:
            return

        if not self._trading_executor:
            return

        for position in self._portfolio_positions:
            try:
                # Get current price for this market/outcome
                current_price = await self._trading_executor.get_current_price(
                    position.market_id,
                    position.outcome,
                )

                # Update position
                position.current_price = current_price
                position.current_value = position.quantity * current_price
                position.unrealized_pnl = position.current_value - position.invested_amount

                if position.invested_amount > 0:
                    position.unrealized_pnl_percent = (
                        position.unrealized_pnl / position.invested_amount * 100
                    )

                position.last_updated = datetime.now()

            except Exception as e:
                logger.debug("Failed to update price for position %s: %s", position.position_id, e)

    async def _apply_risk_management(self) -> None:
        """Apply risk management rules (stop-loss, take-profit, daily limits)."""
        if not self._risk_manager:
            return

        # Check daily loss limit
        daily_pnl = self._risk_manager.calculate_daily_pnl()
        if daily_pnl < -self._config["daily_loss_limit_percent"]:
            logger.warning(
                "Daily loss limit breached: %.2f%% (limit: %.2f%%)",
                daily_pnl,
                self._config["daily_loss_limit_percent"],
            )

            # Cancel all pending orders
            for order in self._active_orders:
                if order.status in ["pending", "submitted"]:
                    try:
                        await self._trading_executor.cancel_order(order)
                        order.status = "cancelled"
                        logger.info("Cancelled order %s due to daily loss limit", order.order_id)
                    except Exception as e:
                        logger.error("Failed to cancel order %s: %s", order.order_id, e)

            # Log breach
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "daily_loss_limit_breached",
                    category="risk",
                    plugin="whallet_trader",
                    details={
                        "daily_pnl_percent": daily_pnl,
                        "limit_percent": self._config["daily_loss_limit_percent"],
                        "active_orders_cancelled": len(
                            [o for o in self._active_orders if o.status == "cancelled"]
                        ),
                    },
                )

        # Check stop-loss and take-profit for each position
        for position in self._portfolio_positions:
            # Skip if no stop-loss/take-profit set
            if position.stop_loss_price is None and position.take_profit_price is None:
                continue

            current_price = position.current_price

            # Check stop-loss
            if position.stop_loss_price is not None and current_price <= position.stop_loss_price:
                logger.info(
                    "Position %s hit stop-loss at $%.4f (current: $%.4f)",
                    position.position_id,
                    position.stop_loss_price,
                    current_price,
                )

                # Create close signal
                # In a full implementation, this would create a signal to close the position
                pass

            # Check take-profit
            if (
                position.take_profit_price is not None
                and current_price >= position.take_profit_price
            ):
                logger.info(
                    "Position %s hit take-profit at $%.4f (current: $%.4f)",
                    position.position_id,
                    position.take_profit_price,
                    current_price,
                )

                # Create close signal
                pass

    def _update_portfolio_with_execution(self, execution: TradeExecution) -> None:
        """Update portfolio positions based on trade execution."""
        # Find existing position for this market/outcome
        existing_position = None
        for position in self._portfolio_positions:
            if position.market_id == execution.market_id and position.outcome == execution.outcome:
                existing_position = position
                break

        if existing_position:
            # Update existing position
            if execution.action.startswith("BUY_"):
                # Calculate new average price
                total_invested = existing_position.invested_amount + execution.position_size_usd
                total_quantity = existing_position.quantity + execution.quantity

                if total_quantity > 0:
                    existing_position.average_price = total_invested / total_quantity

                existing_position.quantity = total_quantity
                existing_position.invested_amount = total_invested

            elif execution.action.startswith("SELL_"):
                # Reduce position
                existing_position.quantity -= execution.quantity
                if existing_position.quantity < 0.0001:  # Near zero
                    existing_position.quantity = 0
                    existing_position.invested_amount = 0
                    existing_position.average_price = 0
                else:
                    # Proportionally reduce invested amount
                    reduction_ratio = execution.quantity / (
                        existing_position.quantity + execution.quantity
                    )
                    existing_position.invested_amount *= 1 - reduction_ratio

            existing_position.last_updated = datetime.now()

            # Remove position if quantity is zero
            if existing_position.quantity == 0:
                self._portfolio_positions.remove(existing_position)

        else:
            # Create new position (only for BUY actions)
            if execution.action.startswith("BUY_"):
                new_position = PortfolioPosition(
                    position_id=f"pos_{execution.execution_id}",
                    market_id=execution.market_id,
                    market_question=execution.market_question,
                    outcome=execution.outcome,
                    quantity=execution.quantity,
                    average_price=execution.execution_price,
                    current_price=execution.execution_price,
                    invested_amount=execution.position_size_usd,
                    current_value=execution.position_size_usd,
                    unrealized_pnl=0,
                    unrealized_pnl_percent=0,
                    first_bought=execution.executed_at,
                    last_updated=execution.executed_at,
                )
                self._portfolio_positions.append(new_position)

    def _log_trade_execution(self, execution: TradeExecution, risk_check: Any) -> None:
        """Log trade execution to audit log."""
        if not self.ctx.audit_log:
            return

        self.ctx.audit_log.log(
            "trade_executed",
            category="trading",
            plugin="whallet_trader",
            details={
                "execution_id": execution.execution_id,
                "market": execution.market_question[:100],
                "action": execution.action,
                "outcome": execution.outcome,
                "quantity": execution.quantity,
                "price": execution.execution_price,
                "size_usd": execution.position_size_usd,
                "gas_used": execution.gas_used,
                "gas_price_gwei": execution.gas_price_gwei,
                "simulation": execution.simulation,
                "risk_level": risk_check.risk_level.value if risk_check else "unknown",
            },
            success=True,
        )

    def _load_state(self) -> None:
        """Load plugin state from disk."""
        if not self._state_file or not self._state_file.exists():
            return

        try:
            data = json.loads(self._state_file.read_text())

            # Load portfolio positions
            self._portfolio_positions = [
                PortfolioPosition(**pos) for pos in data.get("portfolio_positions", [])
            ]

            # Load trade history
            self._trade_history = [TradeExecution(**exec) for exec in data.get("trade_history", [])]

            # Load active orders
            self._active_orders = [TradeOrder(**order) for order in data.get("active_orders", [])]

            self._last_check_time = data.get("last_check_time", 0)

            logger.debug(
                "WhalletTrader: loaded state — %d positions, %d trades, %d orders",
                len(self._portfolio_positions),
                len(self._trade_history),
                len(self._active_orders),
            )

        except (json.JSONDecodeError, KeyError, ValidationError) as e:
            logger.warning("WhalletTrader: failed to load state: %s", e)
            self._portfolio_positions = []
            self._trade_history = []
            self._active_orders = []

    def _save_state(self) -> None:
        """Persist plugin state to disk."""
        if not self._state_file:
            return

        try:
            data = {
                "portfolio_positions": [pos.model_dump() for pos in self._portfolio_positions],
                "trade_history": [
                    exec.model_dump() for exec in self._trade_history[-500:]
                ],  # Last 500 trades
                "active_orders": [order.model_dump() for order in self._active_orders],
                "last_check_time": self._last_check_time,
                "saved_at": datetime.now().isoformat(),
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error("WhalletTrader: failed to save state: %s", e, exc_info=True)

    # Public API for other plugins (e.g., polymarket_monitor)

    async def submit_trading_signal(self, signal: TradeSignal) -> bool:
        """
        Submit a trading signal for execution.

        Called by polymarket_monitor when it detects a high-confidence opportunity.

        Args:
            signal: Trading signal to execute

        Returns:
            True if signal accepted, False if rejected
        """
        # Basic validation
        if not signal.signal_id or not signal.market_id:
            logger.warning("Invalid trading signal received")
            return False

        # Check if we're in simulation mode and signal allows simulation
        if self._config["simulation_mode"] and not signal.allow_simulation:
            logger.debug(
                "Signal %s requires real trading, but we're in simulation mode", signal.signal_id
            )
            return False

        # Add to pending signals queue
        self._pending_signals.append(signal)

        logger.info(
            "WhalletTrader: accepted signal %s — %s %s",
            signal.signal_id,
            signal.action,
            signal.market_question[:50],
        )

        return True

    async def get_portfolio_summary(self) -> dict[str, Any]:
        """Get current portfolio summary."""
        total_invested = sum(p.invested_amount for p in self._portfolio_positions)
        total_value = sum(p.current_value for p in self._portfolio_positions)
        total_pnl = total_value - total_invested
        total_pnl_percent = (total_pnl / total_invested * 100) if total_invested > 0 else 0

        daily_pnl = 0
        if self._risk_manager:
            daily_pnl = self._risk_manager.calculate_daily_pnl()

        return {
            "positions_count": len(self._portfolio_positions),
            "total_invested_usd": total_invested,
            "total_value_usd": total_value,
            "total_pnl_usd": total_pnl,
            "total_pnl_percent": total_pnl_percent,
            "daily_pnl_percent": daily_pnl,
            "simulation_mode": self._config["simulation_mode"],
            "risk_limits": {
                "max_position_size_percent": self._config["max_position_size_percent"],
                "daily_loss_limit_percent": self._config["daily_loss_limit_percent"],
            },
        }
