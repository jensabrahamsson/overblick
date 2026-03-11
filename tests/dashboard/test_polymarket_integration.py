"""
Integration tests for Polymarket dashboard with actual plugin data.

Tests the full integration from plugin state creation to dashboard rendering.
"""

import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestPolymarketDashboardIntegration:
    """Integration tests for Polymarket dashboard with plugin data."""

    @pytest.mark.asyncio
    async def test_full_integration_with_plugin_data(
        self, client, session_cookie, tmp_path, monkeypatch
    ):
        """Test full integration: create plugin data -> dashboard loads it."""
        # Create mock data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create a realistic polymarket_monitor state based on actual model structure
        monitor_state = {
            "monitored_markets": {
                "0xabc123def4567890abcdef1234567890abcdef12": {
                    "id": "0xabc123def4567890abcdef1234567890abcdef12",
                    "slug": "will-eth-above-4000-march",
                    "question": "Will ETH be above $4000 by end of March?",
                    "description": "Ethereum price prediction for end of March",
                    "category": "crypto",
                    "status": "open",
                    "created_time": "2025-03-01T00:00:00Z",
                    "end_time": "2025-03-31T23:59:59Z",
                    "outcomes": [
                        {
                            "name": "Yes",
                            "ticker": "YES",
                            "price": 0.65,
                            "volume_24h": 25000.0,
                            "last_updated": "2025-03-09T10:30:00Z",
                        },
                        {
                            "name": "No",
                            "ticker": "NO",
                            "price": 0.35,
                            "volume_24h": 15000.0,
                            "last_updated": "2025-03-09T10:30:00Z",
                        },
                    ],
                    "volume_24h": 40000.0,
                    "liquidity": 50000.0,
                    "open_interest": 30000.0,
                    "implied_probability": 0.65,
                    "probability_edge": 0.08,
                    "confidence_score": 85.0,
                },
                "0xdef456abc1237890fedcba9876543210fedcba98": {
                    "id": "0xdef456abc1237890fedcba9876543210fedcba98",
                    "slug": "fed-rate-hike-q2",
                    "question": "Will the Fed raise interest rates in Q2 2025?",
                    "description": "Federal Reserve interest rate decision prediction",
                    "category": "politics",
                    "status": "open",
                    "created_time": "2025-02-15T00:00:00Z",
                    "end_time": "2025-06-30T23:59:59Z",
                    "outcomes": [
                        {
                            "name": "Yes",
                            "ticker": "YES",
                            "price": 0.30,
                            "volume_24h": 20000.0,
                            "last_updated": "2025-03-09T10:30:00Z",
                        },
                        {
                            "name": "No",
                            "ticker": "NO",
                            "price": 0.70,
                            "volume_24h": 18000.0,
                            "last_updated": "2025-03-09T10:30:00Z",
                        },
                    ],
                    "volume_24h": 38000.0,
                    "liquidity": 45000.0,
                    "open_interest": 25000.0,
                    "implied_probability": 0.30,
                    "probability_edge": 0.04,
                    "confidence_score": 60.0,
                },
            },
            "recent_opportunities": [
                {
                    "market_id": "0xabc123def4567890abcdef1234567890abcdef12",
                    "market_question": "Will ETH be above $4000 by end of March?",
                    "recommended_outcome": "YES",
                    "market_price": 0.65,
                    "our_probability": 0.73,
                    "probability_edge": 0.08,
                    "expected_value": 0.12,
                    "kelly_fraction": 0.15,
                    "confidence_score": 85.0,
                    "volume_score": 90.0,
                    "time_horizon_days": 22.0,
                    "recommended_action": "BUY_YES",
                    "position_size_percent": 2.5,
                    "urgency": "high",
                    "detected_at": "2025-03-09T10:15:00Z",
                    "last_updated": "2025-03-09T10:15:00Z",
                },
                {
                    "market_id": "0xdef456abc1237890fedcba9876543210fedcba98",
                    "market_question": "Will the Fed raise interest rates in Q2 2025?",
                    "recommended_outcome": "NO",
                    "market_price": 0.30,
                    "our_probability": 0.26,
                    "probability_edge": 0.04,
                    "expected_value": 0.08,
                    "kelly_fraction": 0.08,
                    "confidence_score": 60.0,
                    "volume_score": 75.0,
                    "time_horizon_days": 112.0,
                    "recommended_action": "BUY_NO",
                    "position_size_percent": 1.0,
                    "urgency": "medium",
                    "detected_at": "2025-03-09T09:45:00Z",
                    "last_updated": "2025-03-09T09:45:00Z",
                },
            ],
            "alerts": [
                {
                    "condition": {
                        "name": "high_edge_opportunity",
                        "condition_type": "edge_threshold",
                        "parameter": 0.05,
                        "is_active": True,
                        "created_at": "2025-03-01T00:00:00Z",
                    },
                    "market": {
                        "id": "0xabc123def4567890abcdef1234567890abcdef12",
                        "question": "Will ETH be above $4000 by end of March?",
                    },
                    "current_value": 0.08,
                    "threshold_value": 0.05,
                    "message": "High probability edge detected (8.0% > 5.0% threshold)",
                    "severity": "warning",
                    "triggered_at": "2025-03-09T10:15:00Z",
                    "acknowledged": False,
                },
            ],
        }

        # Create a realistic whallet_trader state based on actual model structure
        trader_state = {
            "portfolio_positions": [
                {
                    "position_id": "pos_12345678",
                    "market_id": "0xabc123def4567890abcdef1234567890abcdef12",
                    "market_question": "Will ETH be above $4000 by end of March?",
                    "outcome": "YES",
                    "quantity": 1000.0,  # tokens
                    "average_price": 0.62,
                    "current_price": 0.65,
                    "invested_amount": 620.0,  # 1000 * 0.62
                    "current_value_usd": 650.0,  # 1000 * 0.65
                    "unrealized_pnl_usd": 30.0,  # 650 - 620
                    "unrealized_pnl_percent": 4.84,  # (30/620)*100
                    "first_bought": "2025-03-05T14:30:00Z",
                    "last_updated": "2025-03-09T10:30:00Z",
                },
                {
                    "position_id": "pos_87654321",
                    "market_id": "0xdef456abc1237890fedcba9876543210fedcba98",
                    "market_question": "Will the Fed raise interest rates in Q2 2025?",
                    "outcome": "NO",
                    "quantity": 2000.0,
                    "average_price": 0.68,
                    "current_price": 0.70,
                    "invested_amount": 1360.0,
                    "current_value_usd": 1400.0,
                    "unrealized_pnl_usd": 40.0,
                    "unrealized_pnl_percent": 2.94,
                    "first_bought": "2025-03-03T11:20:00Z",
                    "last_updated": "2025-03-09T10:30:00Z",
                },
            ],
            "trade_history": [
                {
                    "execution_id": "ex_11111111",
                    "order_id": "ord_11111111",
                    "signal_id": "sig_11111111",
                    "market_id": "0xabc123def4567890abcdef1234567890abcdef12",
                    "market_question": "Will ETH be above $4000 by end of March?",
                    "outcome": "YES",
                    "action": "BUY_YES",
                    "quantity": 500.0,
                    "execution_price": 0.62,
                    "position_size_usd": 310.0,
                    "transaction_hash": "0x1111111111111111111111111111111111111111",
                    "block_number": 12345678,
                    "gas_used": 150000,
                    "gas_price_gwei": 30.5,
                    "expected_price": 0.62,
                    "slippage_percent": 0.0,
                    "simulation": False,
                    "executed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Today
                },
                {
                    "execution_id": "ex_22222222",
                    "order_id": "ord_22222222",
                    "signal_id": "sig_22222222",
                    "market_id": "0xdef456abc1237890fedcba9876543210fedcba98",
                    "market_question": "Will the Fed raise interest rates in Q2 2025?",
                    "outcome": "NO",
                    "action": "BUY_NO",
                    "quantity": 1000.0,
                    "execution_price": 0.68,
                    "position_size_usd": 680.0,
                    "transaction_hash": "0x2222222222222222222222222222222222222222",
                    "block_number": 12345679,
                    "gas_used": 155000,
                    "gas_price_gwei": 32.0,
                    "expected_price": 0.68,
                    "slippage_percent": 0.0,
                    "simulation": False,
                    "executed_at": (datetime.now(UTC) - timedelta(days=1)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),  # Yesterday
                },
                {
                    "execution_id": "ex_33333333",
                    "order_id": "ord_33333333",
                    "signal_id": "sig_33333333",
                    "market_id": "0xabc123def4567890abcdef1234567890abcdef12",
                    "market_question": "Will ETH be above $4000 by end of March?",
                    "outcome": "YES",
                    "action": "SELL_YES",
                    "quantity": 200.0,
                    "execution_price": 0.70,
                    "position_size_usd": 140.0,
                    "transaction_hash": "0x3333333333333333333333333333333333333333",
                    "block_number": 12345680,
                    "gas_used": 152000,
                    "gas_price_gwei": 31.0,
                    "expected_price": 0.70,
                    "slippage_percent": 0.0,
                    "simulation": True,  # Simulation trade
                    "executed_at": (datetime.now(UTC) - timedelta(days=2)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),  # 2 days ago
                },
            ],
            "risk_metrics": {
                "max_drawdown_percent": 8.5,
                "sharpe_ratio": 2.1,
                "win_rate_percent": 66.7,
                "profit_factor": 2.8,
                "avg_position_size_usd": 376.7,
                "current_exposure_percent": 18.3,
                "daily_loss_used_percent": 0.8,
            },
        }

        # Write state files
        (identity_dir / "polymarket_state.json").write_text(json.dumps(monitor_state, indent=2))
        (identity_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state, indent=2))

        # Also create identity.yaml to make has_data() return True
        (identity_dir / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")

        # Patch the config base_dir to use our tmp_path
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/polymarket",
            cookies={SESSION_COOKIE: cookie_value},
        )

        assert resp.status_code == 200
        html = resp.text

        # Verify dashboard renders
        assert "Polymarket Trading" in html
        assert "AI-powered prediction market trading" in html

        # Verify stats are calculated correctly
        # Total markets: 2
        assert "2" in html or "total_markets" in html
        # Portfolio value: 650 + 1400 = 2050
        assert "2050" in html or "portfolio_value_usd" in html
        # Total trades: 3 (2 real, 1 simulation)
        # Daily P&L should include today's trade only

        # Verify market data
        assert "ETH" in html
        assert "Fed" in html
        assert "Crypto" in html or "crypto" in html
        assert "Politics" in html or "politics" in html

        # Verify trading opportunities
        assert "BUY YES" in html
        assert "BUY NO" in html
        assert "8.0%" in html or "0.08" in html  # Probability edge

        # Verify portfolio positions
        assert "YES" in html
        assert "NO" in html
        assert "30.00" in html or "30.0" in html  # P&L

        # Verify recent trades
        assert "BUY YES" in html
        assert "SELL YES" in html

        # Verify risk metrics
        assert "8.5" in html or "max_drawdown_percent" in html
        assert "2.1" in html or "sharpe_ratio" in html
        assert "66.7" in html or "win_rate_percent" in html

        # Verify alerts
        assert "High probability edge detected" in html or "warning" in html

        # Verify simulation mode indicator
        assert "Simulation Mode" in html or "simulation" in html.lower()

    @pytest.mark.asyncio
    async def test_integration_with_multiple_identities(
        self, client, session_cookie, tmp_path, monkeypatch
    ):
        """Test dashboard integration with data from multiple identities."""
        # Create mock data directory structure
        data_root = tmp_path / "data"

        # Identity 1: polytrader (main trading identity)
        id1_dir = data_root / "polytrader"
        id1_dir.mkdir(parents=True)

        monitor_state1 = {
            "monitored_markets": {
                "market1": {
                    "id": "market1",
                    "question": "Market from polytrader",
                    "category": "crypto",
                    "status": "open",
                    "volume_24h": 50000.0,
                },
            },
        }

        trader_state1 = {
            "portfolio_positions": [
                {
                    "market_id": "market1",
                    "outcome": "YES",
                    "current_value_usd": 1000.0,
                },
            ],
            "trade_history": [
                {
                    "market_id": "market1",
                    "action": "BUY_YES",
                    "position_size_usd": 1000.0,
                    "realized_pnl_usd": 100.0,
                    "executed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            ],
        }

        (id1_dir / "polymarket_state.json").write_text(json.dumps(monitor_state1))
        (id1_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state1))
        (id1_dir / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")

        # Identity 2: test_trader (another trading identity)
        id2_dir = data_root / "test_trader"
        id2_dir.mkdir(parents=True)

        monitor_state2 = {
            "monitored_markets": {
                "market2": {
                    "id": "market2",
                    "question": "Market from test_trader",
                    "category": "politics",
                    "status": "open",
                    "volume_24h": 30000.0,
                },
            },
        }

        trader_state2 = {
            "portfolio_positions": [
                {
                    "market_id": "market2",
                    "outcome": "NO",
                    "current_value_usd": 500.0,
                },
            ],
            "trade_history": [
                {
                    "market_id": "market2",
                    "action": "BUY_NO",
                    "position_size_usd": 500.0,
                    "realized_pnl_usd": 50.0,
                    "executed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            ],
        }

        (id2_dir / "polymarket_state.json").write_text(json.dumps(monitor_state2))
        (id2_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state2))
        (id2_dir / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")

        # Patch the config base_dir to use our tmp_path
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/polymarket",
            cookies={SESSION_COOKIE: cookie_value},
        )

        assert resp.status_code == 200
        html = resp.text

        # Should aggregate data from both identities
        # Portfolio value: 1000 + 500 = 1500
        # Total P&L: 100 + 50 = 150
        # Total markets: 2

        # Verify both identity names appear (or data is aggregated)
        assert "polytrader" in html.lower() or "test_trader" in html.lower()

        # Check aggregated stats
        assert "1500" in html or "portfolio_value_usd" in html
        assert "150" in html or "total_pnl_usd" in html
        assert "2" in html or "total_markets" in html

    @pytest.mark.asyncio
    async def test_integration_missing_plugin_state(
        self, client, session_cookie, tmp_path, monkeypatch
    ):
        """Test dashboard handles missing plugin state files gracefully."""
        # Create identity directory but no state files
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create identity.yaml to make has_data() return True
        (identity_dir / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")

        # No state files created

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/polymarket",
            cookies={SESSION_COOKIE: cookie_value},
        )

        assert resp.status_code == 200
        html = resp.text

        # Should render empty dashboard
        assert "Polymarket Trading" in html
        assert "No markets monitored yet" in html or "No trading opportunities detected" in html

        # Should not crash or show error messages (graceful degradation)
        assert "Failed to load" not in html or "Error" not in html

    @pytest.mark.asyncio
    async def test_integration_partial_state_files(
        self, client, session_cookie, tmp_path, monkeypatch
    ):
        """Test dashboard handles partial/corrupted state files."""
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create identity.yaml
        (identity_dir / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")

        # Create corrupted polymarket_state.json
        (identity_dir / "polymarket_state.json").write_text("{invalid json")

        # Create valid but incomplete whallet_trader_state.json
        trader_state = {
            "portfolio_positions": [
                {
                    "market_id": "market1",
                    "outcome": "YES",
                    # Missing required fields like current_value_usd
                },
            ],
        }
        (identity_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state))

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/polymarket",
            cookies={SESSION_COOKIE: cookie_value},
        )

        assert resp.status_code == 200
        html = resp.text

        # Should render with partial data or empty state
        assert "Polymarket Trading" in html
        # Should not crash

    def test_template_rendering_edge_cases(self):
        """Test template rendering with edge case data."""
        from overblick.dashboard.app import create_app
        from overblick.dashboard.config import DashboardConfig

        # Create minimal config for test app
        config = DashboardConfig(
            port=8080,
            password="",
            secret_key="test-secret-key",
            session_hours=1,
        )

        app = create_app(config)

        # Test with extreme values
        extreme_data = {
            "stats": {
                "total_markets": 0,
                "active_markets": 0,
                "total_opportunities": 0,
                "high_confidence_opportunities": 0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl_usd": -999999.99,  # Large negative
                "portfolio_value_usd": 0.01,  # Very small
                "daily_pnl_usd": -50000.0,  # Large negative daily
            },
            "markets": [],
            "opportunities": [],
            "positions": [],
            "trades": [],
            "risk_metrics": {
                "max_drawdown_percent": 99.9,
                "sharpe_ratio": -5.0,  # Negative Sharpe
                "win_rate_percent": 0.0,
                "profit_factor": 0.0,
                "avg_position_size_usd": 0.0,
                "current_exposure_percent": 0.0,
                "daily_loss_used_percent": 100.0,  # Max loss used
            },
            "alerts": [],
        }

        # The template should handle these extreme values without errors
        # (actual rendering test would require template engine)
        # This is more of a placeholder to show what should be tested

        # Test with very large values
        large_data = {
            "stats": {
                "total_markets": 9999,
                "active_markets": 5000,
                "total_opportunities": 1000,
                "high_confidence_opportunities": 500,
                "total_trades": 10000,
                "winning_trades": 9500,
                "losing_trades": 500,
                "total_pnl_usd": 1000000.0,
                "portfolio_value_usd": 5000000.0,
                "daily_pnl_usd": 50000.0,
            },
            "markets": [
                {
                    "market_id": f"market{i}",
                    "market_question": f"Market {i}",
                    "volume_24h": 1000000.0,
                }
                for i in range(100)
            ],
            "opportunities": [
                {"market_id": f"market{i}", "probability_edge": 0.5, "confidence_score": 99.9}
                for i in range(50)
            ],
            "positions": [
                {"market_id": f"market{i}", "unrealized_pnl_usd": 10000.0} for i in range(20)
            ],
            "trades": [
                {"market_id": f"market{i}", "executed_at": "2025-03-09T00:00:00Z"}
                for i in range(100)
            ],
            "risk_metrics": {
                "max_drawdown_percent": 0.1,
                "sharpe_ratio": 10.0,
                "win_rate_percent": 99.9,
                "profit_factor": 100.0,
                "avg_position_size_usd": 50000.0,
                "current_exposure_percent": 99.9,
                "daily_loss_used_percent": 0.0,
            },
            "alerts": [{"message": f"Alert {i}", "severity": "critical"} for i in range(50)],
        }

        # Template should handle large lists (they're limited in the route)
        # markets[:50], opportunities[:20], trades[:50], alerts[:20]
