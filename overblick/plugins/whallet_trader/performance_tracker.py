"""
Performance tracking for paper trading.

Tracks trading performance metrics over time and generates reports.
Saves performance data to disk for historical analysis.
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import TradingPerformance, PortfolioPosition, TradeExecution

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Tracks trading performance metrics over time."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.metrics_file = data_dir / "performance_metrics.json"
        self.trades_file = data_dir / "trade_history.json"

        # Load existing data
        self.metrics_history: List[TradingPerformance] = []
        self.trade_history: List[TradeExecution] = []
        self._load_data()

    def _load_data(self) -> None:
        """Load performance data from disk."""
        try:
            if self.metrics_file.exists():
                with open(self.metrics_file, "r") as f:
                    data = json.load(f)
                    self.metrics_history = [
                        TradingPerformance(**item) for item in data.get("metrics", [])
                    ]

            if self.trades_file.exists():
                with open(self.trades_file, "r") as f:
                    data = json.load(f)
                    self.trade_history = [TradeExecution(**item) for item in data.get("trades", [])]

            logger.info(
                f"Loaded {len(self.metrics_history)} metrics and {len(self.trade_history)} trades"
            )

        except Exception as e:
            logger.error(f"Failed to load performance data: {e}")
            self.metrics_history = []
            self.trade_history = []

    def _save_data(self) -> None:
        """Save performance data to disk."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)

            # Save metrics
            metrics_data = {
                "metrics": [m.model_dump() for m in self.metrics_history],
                "last_updated": datetime.now().isoformat(),
            }
            with open(self.metrics_file, "w") as f:
                json.dump(metrics_data, f, indent=2, default=str)

            # Save trades
            trades_data = {
                "trades": [t.model_dump() for t in self.trade_history],
                "last_updated": datetime.now().isoformat(),
            }
            with open(self.trades_file, "w") as f:
                json.dump(trades_data, f, indent=2, default=str)

        except Exception as e:
            logger.error(f"Failed to save performance data: {e}")

    def record_trade(self, execution: TradeExecution) -> None:
        """Record a completed trade."""
        self.trade_history.append(execution)
        self._save_data()
        logger.info(f"Recorded trade {execution.execution_id} in history")

    def calculate_performance_metrics(
        self, portfolio_positions: List[PortfolioPosition], period_days: int = 30
    ) -> TradingPerformance:
        """Calculate performance metrics for the specified period."""
        period_end = datetime.now()
        period_start = period_end - timedelta(days=period_days)

        # Filter trades within period
        period_trades = [
            t for t in self.trade_history if period_start <= t.executed_at <= period_end
        ]

        if not period_trades:
            # Return empty metrics if no trades
            return TradingPerformance(
                period_start=period_start,
                period_end=period_end,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                total_pnl_usd=Decimal("0"),
                total_pnl_percent=Decimal("0"),
                max_drawdown_percent=Decimal("0"),
                win_rate_percent=Decimal("0"),
                average_win_usd=Decimal("0"),
                average_loss_usd=Decimal("0"),
                profit_factor=Decimal("0"),
                average_position_size_usd=Decimal("0"),
                average_holding_period_days=Decimal("0"),
            )

        # Calculate basic metrics
        total_trades = len(period_trades)

        # Calculate P&L from trades (simplified - in real system would track entry/exit)
        # For simulation, we'll estimate based on current portfolio
        total_invested = Decimal(sum(float(p.invested_amount) for p in portfolio_positions))
        total_value = Decimal(sum(float(p.current_value) for p in portfolio_positions))
        total_pnl_usd = total_value - total_invested
        total_pnl_percent = (
            (total_pnl_usd / total_invested * Decimal("100"))
            if total_invested > 0
            else Decimal("0")
        )

        # Count winning/losing trades (simplified)
        winning_trades = sum(
            1 for t in period_trades if t.simulation
        )  # All simulated trades "win" for now
        losing_trades = total_trades - winning_trades

        # Calculate win rate
        win_rate_percent = (
            (Decimal(str(winning_trades)) / Decimal(str(total_trades)) * Decimal("100"))
            if total_trades > 0
            else Decimal("0")
        )

        # Calculate average win/loss (simplified)
        average_win_usd = (
            total_pnl_usd / Decimal(str(winning_trades)) if winning_trades > 0 else Decimal("0")
        )
        average_loss_usd = Decimal("0") if losing_trades == 0 else Decimal("-10")  # Placeholder

        # Calculate profit factor
        gross_profits = total_pnl_usd if total_pnl_usd > Decimal("0") else Decimal("0")
        gross_losses = abs(total_pnl_usd) if total_pnl_usd < Decimal("0") else Decimal("0")
        profit_factor = (
            gross_profits / gross_losses if gross_losses > Decimal("0") else Decimal("0")
        )

        # Calculate position statistics
        position_sizes = [t.position_size_usd for t in period_trades]
        average_position_size_usd = (
            sum(position_sizes) / Decimal(str(len(position_sizes)))
            if position_sizes
            else Decimal("0")
        )

        # Calculate holding periods (simplified)
        average_holding_period_days = Decimal("7")  # Placeholder

        # Calculate max drawdown (simplified)
        max_drawdown_percent = Decimal("5.0")  # Placeholder

        metrics = TradingPerformance(
            period_start=period_start,
            period_end=period_end,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_pnl_usd=total_pnl_usd,
            total_pnl_percent=total_pnl_percent,
            max_drawdown_percent=max_drawdown_percent,
            win_rate_percent=win_rate_percent,
            average_win_usd=average_win_usd,
            average_loss_usd=average_loss_usd,
            profit_factor=profit_factor,
            average_position_size_usd=average_position_size_usd,
            average_holding_period_days=average_holding_period_days,
        )

        # Save to history
        self.metrics_history.append(metrics)
        # Keep only last 100 metrics
        if len(self.metrics_history) > 100:
            self.metrics_history = self.metrics_history[-100:]

        self._save_data()

        return metrics

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary."""
        if not self.trade_history:
            return {
                "total_trades": 0,
                "total_pnl_usd": 0,
                "total_pnl_percent": 0,
                "win_rate_percent": 0,
                "recent_trades": [],
                "metrics_history": [],
            }

        # Calculate overall metrics
        total_trades = len(self.trade_history)

        # Get recent trades (last 10)
        recent_trades = (
            self.trade_history[-10:] if len(self.trade_history) > 10 else self.trade_history
        )

        # Calculate overall P&L (simplified)
        # In a real system, this would track actual entry/exit prices
        total_pnl_usd = Decimal("0")
        for trade in self.trade_history:
            # Simulated P&L: assume 2% profit per trade
            if trade.simulation:
                total_pnl_usd += trade.position_size_usd * Decimal("0.02")

        # Assume $10,000 starting portfolio
        starting_portfolio = Decimal("10000")
        total_pnl_percent = (
            (total_pnl_usd / starting_portfolio * Decimal("100"))
            if starting_portfolio > 0
            else Decimal("0")
        )

        # Calculate win rate (simplified)
        winning_trades = sum(1 for t in self.trade_history if t.simulation)
        win_rate_percent = (
            (Decimal(str(winning_trades)) / Decimal(str(total_trades)) * Decimal("100"))
            if total_trades > 0
            else Decimal("0")
        )

        return {
            "total_trades": total_trades,
            "total_pnl_usd": float(total_pnl_usd),
            "total_pnl_percent": float(total_pnl_percent),
            "win_rate_percent": float(win_rate_percent),
            "recent_trades": [t.model_dump() for t in recent_trades],
            "metrics_history": [
                m.model_dump() for m in self.metrics_history[-5:]
            ],  # Last 5 periods
        }

    def generate_performance_report(self) -> str:
        """Generate a human-readable performance report."""
        summary = self.get_performance_summary()

        report_lines = [
            "=" * 60,
            "POLYMARKET PAPER TRADING PERFORMANCE REPORT",
            "=" * 60,
            f"Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "OVERALL PERFORMANCE:",
            f"  Total Trades: {summary['total_trades']}",
            f"  Total P&L: ${summary['total_pnl_usd']:.2f} ({summary['total_pnl_percent']:.2f}%)",
            f"  Win Rate: {summary['win_rate_percent']:.1f}%",
            "",
        ]

        if summary["recent_trades"]:
            report_lines.append("RECENT TRADES (last 10):")
            for i, trade in enumerate(summary["recent_trades"], 1):
                market_short = (
                    trade["market_question"][:40] + "..."
                    if len(trade["market_question"]) > 40
                    else trade["market_question"]
                )
                report_lines.append(
                    f"  {i}. {trade['action']} {trade['outcome']} @ ${trade['execution_price']:.4f} "
                    f"({market_short})"
                )

        if summary["metrics_history"]:
            report_lines.append("")
            report_lines.append("RECENT PERFORMANCE METRICS:")
            for metrics in summary["metrics_history"]:
                period = f"{metrics['period_start'][:10]} to {metrics['period_end'][:10]}"
                report_lines.append(
                    f"  {period}: {metrics['total_trades']} trades, "
                    f"P&L: {metrics['total_pnl_percent']:.2f}%, "
                    f"Win Rate: {metrics['win_rate_percent']:.1f}%"
                )

        report_lines.extend(
            [
                "",
                "=" * 60,
                "END OF REPORT",
                "=" * 60,
            ]
        )

        return "\n".join(report_lines)
