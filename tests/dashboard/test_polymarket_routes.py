"""Tests for the /polymarket dashboard route."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestPolymarketRoute:
    """Tests for the Polymarket Trading dashboard tab."""

    @pytest.mark.asyncio
    async def test_polymarket_page_empty(self, client, session_cookie):
        """Polymarket page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/polymarket",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Polymarket Trading" in resp.text
        # Should show empty states
        assert (
            "No markets monitored yet" in resp.text
            or "No trading opportunities detected" in resp.text
        )

    @pytest.mark.asyncio
    async def test_polymarket_page_with_data(self, client, session_cookie):
        """Polymarket page renders markets and opportunities when data exists."""
        import overblick.dashboard.routes.polymarket_dash as polymarket_mod

        mock_data = {
            "stats": {
                "total_markets": 5,
                "active_markets": 3,
                "total_opportunities": 2,
                "high_confidence_opportunities": 1,
                "total_trades": 10,
                "winning_trades": 7,
                "losing_trades": 3,
                "total_pnl_usd": 1250.50,
                "portfolio_value_usd": 5000.00,
                "daily_pnl_usd": 150.25,
            },
            "markets": [
                {
                    "market_id": "0xabc123",
                    "market_question": "Will ETH be above $4000 by end of March?",
                    "category": "Crypto",
                    "implied_probability": 0.65,
                    "volume_24h": 50000.0,
                    "liquidity": 20000.0,
                    "status": "OPEN",
                },
                {
                    "market_id": "0xdef456",
                    "market_question": "Will the Fed raise rates in Q2?",
                    "category": "Economics",
                    "implied_probability": 0.30,
                    "volume_24h": 30000.0,
                    "liquidity": 15000.0,
                    "status": "RESOLVED",
                },
            ],
            "opportunities": [
                {
                    "market_id": "0xabc123",
                    "market_question": "Will ETH be above $4000 by end of March?",
                    "probability_edge": 0.08,
                    "confidence_score": 85.0,
                    "recommended_outcome": "YES",
                    "market_price": 0.65,
                    "our_probability": 0.73,
                },
                {
                    "market_id": "0xdef456",
                    "market_question": "Will the Fed raise rates in Q2?",
                    "probability_edge": 0.04,
                    "confidence_score": 60.0,
                    "recommended_outcome": "NO",
                    "market_price": 0.30,
                    "our_probability": 0.26,
                },
            ],
            "positions": [
                {
                    "market_id": "0xabc123",
                    "outcome": "YES",
                    "average_price": 0.62,
                    "current_price": 0.65,
                    "unrealized_pnl_usd": 45.00,
                    "unrealized_pnl_percent": 7.5,
                    "current_value_usd": 645.00,
                },
            ],
            "trades": [
                {
                    "market_id": "0xabc123",
                    "action": "BUY_YES",
                    "execution_price": 0.62,
                    "position_size_usd": 600.00,
                    "realized_pnl_usd": 45.00,
                    "executed_at": "2025-03-09T10:30:00Z",
                },
            ],
            "risk_metrics": {
                "max_drawdown_percent": 12.5,
                "sharpe_ratio": 1.8,
                "win_rate_percent": 70.0,
                "profit_factor": 2.5,
                "avg_position_size_usd": 500.0,
                "current_exposure_percent": 15.0,
                "daily_loss_used_percent": 0.5,
            },
            "alerts": [
                {
                    "condition": {"name": "liquidity_drop"},
                    "message": "Liquidity dropped 30% on market 0xabc123",
                    "severity": "warning",
                    "triggered_at": "2025-03-09T09:15:00Z",
                },
            ],
        }

        original = polymarket_mod._load_polymarket_data
        polymarket_mod._load_polymarket_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/polymarket",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "Polymarket Trading" in resp.text
            assert "ETH" in resp.text  # market question
            assert "Crypto" in resp.text
            assert "BUY YES" in resp.text
            assert "45.00" in resp.text  # P&L
        finally:
            polymarket_mod._load_polymarket_data = original

    @pytest.mark.asyncio
    async def test_polymarket_page_data_loading_error(self, client, session_cookie):
        """Polymarket page handles data loading errors gracefully."""
        import overblick.dashboard.routes.polymarket_dash as polymarket_mod

        original = polymarket_mod._load_polymarket_data
        polymarket_mod._load_polymarket_data = lambda req: (_ for _ in []).throw(
            Exception("Test error")
        )
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/polymarket",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "Polymarket Trading" in resp.text
            # Should still render the page even with data error
            assert "Failed to load polymarket data" in resp.text
        finally:
            polymarket_mod._load_polymarket_data = original

    @pytest.mark.asyncio
    async def test_polymarket_requires_auth(self, client):
        """Polymarket page redirects without auth."""
        resp = await client.get("/polymarket", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_polymarket_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has polymarket_monitor."""
        from overblick.dashboard.routes import polymarket_dash

        monkeypatch.chdir(tmp_path)
        assert polymarket_dash.has_data() is False

    def test_polymarket_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when polymarket_monitor is configured."""
        from overblick.dashboard.routes import polymarket_dash

        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "polytrader"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")
        assert polymarket_dash.has_data() is True

    def test_polymarket_has_data_multiple_identities(self, tmp_path, monkeypatch):
        """has_data() returns True when any identity has polymarket_monitor."""
        from overblick.dashboard.routes import polymarket_dash

        monkeypatch.chdir(tmp_path)
        # Create identity without polymarket
        id1 = tmp_path / "overblick" / "identities" / "anomal"
        id1.mkdir(parents=True)
        (id1 / "identity.yaml").write_text("plugins:\n  - ai_digest\n")

        # Create identity with polymarket
        id2 = tmp_path / "overblick" / "identities" / "polytrader"
        id2.mkdir(parents=True)
        (id2 / "identity.yaml").write_text("plugins:\n  - polymarket_monitor\n")

        assert polymarket_dash.has_data() is True

    def test_polymarket_has_data_invalid_yaml(self, tmp_path, monkeypatch):
        """has_data() returns False when identity YAML is invalid."""
        from overblick.dashboard.routes import polymarket_dash

        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "polytrader"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("invalid: yaml: [")
        # Should not crash, returns False
        assert polymarket_dash.has_data() is False


class TestLoadPolymarketData:
    """Unit tests for _load_polymarket_data function."""

    def test_load_empty_data_root(self, tmp_path, monkeypatch):
        """Loading data when data root doesn't exist returns empty structure."""
        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Create a mock request with a non-existent data root
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path / "nonexistent")

        result = _load_polymarket_data(mock_request)

        assert result["stats"]["total_markets"] == 0
        assert result["stats"]["total_trades"] == 0
        assert result["stats"]["portfolio_value_usd"] == 0.0
        assert len(result["markets"]) == 0
        assert len(result["opportunities"]) == 0
        assert len(result["positions"]) == 0
        assert len(result["trades"]) == 0
        assert len(result["alerts"]) == 0

    def test_load_with_monitor_state_only(self, tmp_path, monkeypatch):
        """Loading data with only polymarket_monitor state works correctly."""
        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create polymarket_monitor state
        monitor_state = {
            "monitored_markets": {
                "0xabc123": {
                    "market_id": "0xabc123",
                    "market_question": "Test market 1",
                    "category": "Crypto",
                    "implied_probability": 0.65,
                    "volume_24h": 50000.0,
                    "liquidity": 20000.0,
                    "status": "OPEN",
                },
                "0xdef456": {
                    "market_id": "0xdef456",
                    "market_question": "Test market 2",
                    "category": "Politics",
                    "implied_probability": 0.30,
                    "volume_24h": 30000.0,
                    "liquidity": 15000.0,
                    "status": "CLOSED",
                },
            },
            "recent_opportunities": [
                {
                    "market_id": "0xabc123",
                    "market_question": "Test market 1",
                    "probability_edge": 0.08,
                    "confidence_score": 85.0,
                    "recommended_outcome": "YES",
                    "market_price": 0.65,
                    "our_probability": 0.73,
                },
            ],
            "alerts": [
                {
                    "condition": {"name": "price_drop"},
                    "message": "Price dropped 10%",
                    "severity": "warning",
                    "triggered_at": "2025-03-09T09:15:00Z",
                },
            ],
        }

        (identity_dir / "polymarket_state.json").write_text(json.dumps(monitor_state))

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        result = _load_polymarket_data(mock_request)

        assert result["stats"]["total_markets"] == 2
        assert result["stats"]["active_markets"] == 1  # Only one OPEN
        assert result["stats"]["total_opportunities"] == 1
        assert result["stats"]["high_confidence_opportunities"] == 1  # confidence_score >= 70
        assert len(result["markets"]) == 2
        assert len(result["opportunities"]) == 1
        assert len(result["alerts"]) == 1
        # No trader state, so these should be empty/default
        assert len(result["positions"]) == 0
        assert len(result["trades"]) == 0
        assert result["risk_metrics"]["win_rate_percent"] == 0.0  # No trades

    def test_load_with_trader_state_only(self, tmp_path, monkeypatch):
        """Loading data with only whallet_trader state works correctly."""
        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create whallet_trader state
        trader_state = {
            "portfolio_positions": [
                {
                    "market_id": "0xabc123",
                    "outcome": "YES",
                    "average_price": 0.62,
                    "current_price": 0.65,
                    "unrealized_pnl_usd": 45.00,
                    "unrealized_pnl_percent": 7.5,
                    "current_value_usd": 645.00,
                },
            ],
            "trade_history": [
                {
                    "market_id": "0xabc123",
                    "action": "BUY_YES",
                    "execution_price": 0.62,
                    "position_size_usd": 600.00,
                    "realized_pnl_usd": 45.00,
                    "executed_at": "2025-03-09T10:30:00Z",
                },
                {
                    "market_id": "0xdef456",
                    "action": "SELL_NO",
                    "execution_price": 0.75,
                    "position_size_usd": 400.00,
                    "realized_pnl_usd": -20.00,  # Losing trade
                    "executed_at": "2025-03-08T14:20:00Z",
                },
            ],
            "risk_metrics": {
                "max_drawdown_percent": 12.5,
                "sharpe_ratio": 1.8,
                "win_rate_percent": 50.0,  # 1 win, 1 loss = 50%
                "profit_factor": 2.25,
                "avg_position_size_usd": 500.0,
                "current_exposure_percent": 15.0,
                "daily_loss_used_percent": 0.5,
            },
        }

        (identity_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state))

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        result = _load_polymarket_data(mock_request)

        assert result["stats"]["total_trades"] == 2
        assert result["stats"]["winning_trades"] == 1
        assert result["stats"]["losing_trades"] == 1
        assert result["stats"]["total_pnl_usd"] == 25.0  # 45 - 20
        assert result["stats"]["portfolio_value_usd"] == 645.0
        assert len(result["positions"]) == 1
        assert len(result["trades"]) == 2
        # No monitor state, so these should be empty
        assert len(result["markets"]) == 0
        assert len(result["opportunities"]) == 0
        assert len(result["alerts"]) == 0
        # Risk metrics should be loaded from trader state
        assert result["risk_metrics"]["max_drawdown_percent"] == 12.5
        assert result["risk_metrics"]["sharpe_ratio"] == 1.8
        # win_rate_percent should be 50.0 from trader state, not recalculated
        assert result["risk_metrics"]["win_rate_percent"] == 50.0

    def test_load_with_both_states(self, tmp_path, monkeypatch):
        """Loading data with both monitor and trader states works correctly."""
        from datetime import UTC, datetime, timedelta

        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create polymarket_monitor state
        monitor_state = {
            "monitored_markets": {
                "0xabc123": {
                    "market_id": "0xabc123",
                    "market_question": "Test market",
                    "category": "Crypto",
                    "implied_probability": 0.65,
                    "volume_24h": 50000.0,
                    "liquidity": 20000.0,
                    "status": "OPEN",
                },
            },
            "recent_opportunities": [
                {
                    "market_id": "0xabc123",
                    "market_question": "Test market",
                    "probability_edge": 0.08,
                    "confidence_score": 85.0,
                    "recommended_outcome": "YES",
                    "market_price": 0.65,
                    "our_probability": 0.73,
                },
            ],
        }

        # Create whallet_trader state
        trader_state = {
            "portfolio_positions": [
                {
                    "market_id": "0xabc123",
                    "outcome": "YES",
                    "average_price": 0.62,
                    "current_price": 0.65,
                    "unrealized_pnl_usd": 45.00,
                    "unrealized_pnl_percent": 7.5,
                    "current_value_usd": 645.00,
                },
            ],
            "trade_history": [
                {
                    "market_id": "0xabc123",
                    "action": "BUY_YES",
                    "execution_price": 0.62,
                    "position_size_usd": 600.00,
                    "realized_pnl_usd": 45.00,
                    "executed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            ],
            "risk_metrics": {
                "max_drawdown_percent": 12.5,
                "sharpe_ratio": 1.8,
                "win_rate_percent": 100.0,
                "profit_factor": 2.5,
                "avg_position_size_usd": 600.0,
                "current_exposure_percent": 15.0,
                "daily_loss_used_percent": 0.5,
            },
        }

        (identity_dir / "polymarket_state.json").write_text(json.dumps(monitor_state))
        (identity_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state))

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        result = _load_polymarket_data(mock_request)

        # Verify all data is loaded
        assert result["stats"]["total_markets"] == 1
        assert result["stats"]["active_markets"] == 1
        assert result["stats"]["total_opportunities"] == 1
        assert result["stats"]["high_confidence_opportunities"] == 1
        assert result["stats"]["total_trades"] == 1
        assert result["stats"]["winning_trades"] == 1
        assert result["stats"]["losing_trades"] == 0
        assert result["stats"]["total_pnl_usd"] == 45.0
        assert result["stats"]["portfolio_value_usd"] == 645.0
        # Daily P&L should be 45.0 since trade was today
        assert result["stats"]["daily_pnl_usd"] == 45.0

        # Verify data arrays
        assert len(result["markets"]) == 1
        assert len(result["opportunities"]) == 1
        assert len(result["positions"]) == 1
        assert len(result["trades"]) == 1

        # Risk metrics should come from trader state
        assert result["risk_metrics"]["max_drawdown_percent"] == 12.5
        assert result["risk_metrics"]["sharpe_ratio"] == 1.8
        assert result["risk_metrics"]["win_rate_percent"] == 100.0  # From trader state

    def test_load_corrupted_json(self, tmp_path, monkeypatch):
        """Loading continues when JSON files are corrupted."""
        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create corrupted JSON files
        (identity_dir / "polymarket_state.json").write_text("invalid json {")
        (identity_dir / "whallet_trader_state.json").write_text("also invalid {")

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        # Should not raise exception
        result = _load_polymarket_data(mock_request)

        # Should return empty/default data
        assert result["stats"]["total_markets"] == 0
        assert result["stats"]["total_trades"] == 0
        assert len(result["markets"]) == 0
        assert len(result["opportunities"]) == 0
        assert len(result["positions"]) == 0
        assert len(result["trades"]) == 0

    def test_load_multiple_identities(self, tmp_path, monkeypatch):
        """Loading data from multiple identities aggregates correctly."""
        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"

        # Identity 1: polytrader
        id1_dir = data_root / "polytrader"
        id1_dir.mkdir(parents=True)

        monitor_state1 = {
            "monitored_markets": {
                "0xabc123": {
                    "market_id": "0xabc123",
                    "market_question": "Market from polytrader",
                    "category": "Crypto",
                    "status": "OPEN",
                },
            },
        }

        (id1_dir / "polymarket_state.json").write_text(json.dumps(monitor_state1))

        # Identity 2: another_trader
        id2_dir = data_root / "another_trader"
        id2_dir.mkdir(parents=True)

        trader_state2 = {
            "trade_history": [
                {
                    "market_id": "0xdef456",
                    "action": "BUY_YES",
                    "execution_price": 0.50,
                    "position_size_usd": 1000.00,
                    "realized_pnl_usd": 100.00,
                    "executed_at": "2025-03-09T10:30:00Z",
                },
            ],
        }

        (id2_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state2))

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        result = _load_polymarket_data(mock_request)

        # Should aggregate data from both identities
        assert result["stats"]["total_markets"] == 1
        assert result["stats"]["total_trades"] == 1
        assert result["stats"]["total_pnl_usd"] == 100.0
        assert len(result["markets"]) == 1
        assert len(result["trades"]) == 1

        # Check identity field is added
        assert result["markets"][0]["identity"] == "polytrader"
        assert result["trades"][0]["identity"] == "another_trader"

    def test_daily_pnl_calculation(self, tmp_path, monkeypatch):
        """Daily P&L is correctly calculated for recent trades only."""
        from datetime import UTC, datetime, timedelta

        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create trade history with trades from different days
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        trader_state = {
            "trade_history": [
                {
                    "market_id": "0xabc123",
                    "action": "BUY_YES",
                    "execution_price": 0.60,
                    "position_size_usd": 1000.00,
                    "realized_pnl_usd": 50.00,  # Today's trade
                    "executed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                {
                    "market_id": "0xdef456",
                    "action": "SELL_NO",
                    "execution_price": 0.70,
                    "position_size_usd": 800.00,
                    "realized_pnl_usd": 30.00,  # Yesterday's trade
                    "executed_at": yesterday.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                {
                    "market_id": "0xghi789",
                    "action": "BUY_YES",
                    "execution_price": 0.55,
                    "position_size_usd": 1200.00,
                    "realized_pnl_usd": -20.00,  # Two days ago (loss)
                    "executed_at": two_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            ],
        }

        (identity_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state))

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        result = _load_polymarket_data(mock_request)

        # Total P&L should be 50 + 30 - 20 = 60
        assert result["stats"]["total_pnl_usd"] == 60.0
        # Daily P&L should only include today's trade = 50
        assert result["stats"]["daily_pnl_usd"] == 50.0

    def test_data_sorting(self, tmp_path, monkeypatch):
        """Data is sorted correctly for display."""
        from overblick.dashboard.routes.polymarket_dash import _load_polymarket_data

        # Setup data directory structure
        data_root = tmp_path / "data"
        identity_dir = data_root / "polytrader"
        identity_dir.mkdir(parents=True)

        # Create test data with specific ordering
        monitor_state = {
            "monitored_markets": {
                "market1": {
                    "market_id": "market1",
                    "market_question": "Market 1",
                    "volume_24h": 1000.0,
                },
                "market2": {
                    "market_id": "market2",
                    "market_question": "Market 2",
                    "volume_24h": 5000.0,  # Highest volume
                },
                "market3": {
                    "market_id": "market3",
                    "market_question": "Market 3",
                    "volume_24h": 3000.0,
                },
            },
            "recent_opportunities": [
                {
                    "market_id": "market1",
                    "market_question": "Market 1",
                    "probability_edge": 0.02,
                },
                {
                    "market_id": "market2",
                    "market_question": "Market 2",
                    "probability_edge": 0.08,  # Highest edge
                },
                {
                    "market_id": "market3",
                    "market_question": "Market 3",
                    "probability_edge": 0.05,
                },
            ],
        }

        trader_state = {
            "portfolio_positions": [
                {
                    "market_id": "pos1",
                    "unrealized_pnl_usd": 100.0,  # Highest P&L
                },
                {
                    "market_id": "pos2",
                    "unrealized_pnl_usd": 50.0,
                },
            ],
            "trade_history": [
                {
                    "market_id": "trade1",
                    "executed_at": "2025-03-09T14:00:00Z",  # Most recent
                },
                {
                    "market_id": "trade2",
                    "executed_at": "2025-03-09T10:00:00Z",  # Older
                },
            ],
        }

        (identity_dir / "polymarket_state.json").write_text(json.dumps(monitor_state))
        (identity_dir / "whallet_trader_state.json").write_text(json.dumps(trader_state))

        # Mock request
        mock_request = Mock()
        mock_request.app.state.config = Mock()
        mock_request.app.state.config.base_dir = str(tmp_path)

        result = _load_polymarket_data(mock_request)

        # Markets should be sorted by volume_24h descending
        assert result["markets"][0]["market_id"] == "market2"  # volume: 5000
        assert result["markets"][1]["market_id"] == "market3"  # volume: 3000
        assert result["markets"][2]["market_id"] == "market1"  # volume: 1000

        # Opportunities should be sorted by probability_edge descending
        assert result["opportunities"][0]["market_id"] == "market2"  # edge: 0.08
        assert result["opportunities"][1]["market_id"] == "market3"  # edge: 0.05
        assert result["opportunities"][2]["market_id"] == "market1"  # edge: 0.02

        # Positions should be sorted by unrealized_pnl_usd descending
        assert result["positions"][0]["market_id"] == "pos1"  # P&L: 100
        assert result["positions"][1]["market_id"] == "pos2"  # P&L: 50

        # Trades should be sorted by executed_at descending (most recent first)
        assert result["trades"][0]["market_id"] == "trade1"  # 14:00
        assert result["trades"][1]["market_id"] == "trade2"  # 10:00
