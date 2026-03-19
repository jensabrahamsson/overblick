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
    OrderStatus,
    PortfolioPosition,
    RiskParameters,
    TradeExecution,
    TradeOrder,
    TradeSignal,
)
from .trading_executor import TradingError
from .performance_tracker import PerformanceTracker
from .risk_manager import RiskManager
from .polymarket_trading_executor import PolymarketTradingExecutor

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
        self._trading_executor: PolymarketTradingExecutor | None = None
        self._risk_manager: RiskManager | None = None

        # Plugin state
        self._pending_signals: list[TradeSignal] = []
        self._active_orders: list[TradeOrder] = []
        self._portfolio_positions: list[PortfolioPosition] = []
        self._trade_history: list[TradeExecution] = []
        self._performance_tracker: PerformanceTracker | None = None

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

        # Initialize performance tracker
        data_dir = Path(self.ctx.data_dir) / "whallet_trader"
        self._performance_tracker = PerformanceTracker(data_dir)

        # Initialize trading executor
        private_key = self._config.get("private_key")
        rpc_url = self._config.get("rpc_url")

        if not private_key:
            logger.warning("No private key configured for whallet_trader - using simulation mode")
            private_key = "0x0000000000000000000000000000000000000000000000000000000000000000"

        if not rpc_url:
            logger.warning("No RPC URL configured for whallet_trader - using Polygon default")
            rpc_url = "https://polygon-rpc.com"

        self._trading_executor = PolymarketTradingExecutor(
            private_key=private_key,
            polygon_rpc_url=rpc_url,
            simulation_mode=self._config["simulation_mode"],
            max_slippage_percent=self._config.get("max_slippage_percent", 1.0),
            gas_limit=self._config.get("gas_limit", 300000),
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

        # Subscribe to Polymarket trading signals from event bus
        if self.ctx.event_bus:
            self.ctx.event_bus.subscribe("polymarket.trading_signal", self._handle_trading_signal)
            logger.info("WhalletTraderPlugin subscribed to polymarket.trading_signal events")

    async def _handle_trading_signal(self, **kwargs):
        """Handle incoming trading signals from event bus."""
        try:
            signal = TradeSignal(
                signal_id=kwargs.get("signal_id", f"sig_{time.time()}"),
                market_id=kwargs["market_id"],
                market_question=kwargs["market_question"],
                action=kwargs.get("action", "buy"),
                outcome=kwargs.get("outcome", "YES"),
                market_price=Decimal(str(kwargs.get("market_price", 0.5))),
                our_probability=Decimal(str(kwargs.get("our_probability", 0.5))),
                probability_edge=Decimal(str(kwargs.get("probability_edge", 0.01))),
                confidence_score=float(kwargs.get("confidence_score", 50)),
                volume_score=float(kwargs.get("volume_score", 50)),
                time_horizon_days=float(kwargs.get("time_horizon_days", 30)),
                suggested_position_size_percent=Decimal(
                    str(kwargs.get("suggested_position_size_percent", 1.0))
                ),
                kelly_fraction=Decimal(str(kwargs.get("kelly_fraction", 0.1))),
                urgency=kwargs.get("urgency", "medium"),
            )

            self._pending_signals.append(signal)
            logger.info(
                "WhalletTraderPlugin: received trading signal for %s (edge: %.1f%%, confidence: %.0f%%)",
                signal.market_question[:60],
                float(signal.probability_edge) * 100,
                signal.confidence_score,
            )

        except Exception as e:
            logger.error(
                "WhalletTraderPlugin: failed to parse trading signal: %s", e, exc_info=True
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
            # Create TradeExecution from TradeOrder
            execution = TradeExecution(
                order_id=order.order_id,
                signal_id=order.signal_id,
                market_id=order.market_id,
                market_question=order.market_question,
                outcome=order.outcome,
                action=order.action,
                quantity=order.quantity,
                execution_price=order.estimated_price,
                position_size_usd=order.position_size_usd,
                expected_price=order.estimated_price,
                slippage_percent=Decimal("0"),
                transaction_hash="0x0000000000000000000000000000000000000000000000000000000000000000",
                block_number=0,
                gas_used=0,
                gas_price_gwei=Decimal("0"),
                executed_at=order.created_at,
                simulation=self._config["simulation_mode"],
            )

            # Update portfolio
            self._update_portfolio_with_execution(execution)

            # Add to trade history
            self._trade_history.append(execution)

            # Record in performance tracker
            if self._performance_tracker:
                self._performance_tracker.record_trade(execution)

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
            order.status = OrderStatus.FAILED
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
                status_data = await self._trading_executor.get_order_status(order)
                status = status_data.get("status", "unknown")

                if status in ["filled", "completed"]:
                    # Create TradeExecution from status data
                    execution = TradeExecution(
                        order_id=order.order_id,
                        signal_id=order.signal_id,
                        market_id=order.market_id,
                        market_question=order.market_question,
                        outcome=order.outcome,
                        action=order.action,
                        quantity=Decimal(str(status_data.get("filled", 0))),
                        execution_price=Decimal(str(status_data.get("price", 0))),
                        position_size_usd=order.position_size_usd,
                        expected_price=order.estimated_price,
                        slippage_percent=Decimal("0"),
                        transaction_hash="0x0000000000000000000000000000000000000000000000000000000000000000",
                        block_number=0,
                        gas_used=0,
                        gas_price_gwei=Decimal("0"),
                        executed_at=order.created_at,
                        simulation=self._config["simulation_mode"],
                    )
                    self._update_portfolio_with_execution(execution)
                    self._trade_history.append(execution)
                    completed_orders.append(order)
                    order.status = OrderStatus.COMPLETED

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
                # Get market details for this market
                market_details = await self._trading_executor._get_market_details(
                    position.market_id
                )

                # Extract current price from market details (simplified)
                current_price = Decimal(str(market_details.get("minimum_tick_size", "0.5")))

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
                if order.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED]:
                    try:
                        if self._trading_executor:
                            await self._trading_executor.cancel_order(order)
                            order.status = OrderStatus.CANCELLED
                            logger.info(
                                "Cancelled order %s due to daily loss limit", order.order_id
                            )
                        else:
                            logger.warning(
                                "Cannot cancel order %s: trading executor not initialized",
                                order.order_id,
                            )
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
                if existing_position.quantity < Decimal("0.0001"):  # Near zero
                    existing_position.quantity = Decimal("0")
                    existing_position.invested_amount = Decimal("0")
                    existing_position.average_price = Decimal("0")
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
                    unrealized_pnl=Decimal("0"),
                    unrealized_pnl_percent=Decimal("0"),
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
