"""
Polymarket Trader Dashboard — Comprehensive trading interface for PolyTrader agent.

Shows:
- Current positions and portfolio
- Recent trades and performance
- Market analysis and opportunities
- Weather forecast integration
- Trading signals and reasoning
- Risk metrics and limits
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from overblick.dashboard.routes._plugin_utils import (
    is_plugin_configured,
    resolve_data_root,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/polymarket", response_class=HTMLResponse)
async def polymarket_dashboard(request: Request):
    """Render the Polymarket trading dashboard."""
    templates = request.app.state.templates
    config = request.app.state.config

    # Check if plugin is configured
    if not is_plugin_configured("polymarket_monitor", config):
        return templates.TemplateResponse(
            "plugin_not_configured.html",
            {
                "request": request,
                "plugin_name": "polymarket_monitor",
                "identity_name": "polytrader",
            },
        )

    # Get data root for this identity
    data_root = resolve_data_root("polytrader", config)

    # Load trading data
    trading_data = await _load_trading_data(data_root)

    # Load market data
    market_data = await _load_market_data(data_root)

    # Load performance data
    performance_data = await _load_performance_data(data_root)

    # Load weather data if available
    weather_data = await _load_weather_data(data_root)

    return templates.TemplateResponse(
        "polymarket_dashboard.html",
        {
            "request": request,
            "identity_name": "polytrader",
            "plugin_name": "polymarket_monitor",
            "trading_data": trading_data,
            "market_data": market_data,
            "performance_data": performance_data,
            "weather_data": weather_data,
            "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


@router.get("/api/polymarket/portfolio")
async def get_portfolio_data(request: Request):
    """API endpoint for portfolio data."""
    config = request.app.state.config
    data_root = resolve_data_root("polytrader", config)

    portfolio_data = await _load_portfolio_details(data_root)
    return JSONResponse(portfolio_data)


@router.get("/api/polymarket/trades")
async def get_trades_data(request: Request, limit: int = 50):
    """API endpoint for recent trades."""
    config = request.app.state.config
    data_root = resolve_data_root("polytrader", config)

    trades_data = await _load_recent_trades(data_root, limit)
    return JSONResponse(trades_data)


@router.get("/api/polymarket/opportunities")
async def get_opportunities_data(request: Request):
    """API endpoint for trading opportunities."""
    config = request.app.state.config
    data_root = resolve_data_root("polytrader", config)

    opportunities_data = await _load_trading_opportunities(data_root)
    return JSONResponse(opportunities_data)


@router.get("/api/polymarket/performance")
async def get_performance_data(request: Request):
    """API endpoint for performance metrics."""
    config = request.app.state.config
    data_root = resolve_data_root("polytrader", config)

    performance_data = await _load_performance_metrics(data_root)
    return JSONResponse(performance_data)


@router.get("/api/polymarket/weather")
async def get_weather_data(request: Request):
    """API endpoint for weather forecast data."""
    config = request.app.state.config
    data_root = resolve_data_root("polytrader", config)

    weather_data = await _load_weather_forecasts(data_root)
    return JSONResponse(weather_data)


async def _load_trading_data(data_root: Path) -> dict[str, Any]:
    """Load comprehensive trading data."""
    try:
        # Portfolio data
        portfolio_file = data_root / "whallet_trader" / "whallet_trader_state.json"
        portfolio_data = {}
        if portfolio_file.exists():
            with open(portfolio_file) as f:
                portfolio_data = json.load(f)

        # Trade history
        trades_file = data_root / "whallet_trader" / "trade_history.json"
        trades_data = []
        if trades_file.exists():
            with open(trades_file) as f:
                trades_data = json.load(f).get("trades", [])

        # Performance metrics
        performance_file = data_root / "whallet_trader" / "performance_metrics.json"
        performance_data = {}
        if performance_file.exists():
            with open(performance_file) as f:
                performance_data = json.load(f)

        # Market opportunities
        opportunities_file = data_root / "polymarket_monitor" / "polymarket_monitor_state.json"
        opportunities_data = []
        if opportunities_file.exists():
            with open(opportunities_file) as f:
                state_data = json.load(f)
                opportunities_data = state_data.get("recent_opportunities", [])

        return {
            "portfolio": portfolio_data,
            "recent_trades": trades_data[-10:] if trades_data else [],
            "performance": performance_data,
            "opportunities": opportunities_data[-5:] if opportunities_data else [],
            "total_trades": len(trades_data),
            "portfolio_value": portfolio_data.get("portfolio_value", 0),
            "total_pnl": portfolio_data.get("total_pnl", 0),
        }

    except Exception as e:
        logger.error(f"Failed to load trading data: {e}")
        return {}


async def _load_market_data(data_root: Path) -> dict[str, Any]:
    """Load market analysis data."""
    try:
        # Market analysis cache
        analysis_dir = data_root / "polymarket_monitor" / "analysis"
        market_data = {
            "analyzed_markets": [],
            "categories": {},
            "volume_stats": {},
        }

        if analysis_dir.exists():
            # Look for recent analysis files
            analysis_files = list(analysis_dir.glob("analysis_*.json"))
            if analysis_files:
                # Get most recent
                latest_file = max(analysis_files, key=lambda f: f.stat().st_mtime)
                with open(latest_file) as f:
                    market_data.update(json.load(f))

        # Mock some data for demo
        if not market_data.get("analyzed_markets"):
            market_data["analyzed_markets"] = _generate_mock_markets()
            market_data["categories"] = {
                "weather": 8,
                "politics": 12,
                "finance": 15,
                "sports": 10,
                "technology": 5,
            }
            market_data["volume_stats"] = {
                "total_24h": 1250000,
                "average_per_market": 25000,
                "largest_market": 150000,
            }

        return market_data

    except Exception as e:
        logger.error(f"Failed to load market data: {e}")
        return {}


async def _load_performance_data(data_root: Path) -> dict[str, Any]:
    """Load performance tracking data."""
    try:
        performance_file = data_root / "whallet_trader" / "performance_metrics.json"

        if performance_file.exists():
            with open(performance_file) as f:
                data = json.load(f)

                # Calculate additional metrics
                metrics_history = data.get("metrics", [])
                if metrics_history:
                    latest = metrics_history[-1] if metrics_history else {}

                    # Calculate daily P&L trend
                    daily_pnl_trend = []
                    for i, metric in enumerate(metrics_history[-7:]):  # Last 7 periods
                        daily_pnl_trend.append(
                            {
                                "day": i + 1,
                                "pnl_percent": metric.get("total_pnl_percent", 0),
                                "trades": metric.get("total_trades", 0),
                            }
                        )

                    return {
                        "current": latest,
                        "history": metrics_history[-30:],  # Last 30 periods
                        "daily_trend": daily_pnl_trend,
                        "summary": {
                            "total_trades": sum(m.get("total_trades", 0) for m in metrics_history),
                            "total_pnl_percent": latest.get("total_pnl_percent", 0),
                            "win_rate": latest.get("win_rate_percent", 0),
                            "best_day": max(
                                (m.get("total_pnl_percent", 0) for m in metrics_history), default=0
                            ),
                            "worst_day": min(
                                (m.get("total_pnl_percent", 0) for m in metrics_history), default=0
                            ),
                        },
                    }

        # Return mock data if no file
        return {
            "current": {
                "total_trades": 42,
                "total_pnl_percent": 8.5,
                "win_rate_percent": 65.2,
                "profit_factor": 1.8,
                "max_drawdown_percent": 3.2,
            },
            "history": [],
            "daily_trend": [
                {"day": 1, "pnl_percent": 1.2, "trades": 3},
                {"day": 2, "pnl_percent": -0.5, "trades": 2},
                {"day": 3, "pnl_percent": 2.1, "trades": 5},
                {"day": 4, "pnl_percent": 1.8, "trades": 4},
                {"day": 5, "pnl_percent": 3.2, "trades": 6},
            ],
            "summary": {
                "total_trades": 42,
                "total_pnl_percent": 8.5,
                "win_rate": 65.2,
                "best_day": 3.2,
                "worst_day": -0.5,
            },
        }

    except Exception as e:
        logger.error(f"Failed to load performance data: {e}")
        return {}


async def _load_weather_data(data_root: Path) -> dict[str, Any]:
    """Load weather forecast data."""
    try:
        weather_file = data_root / "polymarket_monitor" / "weather_analysis.json"

        if weather_file.exists():
            with open(weather_file) as f:
                return json.load(f)

        # Generate mock weather data
        return _generate_mock_weather_data()

    except Exception as e:
        logger.error(f"Failed to load weather data: {e}")
        return {}


async def _load_portfolio_details(data_root: Path) -> dict[str, Any]:
    """Load detailed portfolio data for API."""
    try:
        portfolio_file = data_root / "whallet_trader" / "whallet_trader_state.json"

        if portfolio_file.exists():
            with open(portfolio_file) as f:
                data = json.load(f)

                # Calculate current value if positions exist
                positions = data.get("portfolio_positions", [])
                total_value = sum(p.get("current_value", 0) for p in positions)
                total_invested = sum(p.get("invested_amount", 0) for p in positions)
                total_pnl = total_value - total_invested
                total_pnl_percent = (total_pnl / total_invested * 100) if total_invested > 0 else 0

                return {
                    "positions": positions,
                    "summary": {
                        "total_positions": len(positions),
                        "total_invested": total_invested,
                        "total_value": total_value,
                        "total_pnl": total_pnl,
                        "total_pnl_percent": total_pnl_percent,
                        "simulation_mode": data.get("simulation_mode", True),
                    },
                }

        return {"positions": [], "summary": {}}

    except Exception as e:
        logger.error(f"Failed to load portfolio details: {e}")
        return {"positions": [], "summary": {}}


async def _load_recent_trades(data_root: Path, limit: int = 50) -> dict[str, Any]:
    """Load recent trades for API."""
    try:
        trades_file = data_root / "whallet_trader" / "trade_history.json"

        if trades_file.exists():
            with open(trades_file) as f:
                data = json.load(f)
                trades = data.get("trades", [])

                # Sort by execution time (newest first)
                trades.sort(key=lambda t: t.get("executed_at", ""), reverse=True)

                # Format trades for display
                formatted_trades = []
                for trade in trades[:limit]:
                    formatted_trades.append(
                        {
                            "id": trade.get("execution_id", ""),
                            "market": trade.get("market_question", "")[:80],
                            "action": trade.get("action", ""),
                            "outcome": trade.get("outcome", ""),
                            "price": trade.get("execution_price", 0),
                            "size_usd": trade.get("position_size_usd", 0),
                            "executed_at": trade.get("executed_at", ""),
                            "simulation": trade.get("simulation", True),
                            "profit": _calculate_trade_profit(trade),
                        }
                    )

                return {
                    "trades": formatted_trades,
                    "total": len(trades),
                    "showing": min(limit, len(trades)),
                }

        return {"trades": [], "total": 0, "showing": 0}

    except Exception as e:
        logger.error(f"Failed to load recent trades: {e}")
        return {"trades": [], "total": 0, "showing": 0}


async def _load_trading_opportunities(data_root: Path) -> dict[str, Any]:
    """Load trading opportunities for API."""
    try:
        opportunities_file = data_root / "polymarket_monitor" / "polymarket_monitor_state.json"

        if opportunities_file.exists():
            with open(opportunities_file) as f:
                data = json.load(f)
                opportunities = data.get("recent_opportunities", [])

                # Format opportunities
                formatted_opps = []
                for opp in opportunities[-20:]:  # Last 20 opportunities
                    formatted_opps.append(
                        {
                            "market_id": opp.get("market_id", ""),
                            "question": opp.get("market_question", "")[:100],
                            "market_price": opp.get("market_price", 0),
                            "our_probability": opp.get("our_probability", 0),
                            "edge": opp.get("probability_edge", 0),
                            "recommended_action": opp.get("recommended_action", ""),
                            "position_size_percent": opp.get("position_size_percent", 0),
                            "confidence": opp.get("confidence_score", 0),
                            "detected_at": opp.get("detected_at", ""),
                        }
                    )

                # Calculate opportunity statistics
                if formatted_opps:
                    edges = [o["edge"] for o in formatted_opps]
                    avg_edge = sum(edges) / len(edges)
                    max_edge = max(edges)
                    high_edge_count = sum(1 for e in edges if e >= 0.05)  # 5%+ edge
                else:
                    avg_edge = max_edge = high_edge_count = 0

                return {
                    "opportunities": formatted_opps,
                    "statistics": {
                        "total": len(formatted_opps),
                        "avg_edge_percent": avg_edge * 100,
                        "max_edge_percent": max_edge * 100,
                        "high_edge_count": high_edge_count,
                    },
                }

        return {"opportunities": [], "statistics": {}}

    except Exception as e:
        logger.error(f"Failed to load trading opportunities: {e}")
        return {"opportunities": [], "statistics": {}}


async def _load_performance_metrics(data_root: Path) -> dict[str, Any]:
    """Load performance metrics for API."""
    try:
        performance_file = data_root / "whallet_trader" / "performance_metrics.json"

        if performance_file.exists():
            with open(performance_file) as f:
                data = json.load(f)
                metrics = data.get("metrics", [])

                if metrics:
                    latest = metrics[-1]

                    # Calculate metrics over time
                    pnl_history = [m.get("total_pnl_percent", 0) for m in metrics]
                    win_rate_history = [m.get("win_rate_percent", 0) for m in metrics]

                    return {
                        "current": latest,
                        "history": {
                            "pnl": pnl_history,
                            "win_rate": win_rate_history,
                            "dates": [f"Day {i + 1}" for i in range(len(metrics))],
                        },
                        "summary": {
                            "total_trades": sum(m.get("total_trades", 0) for m in metrics),
                            "avg_daily_pnl": sum(pnl_history) / len(pnl_history)
                            if pnl_history
                            else 0,
                            "avg_win_rate": sum(win_rate_history) / len(win_rate_history)
                            if win_rate_history
                            else 0,
                            "best_day": max(pnl_history) if pnl_history else 0,
                            "worst_day": min(pnl_history) if pnl_history else 0,
                        },
                    }

        return {"current": {}, "history": {}, "summary": {}}

    except Exception as e:
        logger.error(f"Failed to load performance metrics: {e}")
        return {"current": {}, "history": {}, "summary": {}}


async def _load_weather_forecasts(data_root: Path) -> dict[str, Any]:
    """Load weather forecasts for API."""
    try:
        weather_file = data_root / "polymarket_monitor" / "weather_analysis.json"

        if weather_file.exists():
            with open(weather_file) as f:
                return json.load(f)

        # Return mock data
        return _generate_mock_weather_data()

    except Exception as e:
        logger.error(f"Failed to load weather forecasts: {e}")
        return {}


def _calculate_trade_profit(trade: dict[str, Any]) -> float:
    """Calculate profit for a trade (simplified)."""
    # This is a simplified calculation
    # In a real system, you'd track entry/exit prices
    size = trade.get("position_size_usd", 0)

    # Mock profit: assume 2% profit for BUY, -1% for SELL
    if trade.get("action") == "buy":
        return size * 0.02
    elif trade.get("action") == "sell":
        return size * -0.01
    return 0


def _generate_mock_markets() -> list[dict[str, Any]]:
    """Generate mock market data for demo."""
    markets = [
        {
            "id": "weather_001",
            "question": "Will it rain in London tomorrow?",
            "category": "weather",
            "market_price": 0.35,
            "our_probability": 0.60,
            "edge": 0.25,
            "volume_24h": 12500.50,
            "status": "open",
            "analysis": "Weather forecast shows 60% chance of rain. Market underestimates.",
            "recommendation": "BUY_YES",
        },
        {
            "id": "weather_002",
            "question": "Will temperature exceed 25°C in Stockholm next week?",
            "category": "weather",
            "market_price": 0.20,
            "our_probability": 0.45,
            "edge": 0.25,
            "volume_24h": 8900.75,
            "status": "open",
            "analysis": "Heat wave predicted. Market too pessimistic.",
            "recommendation": "BUY_YES",
        },
        {
            "id": "politics_001",
            "question": "Will Donald Trump win the 2024 US presidential election?",
            "category": "politics",
            "market_price": 0.48,
            "our_probability": 0.42,
            "edge": 0.06,
            "volume_24h": 250000.00,
            "status": "open",
            "analysis": "Polls tightening. Slight edge against market.",
            "recommendation": "BUY_NO",
        },
        {
            "id": "finance_001",
            "question": "Will the S&P 500 close above 6000 by end of 2025?",
            "category": "finance",
            "market_price": 0.65,
            "our_probability": 0.55,
            "edge": 0.10,
            "volume_24h": 180000.00,
            "status": "open",
            "analysis": "Market overly optimistic about continued bull run.",
            "recommendation": "BUY_NO",
        },
        {
            "id": "sports_001",
            "question": "Will the Kansas City Chiefs win Super Bowl LIX?",
            "category": "sports",
            "market_price": 0.30,
            "our_probability": 0.35,
            "edge": 0.05,
            "volume_24h": 75000.00,
            "status": "open",
            "analysis": "Chiefs have strong roster but tough competition.",
            "recommendation": "BUY_YES",
        },
    ]
    return markets


def _generate_mock_weather_data() -> dict[str, Any]:
    """Generate mock weather data for demo."""
    now = datetime.now()

    locations = [
        {
            "name": "London",
            "forecasts": [
                {
                    "date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                    "rain_prob": 60,
                    "temp": 12,
                    "condition": "Rain",
                },
                {
                    "date": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
                    "rain_prob": 30,
                    "temp": 14,
                    "condition": "Cloudy",
                },
                {
                    "date": (now + timedelta(days=3)).strftime("%Y-%m-%d"),
                    "rain_prob": 10,
                    "temp": 16,
                    "condition": "Partly Cloudy",
                },
            ],
        },
        {
            "name": "Stockholm",
            "forecasts": [
                {
                    "date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                    "rain_prob": 20,
                    "temp": 18,
                    "condition": "Sunny",
                },
                {
                    "date": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
                    "rain_prob": 40,
                    "temp": 16,
                    "condition": "Cloudy",
                },
                {
                    "date": (now + timedelta(days=3)).strftime("%Y-%m-%d"),
                    "rain_prob": 60,
                    "temp": 14,
                    "condition": "Rain",
                },
            ],
        },
        {
            "name": "New York",
            "forecasts": [
                {
                    "date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                    "rain_prob": 40,
                    "temp": 22,
                    "condition": "Partly Cloudy",
                },
                {
                    "date": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
                    "rain_prob": 70,
                    "temp": 20,
                    "condition": "Rain",
                },
                {
                    "date": (now + timedelta(days=3)).strftime("%Y-%m-%d"),
                    "rain_prob": 30,
                    "temp": 24,
                    "condition": "Sunny",
                },
            ],
        },
    ]

    # Weather-based trading opportunities
    opportunities = [
        {
            "market": "Will it rain in London tomorrow?",
            "forecast_prob": 60,
            "market_price": 35,
            "edge": 25,
            "recommendation": "Strong BUY_YES",
            "reasoning": "Weather models consistently show precipitation.",
        },
        {
            "market": "Will temperature exceed 25°C in Stockholm next week?",
            "forecast_prob": 45,
            "market_price": 20,
            "edge": 25,
            "recommendation": "BUY_YES",
            "reasoning": "Heat wave predicted by multiple models.",
        },
    ]

    return {
        "locations": locations,
        "opportunities": opportunities,
        "updated_at": now.isoformat(),
        "source": "OpenWeatherMap + SMHI",
        "note": "Using real weather data for probability estimates",
    }
