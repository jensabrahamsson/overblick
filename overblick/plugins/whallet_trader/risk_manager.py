"""
Risk Manager — Position sizing and risk controls for Polymarket trading.

Implements risk management rules for the Whallet Trader plugin:
- Position sizing (Kelly criterion with constraints)
- Daily loss limits
- Portfolio concentration limits
- Signal validation and filtering
- Stop-loss and take-profit calculation
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .models import (
    PortfolioPosition,
    RiskCheckResult,
    RiskLevel,
    RiskParameters,
    TradeExecution,
    TradeSignal,
)

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manages trading risk for the Whallet Trader plugin.

    Applies risk management rules to:
    1. Validate trading signals against risk parameters
    2. Calculate appropriate position sizes
    3. Enforce daily and portfolio limits
    4. Suggest stop-loss and take-profit levels
    """

    def __init__(
        self,
        max_position_size_percent: Decimal = Decimal("5.0"),
        daily_loss_limit_percent: Decimal = Decimal("2.0"),
        portfolio_positions: list[PortfolioPosition] | None = None,
        trade_history: list[TradeExecution] | None = None,
    ):
        """
        Initialize risk manager.

        Args:
            max_position_size_percent: Maximum position size as % of portfolio
            daily_loss_limit_percent: Maximum daily loss as % of portfolio
            portfolio_positions: Current portfolio positions
            trade_history: Historical trade executions
        """
        self.max_position_size_percent = max_position_size_percent
        self.daily_loss_limit_percent = daily_loss_limit_percent

        self.portfolio_positions = portfolio_positions or []
        self.trade_history = trade_history or []

        # Default risk parameters
        self.risk_params = RiskParameters(
            max_position_size_percent=max_position_size_percent,
            daily_loss_limit_percent=daily_loss_limit_percent,
        )

        # Cache for performance calculations
        self._daily_pnl_cache: Decimal | None = None
        self._cache_time: datetime | None = None

    async def check_signal_risk(self, signal: TradeSignal) -> RiskCheckResult:
        """
        Check if a trading signal passes risk management rules.

        Args:
            signal: Trading signal to evaluate

        Returns:
            RiskCheckResult indicating approval status and constraints
        """
        # Start with approval
        approved = True
        reason = None
        risk_level = signal.risk_level
        risk_score = 0.0
        warnings = []

        # 1. Check minimum edge requirement
        if signal.probability_edge < self.risk_params.min_probability_edge:
            approved = False
            reason = (
                f"Probability edge too small: {signal.probability_edge:.1%} "
                f"(minimum: {self.risk_params.min_probability_edge:.1%})"
            )

        # 2. Check minimum confidence
        elif signal.confidence_score < self.risk_params.min_confidence_score:
            approved = False
            reason = (
                f"Confidence score too low: {signal.confidence_score:.0f}/100 "
                f"(minimum: {self.risk_params.min_confidence_score:.0f})"
            )

        # 3. Check daily loss limit (if we have trades today)
        daily_pnl = self.calculate_daily_pnl()
        if daily_pnl < -self.daily_loss_limit_percent:
            approved = False
            reason = (
                f"Daily loss limit breached: {daily_pnl:.2f}% "
                f"(limit: {self.daily_loss_limit_percent:.2f}%)"
            )

        # 4. Check if signal is expired
        elif signal.expires_at and datetime.now() > signal.expires_at:
            approved = False
            reason = f"Signal expired at {signal.expires_at}"

        # 5. Check portfolio concentration
        if approved:
            portfolio_exposure = self._calculate_portfolio_exposure()
            if portfolio_exposure > self.risk_params.max_portfolio_exposure_percent:
                warnings.append(
                    f"Portfolio exposure high: {portfolio_exposure:.1f}% "
                    f"(limit: {self.risk_params.max_portfolio_exposure_percent:.1f}%)"
                )

                # Still approve, but with warning

        # 6. Check maximum trades per day
        if approved:
            trades_today = self._count_trades_today()
            if trades_today >= self.risk_params.max_trades_per_day:
                approved = False
                reason = (
                    f"Maximum trades per day reached: {trades_today} "
                    f"(limit: {self.risk_params.max_trades_per_day})"
                )

        # Calculate risk score (0-100, higher = riskier)
        if approved:
            risk_score = self._calculate_risk_score(signal)

            # Adjust risk level based on score
            if risk_score < 30:
                risk_level = RiskLevel.LOW
            elif risk_score < 60:
                risk_level = RiskLevel.MEDIUM
            elif risk_score < 80:
                risk_level = RiskLevel.HIGH
            else:
                risk_level = RiskLevel.EXTREME
                warnings.append("Extreme risk level - trade with caution")

        # Calculate position size constraints if approved
        max_position_size_usd = None
        recommended_position_size_usd = None
        required_stop_loss = None
        required_take_profit = None

        if approved:
            # Calculate maximum position size based on portfolio
            max_position_size_usd = self._calculate_max_position_size(signal)

            # Calculate recommended position size (using Kelly with constraints)
            recommended_position_size_usd = await self.calculate_position_size(
                signal,
                RiskCheckResult(
                    approved=True,
                    risk_level=risk_level,
                    risk_score=risk_score,
                    max_position_size_usd=max_position_size_usd,
                ),
            )

            # Calculate stop-loss and take-profit levels
            required_stop_loss, required_take_profit = self._calculate_risk_levels(
                signal, risk_level
            )

        return RiskCheckResult(
            approved=approved,
            reason=reason,
            risk_level=risk_level,
            risk_score=risk_score,
            max_position_size_usd=max_position_size_usd,
            recommended_position_size_usd=recommended_position_size_usd,
            required_stop_loss=required_stop_loss,
            required_take_profit=required_take_profit,
            warnings=warnings,
        )

    async def calculate_position_size(
        self, signal: TradeSignal, risk_check: RiskCheckResult
    ) -> Decimal:
        """
        Calculate appropriate position size for a trading signal.

        Uses Kelly criterion with constraints:
        1. Half-Kelly for safety (use 50% of Kelly fraction)
        2. Maximum position size constraint
        3. Portfolio concentration limits

        Args:
            signal: Trading signal
            risk_check: Risk check result with constraints

        Returns:
            Position size in USD
        """
        if not risk_check.approved:
            return Decimal("0")

        # Get portfolio value (simplified - in real implementation would track total portfolio)
        portfolio_value = self._estimate_portfolio_value()

        # Calculate Kelly position size
        kelly_position_size = self._calculate_kelly_position_size(signal, portfolio_value)

        # Apply half-Kelly for safety
        half_kelly_size = kelly_position_size * Decimal("0.5")

        # Apply maximum position size constraint
        if risk_check.max_position_size_usd:
            max_size = risk_check.max_position_size_usd
        else:
            # Default to percentage of portfolio
            max_size = portfolio_value * (self.max_position_size_percent / Decimal("100"))

        # Choose the smaller of half-Kelly and maximum size
        position_size = min(half_kelly_size, max_size)

        # Ensure minimum position size (e.g., $10 to make gas costs worthwhile)
        min_position_size = Decimal("10.0")  # $10 minimum
        if position_size < min_position_size:
            logger.debug(
                "Position size $%.2f below minimum $%.2f, using minimum",
                position_size,
                min_position_size,
            )
            position_size = min_position_size

        # Cap at portfolio value (shouldn't happen with percentage constraints, but safe)
        position_size = min(position_size, portfolio_value * Decimal("0.9"))  # Leave 10% for gas

        logger.debug(
            "Calculated position size: $%.2f (Kelly: $%.2f, Max: $%.2f, Portfolio: $%.2f)",
            position_size,
            kelly_position_size,
            max_size,
            portfolio_value,
        )

        return position_size

    def calculate_daily_pnl(self) -> Decimal:
        """
        Calculate daily P&L as percentage of portfolio.

        Returns:
            Daily P&L percentage (can be negative)
        """
        # Check cache (valid for 5 minutes)
        now = datetime.now()
        if (
            self._daily_pnl_cache is not None
            and self._cache_time
            and (now - self._cache_time) < timedelta(minutes=5)
        ):
            return self._daily_pnl_cache

        # Calculate P&L for trades today
        today = datetime.now().date()
        today_trades = [
            trade
            for trade in self.trade_history
            if trade.executed_at.date() == today and not trade.simulation
        ]

        if not today_trades:
            self._daily_pnl_cache = Decimal("0")
            self._cache_time = now
            return self._daily_pnl_cache

        # Calculate total P&L in USD
        total_pnl_usd = Decimal("0")
        for trade in today_trades:
            # Simplified P&L calculation
            # In real implementation, would track cost basis and realized P&L
            # For now, assume all trades are unrealized
            pass

        # Estimate portfolio value
        portfolio_value = self._estimate_portfolio_value()

        if portfolio_value > 0:
            daily_pnl_percent = (total_pnl_usd / portfolio_value) * Decimal("100")
        else:
            daily_pnl_percent = Decimal("0")

        self._daily_pnl_cache = daily_pnl_percent
        self._cache_time = now

        return daily_pnl_percent

    def _calculate_kelly_position_size(
        self, signal: TradeSignal, portfolio_value: Decimal
    ) -> Decimal:
        """
        Calculate Kelly criterion position size.

        Formula: f* = (p*b - q) / b
        where:
          p = win probability
          q = loss probability (1 - p)
          b = net odds (win payout - 1)

        For Polymarket:
          - Win probability = our estimated probability
          - Win payout = 1 / market_price (for YES) or 1 / (1 - market_price) (for NO)
        """
        if signal.action.value.startswith("BUY_"):
            if signal.outcome == "YES":
                win_probability = signal.our_probability
                win_payout = (
                    Decimal("1") / signal.market_price if signal.market_price > 0 else Decimal("1")
                )
            else:  # NO
                win_probability = Decimal("1") - signal.our_probability
                win_payout = (
                    Decimal("1") / (Decimal("1") - signal.market_price)
                    if signal.market_price < Decimal("1")
                    else Decimal("1")
                )
        else:
            # For SELL actions, Kelly calculation is different
            # Simplified: use half of buy Kelly
            return portfolio_value * signal.kelly_fraction * Decimal("0.5")

        # Calculate Kelly fraction
        if win_payout <= Decimal("1"):
            kelly_fraction = Decimal("0")
        else:
            loss_probability = Decimal("1") - win_probability
            b = win_payout - Decimal("1")
            kelly_fraction = (win_probability * b - loss_probability) / b

        # Constrain to 0-1 range
        kelly_fraction = max(Decimal("0"), min(kelly_fraction, Decimal("1")))

        # Apply to portfolio
        return portfolio_value * kelly_fraction

    def _calculate_max_position_size(self, signal: TradeSignal) -> Decimal:
        """Calculate maximum allowed position size for a signal."""
        portfolio_value = self._estimate_portfolio_value()

        # Base maximum: percentage of portfolio
        max_size_percent = self.max_position_size_percent
        max_size = portfolio_value * (max_size_percent / Decimal("100"))

        # Adjust for risk level
        if signal.risk_level == RiskLevel.LOW:
            # Allow full position for low risk
            pass
        elif signal.risk_level == RiskLevel.MEDIUM:
            # Reduce by 25% for medium risk
            max_size *= Decimal("0.75")
        elif signal.risk_level == RiskLevel.HIGH:
            # Reduce by 50% for high risk
            max_size *= Decimal("0.5")
        elif signal.risk_level == RiskLevel.EXTREME:
            # Reduce by 75% for extreme risk
            max_size *= Decimal("0.25")

        return max_size

    def _calculate_risk_levels(
        self, signal: TradeSignal, risk_level: RiskLevel
    ) -> tuple[Decimal | None, Decimal | None]:
        """
        Calculate stop-loss and take-profit levels for a signal.

        Args:
            signal: Trading signal
            risk_level: Risk level of the trade

        Returns:
            Tuple of (stop_loss_price, take_profit_price) or (None, None)
        """
        if signal.market_price <= Decimal("0") or signal.market_price >= Decimal("1"):
            return None, None

        # Base stop-loss and take-profit percentages
        if risk_level == RiskLevel.LOW:
            stop_loss_pct = Decimal("0.10")  # 10% stop-loss
            take_profit_pct = Decimal("0.20")  # 20% take-profit
        elif risk_level == RiskLevel.MEDIUM:
            stop_loss_pct = Decimal("0.15")  # 15% stop-loss
            take_profit_pct = Decimal("0.30")  # 30% take-profit
        elif risk_level == RiskLevel.HIGH:
            stop_loss_pct = Decimal("0.20")  # 20% stop-loss
            take_profit_pct = Decimal("0.40")  # 40% take-profit
        else:  # EXTREME
            stop_loss_pct = Decimal("0.25")  # 25% stop-loss
            take_profit_pct = Decimal("0.50")  # 50% take-profit

        # Calculate price levels
        if signal.action.value.startswith("BUY_"):
            # For buy orders, stop-loss below entry, take-profit above
            stop_loss_price = signal.market_price * (Decimal("1") - stop_loss_pct)
            take_profit_price = signal.market_price * (Decimal("1") + take_profit_pct)
        else:
            # For sell orders, stop-loss above entry, take-profit below
            stop_loss_price = signal.market_price * (Decimal("1") + stop_loss_pct)
            take_profit_price = signal.market_price * (Decimal("1") - take_profit_pct)

        # Ensure prices stay in valid range (0.00-1.00)
        stop_loss_price = max(Decimal("0.0001"), min(stop_loss_price, Decimal("0.9999")))
        take_profit_price = max(Decimal("0.0001"), min(take_profit_price, Decimal("0.9999")))

        # For extreme risk, don't set stop-loss (allows for larger moves)
        if risk_level == RiskLevel.EXTREME:
            stop_loss_price = None

        return stop_loss_price, take_profit_price

    def _calculate_risk_score(self, signal: TradeSignal) -> float:
        """
        Calculate risk score (0-100) for a trading signal.

        Higher score = higher risk.
        """
        score = 50.0  # Baseline

        # Adjust for probability edge (smaller edge = higher risk)
        edge_factor = 1.0 / max(0.01, float(signal.probability_edge))
        score += (edge_factor - 1.0) * 10

        # Adjust for confidence (lower confidence = higher risk)
        confidence_factor = (100.0 - signal.confidence_score) / 100.0
        score += confidence_factor * 20

        # Adjust for time horizon (longer horizon = higher risk)
        if signal.time_horizon_days > 30:
            score += 10
        elif signal.time_horizon_days > 90:
            score += 20

        # Adjust for volume score (lower volume = higher risk)
        volume_factor = (100.0 - signal.volume_score) / 100.0
        score += volume_factor * 10

        # Adjust for urgency (higher urgency = higher risk)
        if signal.urgency == "medium":
            score += 5
        elif signal.urgency == "high":
            score += 10
        elif signal.urgency == "critical":
            score += 20

        # Constrain to 0-100 range
        return max(0.0, min(score, 100.0))

    def _estimate_portfolio_value(self) -> Decimal:
        """Estimate total portfolio value (simplified)."""
        # Sum current values of all positions
        position_value = sum(
            pos.current_value for pos in self.portfolio_positions if pos.current_value is not None
        )

        # Add cash (simplified - in real implementation would track cash balance)
        # Assume $10,000 starting portfolio for simulation
        cash_balance = Decimal("10000.0")

        return position_value + cash_balance

    def _calculate_portfolio_exposure(self) -> Decimal:
        """Calculate portfolio exposure as percentage of total value."""
        portfolio_value = self._estimate_portfolio_value()
        if portfolio_value == 0:
            return Decimal("0")

        # Sum invested amounts (not current values)
        total_invested = sum(
            pos.invested_amount
            for pos in self.portfolio_positions
            if pos.invested_amount is not None
        )

        return (total_invested / portfolio_value) * Decimal("100")

    def _count_trades_today(self) -> int:
        """Count number of trades executed today."""
        today = datetime.now().date()
        return len(
            [
                trade
                for trade in self.trade_history
                if trade.executed_at.date() == today and not trade.simulation
            ]
        )
