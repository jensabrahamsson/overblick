"""
Polymarket Trading Dashboard route — prediction market trading.

Displays market monitoring, trading opportunities, portfolio positions,
trade history, and risk metrics from the Polymarket trading agent.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/polymarket", response_class=HTMLResponse)
async def polymarket_page(request: Request):
    """Render the Polymarket Trading dashboard page."""
    templates = request.app.state.templates

    # Load initial data for the first render
    try:
        data = await asyncio.to_thread(_load_polymarket_data, request)
        data_errors: list[str] = []
    except Exception as e:
        logger.error("Failed to load polymarket data: %s", e, exc_info=True)
        data = _get_default_data()
        data_errors = ["Failed to load trading data. Check server logs for details."]

    return templates.TemplateResponse(
        "polymarket.html",
        {
            "request": request,
            "csrf_token": request.state.session.get("csrf_token", ""),
            "stats": data["stats"],
            "markets": data["markets"],
            "categories": data.get("categories", []),
            "opportunities": data["opportunities"],
            "positions": data["positions"],
            "trades": data["trades"],
            "risk_metrics": data["risk_metrics"],
            "alerts": data["alerts"],
            "activity_feed": data.get("activity_feed", []),
            "latest_reasoning": data.get("latest_reasoning", ""),
            "data_errors": data_errors,
        },
    )


@router.get("/partials/polymarket-content", response_class=HTMLResponse)
async def polymarket_content(request: Request):
    """Render only the Polymarket content partial for htmx refresh."""
    templates = request.app.state.templates

    try:
        data = await asyncio.to_thread(_load_polymarket_data, request)
    except Exception as e:
        logger.error("Failed to refresh polymarket data: %s", e)
        data = _get_default_data()

    return templates.TemplateResponse(
        "partials/polymarket_content.html",
        {
            "request": request,
            "stats": data["stats"],
            "markets": data["markets"],
            "categories": data.get("categories", []),
            "opportunities": data["opportunities"],
            "positions": data["positions"],
            "trades": data["trades"],
            "risk_metrics": data["risk_metrics"],
            "alerts": data["alerts"],
            "activity_feed": data.get("activity_feed", []),
            "latest_reasoning": data.get("latest_reasoning", ""),
        },
    )


@router.get("/api/polymarket/chart/{market_id}")
async def market_chart_data(market_id: str, request: Request):
    """API endpoint returning price history + trades for chart rendering."""
    import re

    # Validate market_id format
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{1,100}", market_id):
        return JSONResponse(
            {"error": "Invalid market ID", "prices": [], "trades": []},
            status_code=400,
        )

    conn = None
    try:
        import asyncpg

        # Read DSN from config or environment
        dsn = getattr(request.app.state, "postgres_dsn", None)
        if not dsn:
            import os
            dsn = os.environ.get("OVERBLICK_POSTGRES_DSN")
        if not dsn:
            return JSONResponse(
                {"error": "Database not configured", "prices": [], "trades": []},
                status_code=503,
            )

        conn = await asyncpg.connect(dsn)

        # Price history
        prices = await conn.fetch(
            """
            SELECT yes_price, no_price, captured_at
            FROM overblick.market_snapshots
            WHERE market_id = $1
            ORDER BY captured_at ASC
            """,
            market_id,
        )

        # Trades on this market
        trades = await conn.fetch(
            """
            SELECT action, outcome, price, position_size_usd, executed_at
            FROM overblick.trade_log
            WHERE market_id = $1
            ORDER BY executed_at ASC
            """,
            market_id,
        )

        # Market question
        question = ""
        if prices:
            q_row = await conn.fetchrow(
                "SELECT market_question FROM overblick.market_snapshots WHERE market_id = $1 LIMIT 1",
                market_id,
            )
            if q_row:
                question = q_row["market_question"] or ""

        return JSONResponse(
            {
                "market_id": market_id,
                "question": question,
                "prices": [
                    {
                        "time": int(r["captured_at"].timestamp()),
                        "yes": float(r["yes_price"]),
                        "no": float(r["no_price"]),
                    }
                    for r in prices
                ],
                "trades": [
                    {
                        "time": int(r["executed_at"].timestamp()),
                        "action": r["action"],
                        "outcome": r["outcome"],
                        "price": float(r["price"]),
                        "size": float(r["position_size_usd"]) if r["position_size_usd"] else 0,
                    }
                    for r in trades
                ],
            }
        )
    except Exception as e:
        logger.error("Chart data error for market %s: %s", market_id, e)
        return JSONResponse(
            {"error": "Failed to load chart data", "prices": [], "trades": []},
            status_code=500,
        )
    finally:
        if conn:
            await conn.close()


def _get_default_data() -> dict:
    """Return default empty data structure."""
    return {
        "stats": {
            "total_markets": 0,
            "active_markets": 0,
            "total_opportunities": 0,
            "high_confidence_opportunities": 0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl_usd": 0.0,
            "portfolio_value_usd": 0.0,
            "daily_pnl_usd": 0.0,
        },
        "markets": [],
        "categories": [],
        "opportunities": [],
        "positions": [],
        "trades": [],
        "risk_metrics": {
            "max_drawdown_percent": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate_percent": 0.0,
            "profit_factor": 0.0,
            "avg_position_size_usd": 0.0,
            "current_exposure_percent": 0.0,
            "daily_loss_used_percent": 0.0,
        },
        "alerts": [],
        "activity_feed": [],
        "latest_reasoning": "",
    }


def has_data() -> bool:
    """Return True if polymarket plugin is configured for any identity."""
    from overblick.dashboard.routes._plugin_utils import is_plugin_configured

    return is_plugin_configured("polymarket_monitor")


def _load_polymarket_data(request: Request) -> dict:
    """Load Polymarket trading data from JSON state files across identities."""
    from overblick.dashboard.routes._plugin_utils import resolve_data_root

    data_root = resolve_data_root(request)
    markets: list[dict] = []
    opportunities: list[dict] = []
    positions: list[dict] = []
    trades: list[dict] = []
    alerts: list[dict] = []
    activity_feed: list[dict] = []
    latest_reasoning = ""

    starting_balance = Decimal("100")
    stats = {
        "total_markets": 0,
        "active_markets": 0,
        "total_opportunities": 0,
        "high_confidence_opportunities": 0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl_usd": Decimal("0"),
        "portfolio_value_usd": Decimal("0"),
        "positions_value_usd": Decimal("0"),
        "total_invested_usd": Decimal("0"),
        "cash_usd": starting_balance,
        "starting_balance_usd": starting_balance,
        "daily_pnl_usd": Decimal("0"),
    }

    risk_metrics = {
        "max_drawdown_percent": Decimal("0"),
        "sharpe_ratio": Decimal("0"),
        "win_rate_percent": Decimal("0"),
        "profit_factor": Decimal("0"),
        "avg_position_size_usd": Decimal("0"),
        "current_exposure_percent": Decimal("0"),
        "daily_loss_used_percent": Decimal("0"),
    }

    if not data_root.exists():
        stats["portfolio_value_usd"] = stats["cash_usd"]  # No positions, portfolio = cash
        return {
            "stats": stats,
            "markets": markets,
            "opportunities": opportunities,
            "positions": positions,
            "trades": trades,
            "risk_metrics": risk_metrics,
            "alerts": alerts,
            "activity_feed": [],
            "latest_reasoning": "",
        }

    for identity_dir in data_root.iterdir():
        if not identity_dir.is_dir():
            continue

        identity_name = identity_dir.name

        # Load polymarket_monitor state
        monitor_state_path = identity_dir / "polymarket_state.json"
        if monitor_state_path.exists():
            try:
                with open(monitor_state_path) as f:
                    monitor_state = json.load(f)

                # Extract markets and opportunities
                if "monitored_markets" in monitor_state:
                    for market_id, market_data in monitor_state["monitored_markets"].items():
                        # Create a copy with renamed fields for template compatibility
                        market = dict(market_data)
                        market["identity"] = identity_name
                        market["market_id"] = market_id
                        # Rename 'question' to 'market_question' if present (template expects market_question)
                        if "question" in market:
                            market["market_question"] = market.pop("question")
                        # Normalize status to uppercase for template consistency
                        if "status" in market:
                            market["status"] = market["status"].upper()
                        markets.append(market)
                        stats["total_markets"] += 1

                        if market.get("status") == "OPEN":
                            stats["active_markets"] += 1

                if "recent_opportunities" in monitor_state:
                    for opp in monitor_state["recent_opportunities"]:
                        opp["identity"] = identity_name
                        opportunities.append(opp)
                        stats["total_opportunities"] += 1

                        # Use reasoning from latest opportunity
                        if "reasoning" in opp:
                            latest_reasoning = opp["reasoning"]

                        if opp.get("confidence_score", 0) >= 70:
                            stats["high_confidence_opportunities"] += 1

                        # Add to activity feed (skip dummy 1% edge signals from quick-screen)
                        edge_pct = opp.get("probability_edge", 0) * 100
                        if edge_pct > 1.5:
                            activity_feed.append({
                                "time": opp.get("detected_at", ""),
                                "identity": identity_name,
                                "type": "opportunity",
                                "msg": f"SIGNAL: {opp.get('recommended_outcome')} ({edge_pct:.1f}% edge, {opp.get('confidence_score', 0):.0f}% conf) — {opp.get('market_question', '')}",
                            })

                if "alerts" in monitor_state:
                    for alert in monitor_state["alerts"]:
                        alert["identity"] = identity_name
                        alerts.append(alert)
                        # Add to activity feed
                        activity_feed.append({
                            "time": alert.get("triggered_at", ""),
                            "identity": identity_name,
                            "type": "alert",
                            "msg": f"ALERT: {alert.get('message', alert.get('condition', {}).get('name', 'Triggered'))}",
                        })

            except Exception as e:
                logger.warning(
                    "Failed to load polymarket_monitor state for %s: %s", identity_name, e
                )

        # Load whallet_trader state
        trader_state_path = identity_dir / "whallet_trader_state.json"
        if trader_state_path.exists():
            try:
                with open(trader_state_path) as f:
                    trader_state = json.load(f)

                # Extract positions and trades
                if "portfolio_positions" in trader_state:
                    for position in trader_state["portfolio_positions"]:
                        position["identity"] = identity_name
                        positions.append(position)

                        # Track invested and current value
                        try:
                            invested = Decimal(str(position.get("invested_amount", 0)))
                            current = Decimal(str(position.get("current_value", position.get("current_value_usd", 0))))
                            stats["total_invested_usd"] += invested
                            stats["positions_value_usd"] += current
                        except (ValueError, TypeError, ArithmeticError):
                            pass

                if "trade_history" in trader_state:
                    for trade in trader_state["trade_history"]:
                        trade["identity"] = identity_name
                        trades.append(trade)
                        stats["total_trades"] += 1

                        # Add to activity feed
                        activity_feed.append({
                            "time": trade.get("executed_at", ""),
                            "identity": identity_name,
                            "type": "trade",
                            "msg": f"TRADE: {trade.get('action')} {trade.get('position_size_usd', 0):.0f}$ at {trade.get('execution_price', 0):.3f}"
                        })

                        # Calculate P&L
                        if "realized_pnl_usd" in trade:
                            try:
                                pnl = Decimal(str(trade["realized_pnl_usd"]))
                                stats["total_pnl_usd"] += pnl

                                # Check if trade was today
                                if "executed_at" in trade:
                                    trade_time = datetime.fromisoformat(
                                        trade["executed_at"].replace("Z", "+00:00")
                                    )
                                    if datetime.now(UTC) - trade_time < timedelta(days=1):
                                        stats["daily_pnl_usd"] += pnl

                                if pnl > 0:
                                    stats["winning_trades"] += 1
                                elif pnl < 0:
                                    stats["losing_trades"] += 1
                            except (ValueError, TypeError, ArithmeticError):
                                pass

                # Extract risk metrics
                if "risk_metrics" in trader_state:
                    risk_metrics.update(trader_state["risk_metrics"])

            except Exception as e:
                logger.warning("Failed to load whallet_trader state for %s: %s", identity_name, e)

    # Calculate derived stats
    # Only compute win_rate_percent if not already provided by plugin risk metrics
    if stats["total_trades"] > 0 and risk_metrics.get("win_rate_percent", 0) == 0:
        risk_metrics["win_rate_percent"] = Decimal(
            str((stats["winning_trades"] / stats["total_trades"]) * 100)
        ).quantize(Decimal("0.01"))

    # Sort data for display
    markets.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
    opportunities.sort(key=lambda x: x.get("probability_edge", 0), reverse=True)
    positions.sort(key=lambda x: x.get("unrealized_pnl_usd", 0), reverse=True)
    trades.sort(key=lambda x: x.get("executed_at", ""), reverse=True)
    alerts.sort(key=lambda x: x.get("triggered_at", ""), reverse=True)
    activity_feed.sort(key=lambda x: x.get("time", ""), reverse=True)

    # Correct portfolio calculation:
    # cash = starting_balance - total_invested
    # portfolio = cash + positions_value
    # pnl = portfolio - starting_balance
    stats["cash_usd"] = stats["starting_balance_usd"] - stats["total_invested_usd"]
    stats["portfolio_value_usd"] = stats["cash_usd"] + stats["positions_value_usd"]
    stats["total_pnl_usd"] = stats["portfolio_value_usd"] - stats["starting_balance_usd"]

    # Convert Decimal to float for JSON serialization
    stats_serializable = {}
    for key, value in stats.items():
        if isinstance(value, Decimal):
            stats_serializable[key] = float(value)
        else:
            stats_serializable[key] = value

    risk_metrics_serializable = {}
    for key, value in risk_metrics.items():
        if isinstance(value, Decimal):
            risk_metrics_serializable[key] = float(value)
        else:
            risk_metrics_serializable[key] = value

    # Extract unique categories for filter UI
    categories = sorted({m.get("category", "other") for m in markets})

    return {
        "stats": stats_serializable,
        "markets": markets,
        "categories": categories,
        "opportunities": opportunities[:20],
        "positions": positions,
        "trades": trades[:50],
        "risk_metrics": risk_metrics_serializable,
        "alerts": alerts[:20],
        "activity_feed": activity_feed[:100],
        "latest_reasoning": latest_reasoning,
    }
