"""Tests for polymarket_monitor plugin — covers all uncovered lines."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.plugins.polymarket_monitor.models import (
    MarketCategory,
    MarketOutcome,
    MarketStatus,
    PolymarketMarket,
    TradingOpportunity,
)
from overblick.plugins.polymarket_monitor.plugin import PolymarketMonitorPlugin


def _make_ctx(tmp_path):
    ctx = MagicMock(spec=PluginContext)
    ctx.identity_name = "polytrader"
    ctx.data_dir = tmp_path / "data"
    ctx.log_dir = tmp_path / "logs"
    ctx.identity = MagicMock()
    ctx.identity.raw_config = {}
    ctx.audit_log = MagicMock()
    ctx.audit_log.log = MagicMock()
    ctx.llm_pipeline = None
    return ctx


def _make_market(**overrides):
    defaults = dict(
        id="mkt_1",
        slug="test-market",
        question="Will it rain?",
        description="A test market about weather",
        category=MarketCategory.OTHER,
        status=MarketStatus.OPEN,
        created_time=datetime.now(),
        end_time=datetime.now() + timedelta(days=14),
        outcomes=[
            MarketOutcome(name="Yes", ticker="YES", price=0.60, volume_24h=5000.0, last_updated=datetime.now()),
            MarketOutcome(name="No", ticker="NO", price=0.40, volume_24h=3000.0, last_updated=datetime.now()),
        ],
        volume_24h=8000.0,
        liquidity=20000.0,
    )
    defaults.update(overrides)
    return PolymarketMarket(**defaults)


class TestSetup:
    @pytest.mark.asyncio
    async def test_should_setup_with_defaults(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        assert plugin._state_file is not None
        assert len(plugin._alert_conditions) == 3

    @pytest.mark.asyncio
    async def test_should_load_config_from_identity(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.identity.raw_config = {
            "polymarket_monitor": {
                "max_markets": 20,
                "min_probability_edge": 0.05,
                "simulation_mode": False,
                "check_interval_minutes": 30,
            }
        }
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        assert plugin._config["max_markets"] == 20
        assert plugin._check_interval_seconds == 30 * 60

    @pytest.mark.asyncio
    async def test_should_keep_existing_alert_conditions(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        from overblick.plugins.polymarket_monitor.models import AlertCondition
        plugin._alert_conditions = [
            AlertCondition(name="custom", condition_type="edge_threshold", parameter=0.10)
        ]
        await plugin.setup()
        assert len(plugin._alert_conditions) == 1


class TestTick:
    @pytest.mark.asyncio
    async def test_should_skip_when_interval_not_elapsed(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 9999999999.0
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_should_perform_scan(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 0
        plugin._perform_market_scan = AsyncMock()
        await plugin.tick()
        plugin._perform_market_scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_scan_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 0
        plugin._perform_market_scan = AsyncMock(side_effect=RuntimeError("fail"))
        await plugin.tick()
        ctx.audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_should_handle_scan_error_without_audit(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.audit_log = None
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._last_check_time = 0
        plugin._perform_market_scan = AsyncMock(side_effect=RuntimeError("fail"))
        await plugin.tick()


class TestPerformMarketScan:
    @pytest.mark.asyncio
    async def test_should_handle_no_markets(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._fetch_markets = AsyncMock(return_value=[])
        await plugin._perform_market_scan()

    @pytest.mark.asyncio
    async def test_should_analyze_tradable_markets(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()

        market = _make_market()
        plugin._fetch_markets = AsyncMock(return_value=[market])
        plugin._analyze_market = AsyncMock(return_value=None)

        await plugin._perform_market_scan()
        plugin._analyze_market.assert_called()

    @pytest.mark.asyncio
    async def test_should_detect_opportunities(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()

        market = _make_market()
        opp = TradingOpportunity(
            market_id="mkt_1",
            market_question="Will it rain?",
            recommended_outcome="YES",
            market_price=0.60,
            our_probability=0.70,
            probability_edge=0.10,
            expected_value=0.15,
            kelly_fraction=0.10,
            confidence_score=85.0,
            volume_score=80.0,
            time_horizon_days=14,
            recommended_action="BUY_YES",
            position_size_percent=2.5,
            urgency="medium",
        )

        plugin._fetch_markets = AsyncMock(return_value=[market])
        plugin._analyze_market = AsyncMock(return_value=opp)

        await plugin._perform_market_scan()
        assert len(plugin._recent_opportunities) == 1
        ctx.audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_should_trigger_alert_for_high_confidence(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()

        market = _make_market()
        opp = TradingOpportunity(
            market_id="mkt_1",
            market_question="Will it rain?",
            recommended_outcome="YES",
            market_price=0.60,
            our_probability=0.70,
            probability_edge=0.10,
            expected_value=0.15,
            kelly_fraction=0.10,
            confidence_score=85.0,
            volume_score=80.0,
            time_horizon_days=14,
            recommended_action="BUY_YES",
            position_size_percent=2.5,
            urgency="high",
        )

        plugin._fetch_markets = AsyncMock(return_value=[market])
        plugin._analyze_market = AsyncMock(return_value=opp)
        plugin._trigger_opportunity_alert = AsyncMock()

        await plugin._perform_market_scan()
        plugin._trigger_opportunity_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_trim_recent_opportunities(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()

        # Fill with 101 opportunities
        for i in range(101):
            plugin._recent_opportunities.append(
                TradingOpportunity(
                    market_id=f"mkt_{i}",
                    market_question=f"Q{i}?",
                    recommended_outcome="YES",
                    market_price=0.50,
                    our_probability=0.60,
                    probability_edge=0.10,
                    expected_value=0.20,
                    kelly_fraction=0.10,
                    confidence_score=50.0,
                    volume_score=50.0,
                    time_horizon_days=14,
                    recommended_action="BUY_YES",
                    position_size_percent=2.0,
                    urgency="low",
                )
            )

        market = _make_market(status=MarketStatus.CLOSED)  # non-tradable so no new opps added
        plugin._fetch_markets = AsyncMock(return_value=[market])
        await plugin._perform_market_scan()
        assert len(plugin._recent_opportunities) == 100

    @pytest.mark.asyncio
    async def test_should_skip_non_tradable_markets(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()

        market = _make_market(status=MarketStatus.CLOSED)
        plugin._fetch_markets = AsyncMock(return_value=[market])
        plugin._analyze_market = AsyncMock()

        await plugin._perform_market_scan()
        plugin._analyze_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_skip_monitored_market_not_in_fetched_list(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._monitored_markets = ["nonexistent_id"]
        market = _make_market(id="other_id")
        plugin._fetch_markets = AsyncMock(return_value=[market])
        # Prevent _update_monitored_markets from cleaning up our ghost ID
        plugin._update_monitored_markets = MagicMock()
        plugin._analyze_market = AsyncMock(return_value=None)
        await plugin._perform_market_scan()

    @pytest.mark.asyncio
    async def test_should_audit_log_scan_without_audit(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.audit_log = None
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._fetch_markets = AsyncMock(return_value=[])
        await plugin._perform_market_scan()


class TestInitClient:
    @pytest.mark.asyncio
    async def test_should_skip_in_simulation_mode(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        await plugin._init_client()
        assert plugin._client is None

    @pytest.mark.asyncio
    async def test_should_create_client_in_real_mode(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._config["simulation_mode"] = False

        import sys
        mock_aiohttp = MagicMock()
        mock_session = MagicMock()
        mock_aiohttp.ClientSession.return_value = mock_session
        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            await plugin._init_client()
        assert plugin._client is not None

    @pytest.mark.asyncio
    async def test_should_raise_on_missing_aiohttp(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._config["simulation_mode"] = False

        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "aiohttp":
                raise ImportError("No module named 'aiohttp'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError):
                await plugin._init_client()


class TestFetchMarkets:
    @pytest.mark.asyncio
    async def test_should_return_empty_in_simulation(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        markets = await plugin._fetch_markets()
        assert markets == []

    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_client(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._config["simulation_mode"] = False
        markets = await plugin._fetch_markets()
        assert markets == []

    @pytest.mark.asyncio
    async def test_should_fetch_from_client(self, tmp_path):
        from overblick.plugins.polymarket_monitor.polymarket_client import PolymarketClient

        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._config["simulation_mode"] = False

        mock_client = MagicMock(spec=PolymarketClient)
        market = _make_market()
        mock_client.get_all_markets = AsyncMock(return_value=[market])
        plugin._client = mock_client

        markets = await plugin._fetch_markets()
        assert len(markets) == 1

    @pytest.mark.asyncio
    async def test_should_handle_api_error(self, tmp_path):
        from overblick.plugins.polymarket_monitor.polymarket_client import (
            PolymarketAPIError,
            PolymarketClient,
        )

        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin.setup()
        plugin._config["simulation_mode"] = False

        mock_client = MagicMock(spec=PolymarketClient)
        mock_client.get_all_markets = AsyncMock(side_effect=PolymarketAPIError("err"))
        plugin._client = mock_client

        markets = await plugin._fetch_markets()
        assert markets == []


class TestIsMarketTradable:
    def test_should_accept_open_liquid_binary_market(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market()
        assert plugin._is_market_tradable(market) is True

    def test_should_reject_closed_market(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(status=MarketStatus.CLOSED)
        assert plugin._is_market_tradable(market) is False

    def test_should_reject_low_volume(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(volume_24h=100.0)
        assert plugin._is_market_tradable(market) is False

    def test_should_reject_no_outcomes(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(outcomes=[])
        assert plugin._is_market_tradable(market) is False

    def test_should_reject_binary_without_yes_no(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(
            outcomes=[
                MarketOutcome(name="A", ticker="A", price=0.5, last_updated=datetime.now()),
                MarketOutcome(name="B", ticker="B", price=0.5, last_updated=datetime.now()),
            ]
        )
        assert plugin._is_market_tradable(market) is False

    def test_should_accept_non_binary_with_outcomes(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(
            outcomes=[
                MarketOutcome(name="A", ticker="A", price=0.33, last_updated=datetime.now()),
                MarketOutcome(name="B", ticker="B", price=0.33, last_updated=datetime.now()),
                MarketOutcome(name="C", ticker="C", price=0.33, last_updated=datetime.now()),
            ]
        )
        assert plugin._is_market_tradable(market) is True


class TestUpdateMonitoredMarkets:
    def test_should_sort_by_volume(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        markets = [
            _make_market(id="low", volume_24h=100.0),
            _make_market(id="high", volume_24h=10000.0),
        ]
        plugin._update_monitored_markets(markets)
        assert set(plugin._monitored_markets) == {"low", "high"}

    def test_should_preserve_existing_markets(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._monitored_markets = ["high"]
        markets = [
            _make_market(id="high", volume_24h=10000.0),
            _make_market(id="new", volume_24h=5000.0),
        ]
        plugin._update_monitored_markets(markets)
        assert "high" in plugin._monitored_markets
        assert "new" in plugin._monitored_markets


class TestAnalyzeMarket:
    @pytest.mark.asyncio
    async def test_should_return_none_for_non_binary(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(outcomes=[
            MarketOutcome(name="A", ticker="A", price=0.33, last_updated=datetime.now()),
            MarketOutcome(name="B", ticker="B", price=0.33, last_updated=datetime.now()),
            MarketOutcome(name="C", ticker="C", price=0.33, last_updated=datetime.now()),
        ])
        result = await plugin._analyze_market(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_missing_outcomes(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(outcomes=[
            MarketOutcome(name="A", ticker="A", price=0.5, last_updated=datetime.now()),
            MarketOutcome(name="B", ticker="B", price=0.5, last_updated=datetime.now()),
        ])
        result = await plugin._analyze_market(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_probability(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._estimate_probability = AsyncMock(return_value=None)
        market = _make_market()
        result = await plugin._analyze_market(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_when_edge_too_small(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        # Market price = 0.60, our estimate = 0.605 -> edge = 0.005 < 0.01
        plugin._estimate_probability = AsyncMock(return_value=0.605)
        market = _make_market()
        result = await plugin._analyze_market(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_opportunity_buy_yes(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._estimate_probability = AsyncMock(return_value=0.75)
        market = _make_market()
        result = await plugin._analyze_market(market)
        assert result is not None
        assert result.recommended_outcome == "YES"
        assert result.recommended_action == "BUY_YES"

    @pytest.mark.asyncio
    async def test_should_return_opportunity_buy_no(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._estimate_probability = AsyncMock(return_value=0.40)
        market = _make_market()
        result = await plugin._analyze_market(market)
        assert result is not None
        assert result.recommended_outcome == "NO"
        assert result.recommended_action == "BUY_NO"

    @pytest.mark.asyncio
    async def test_should_set_urgency_based_on_edge(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        # High edge + high confidence
        plugin._estimate_probability = AsyncMock(return_value=0.85)
        market = _make_market(
            outcomes=[
                MarketOutcome(name="Yes", ticker="YES", price=0.60, volume_24h=50000.0, last_updated=datetime.now()),
                MarketOutcome(name="No", ticker="NO", price=0.40, volume_24h=30000.0, last_updated=datetime.now()),
            ],
            volume_24h=100000.0,
            liquidity=100000.0,
        )
        result = await plugin._analyze_market(market)
        assert result is not None
        assert result.urgency in ("high", "critical")

    @pytest.mark.asyncio
    async def test_should_set_critical_urgency(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        # our_probability = 0.90, market_price = 0.60 -> edge = 0.30 > 0.15
        plugin._estimate_probability = AsyncMock(return_value=0.90)
        # Use high volume to boost confidence > 80
        market = _make_market(
            outcomes=[
                MarketOutcome(name="Yes", ticker="YES", price=0.60, volume_24h=500000.0, last_updated=datetime.now()),
                MarketOutcome(name="No", ticker="NO", price=0.40, volume_24h=300000.0, last_updated=datetime.now()),
            ],
            volume_24h=800000.0,
            liquidity=500000.0,
            end_time=datetime.now() + timedelta(days=5),  # imminent -> +20 confidence
        )
        result = await plugin._analyze_market(market)
        assert result is not None
        assert result.urgency == "critical"

    @pytest.mark.asyncio
    async def test_should_handle_market_without_end_time(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._estimate_probability = AsyncMock(return_value=0.75)
        market = _make_market(end_time=None)
        result = await plugin._analyze_market(market)
        assert result is not None
        assert result.time_horizon_days == 30

    @pytest.mark.asyncio
    async def test_should_handle_no_price_edge_for_no_outcome(self, tmp_path):
        """When our_probability < market_price, we recommend NO outcome."""
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        # market_price = 0.60, our_prob = 0.40 -> edge = 0.20, recommend NO
        # no_price = 0.40, no_prob = 0.60
        plugin._estimate_probability = AsyncMock(return_value=0.40)
        market = _make_market(
            outcomes=[
                MarketOutcome(name="Yes", ticker="YES", price=0.95, volume_24h=5000.0, last_updated=datetime.now()),
                MarketOutcome(name="No", ticker="NO", price=0.05, volume_24h=3000.0, last_updated=datetime.now()),
            ],
        )
        result = await plugin._analyze_market(market)
        assert result is not None
        assert result.recommended_outcome == "NO"


class TestEstimateProbability:
    @pytest.mark.asyncio
    async def test_should_fallback_to_implied_probability(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(implied_probability=0.65)
        result = await plugin._estimate_probability(market)
        assert result == 0.65

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_fallback(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(implied_probability=None)
        result = await plugin._estimate_probability(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_use_llm_pipeline(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        mock_pipeline = AsyncMock()
        mock_result = MagicMock()
        mock_result.blocked = False
        mock_result.content = "72"
        mock_pipeline.chat = AsyncMock(return_value=mock_result)
        ctx.llm_pipeline = mock_pipeline

        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market()
        result = await plugin._estimate_probability(market)
        assert result == 0.72

    @pytest.mark.asyncio
    async def test_should_handle_invalid_llm_response(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        mock_pipeline = AsyncMock()
        mock_result = MagicMock()
        mock_result.blocked = False
        mock_result.content = "not a number"
        mock_pipeline.chat = AsyncMock(return_value=mock_result)
        ctx.llm_pipeline = mock_pipeline

        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(implied_probability=0.55)
        result = await plugin._estimate_probability(market)
        assert result == 0.55  # Falls back

    @pytest.mark.asyncio
    async def test_should_handle_blocked_response(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        mock_pipeline = AsyncMock()
        mock_result = MagicMock()
        mock_result.blocked = True
        mock_result.content = None
        mock_pipeline.chat = AsyncMock(return_value=mock_result)
        ctx.llm_pipeline = mock_pipeline

        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(implied_probability=0.55)
        result = await plugin._estimate_probability(market)
        assert result == 0.55

    @pytest.mark.asyncio
    async def test_should_handle_llm_exception(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        ctx.llm_pipeline = mock_pipeline

        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(implied_probability=0.55)
        result = await plugin._estimate_probability(market)
        assert result == 0.55

    @pytest.mark.asyncio
    async def test_should_reject_out_of_range_probability(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        mock_pipeline = AsyncMock()
        mock_result = MagicMock()
        mock_result.blocked = False
        mock_result.content = "150"  # Out of range
        mock_pipeline.chat = AsyncMock(return_value=mock_result)
        ctx.llm_pipeline = mock_pipeline

        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(implied_probability=0.55)
        result = await plugin._estimate_probability(market)
        assert result == 0.55


class TestBuildMarketContext:
    def test_should_build_context_with_all_fields(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market()
        context = plugin._build_market_context(market)
        assert "Description:" in context
        assert "Category:" in context
        assert "Days until resolution:" in context
        assert "YES:" in context

    def test_should_handle_no_description(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(description=None)
        context = plugin._build_market_context(market)
        assert "Description:" not in context

    def test_should_handle_no_end_time(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(end_time=None)
        context = plugin._build_market_context(market)
        assert "Days until resolution:" not in context


class TestKellyFraction:
    def test_should_calculate_kelly(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        result = plugin._calculate_kelly_fraction(0.6, 2.0, 0.4)
        assert result > 0

    def test_should_return_zero_when_payout_low(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        result = plugin._calculate_kelly_fraction(0.5, 1.0, 0.5)
        assert result == 0.0

    def test_should_cap_at_one(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        result = plugin._calculate_kelly_fraction(0.99, 100.0, 0.01)
        assert result <= 1.0


class TestConfidenceScore:
    def test_should_increase_for_imminent_events(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(end_time=datetime.now() + timedelta(days=3))
        score = plugin._calculate_confidence_score(market, 0.5)
        assert score > 50

    def test_should_decrease_for_distant_events(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(end_time=datetime.now() + timedelta(days=400))
        score = plugin._calculate_confidence_score(market, 0.5)
        assert score < 60

    def test_should_adjust_for_tight_spread(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(
            outcomes=[
                MarketOutcome(name="Yes", ticker="YES", price=0.52, last_updated=datetime.now()),
                MarketOutcome(name="No", ticker="NO", price=0.48, last_updated=datetime.now()),
            ],
            end_time=None,
            volume_24h=300000.0,  # high volume to get positive adjustment
        )
        score = plugin._calculate_confidence_score(market, 0.5)
        # tight spread (0.04 < 0.1) adds 10, high volume adds positive
        assert score >= 50

    def test_should_adjust_for_wide_spread(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(
            outcomes=[
                MarketOutcome(name="Yes", ticker="YES", price=0.80, last_updated=datetime.now()),
                MarketOutcome(name="No", ticker="NO", price=0.20, last_updated=datetime.now()),
            ],
            end_time=None,
        )
        score = plugin._calculate_confidence_score(market, 0.5)
        # wide spread -> lower score

    def test_should_handle_no_end_time(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(end_time=None, outcomes=[])
        score = plugin._calculate_confidence_score(market, 0.5)
        assert 0 <= score <= 100

    def test_should_adjust_for_30_day_window(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        market = _make_market(
            end_time=datetime.now() + timedelta(days=20),
            volume_24h=300000.0,
        )
        score = plugin._calculate_confidence_score(market, 0.5)
        # 20 days (< 30) adds +10, high volume adds positive
        assert score >= 50


class TestTriggerOpportunityAlert:
    @pytest.mark.asyncio
    async def test_should_add_alert(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        opp = TradingOpportunity(
            market_id="mkt_1",
            market_question="Will it rain? " * 20,
            recommended_outcome="YES",
            market_price=0.60,
            our_probability=0.75,
            probability_edge=0.15,
            expected_value=0.25,
            kelly_fraction=0.10,
            confidence_score=85.0,
            volume_score=80.0,
            time_horizon_days=14,
            recommended_action="BUY_YES",
            position_size_percent=2.5,
            urgency="high",
        )
        await plugin._trigger_opportunity_alert(opp)
        assert len(plugin._active_alerts) == 1
        assert plugin._active_alerts[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_should_trim_alerts(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        from overblick.plugins.polymarket_monitor.models import Alert, AlertCondition

        for _ in range(51):
            plugin._active_alerts.append(MagicMock())

        opp = TradingOpportunity(
            market_id="mkt_1",
            market_question="Test?",
            recommended_outcome="YES",
            market_price=0.60,
            our_probability=0.75,
            probability_edge=0.15,
            expected_value=0.25,
            kelly_fraction=0.10,
            confidence_score=85.0,
            volume_score=80.0,
            time_horizon_days=14,
            recommended_action="BUY_YES",
            position_size_percent=2.5,
            urgency="medium",
        )
        await plugin._trigger_opportunity_alert(opp)
        assert len(plugin._active_alerts) == 50

    @pytest.mark.asyncio
    async def test_should_set_info_severity_for_non_high(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        opp = TradingOpportunity(
            market_id="mkt_1",
            market_question="Test?",
            recommended_outcome="YES",
            market_price=0.60,
            our_probability=0.75,
            probability_edge=0.15,
            expected_value=0.25,
            kelly_fraction=0.10,
            confidence_score=85.0,
            volume_score=80.0,
            time_horizon_days=14,
            recommended_action="BUY_YES",
            position_size_percent=2.5,
            urgency="medium",
        )
        await plugin._trigger_opportunity_alert(opp)
        assert plugin._active_alerts[0].severity == "info"


class TestLoadSaveState:
    def test_should_load_state(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "monitored_markets": ["mkt_1"],
            "recent_opportunities": [],
            "last_check_time": 100.0,
        }))
        plugin._state_file = state_file
        plugin._load_state()
        assert plugin._monitored_markets == ["mkt_1"]
        assert plugin._last_check_time == 100.0

    def test_should_handle_corrupt_state(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid")
        plugin._state_file = state_file
        plugin._load_state()
        assert plugin._monitored_markets == []

    def test_should_skip_when_no_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._state_file = tmp_path / "nonexistent.json"
        plugin._load_state()

    def test_should_skip_when_state_file_none(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._state_file = None
        plugin._load_state()

    def test_should_save_state(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        state_file = tmp_path / "state.json"
        plugin._state_file = state_file
        plugin._last_check_time = 200.0
        plugin._save_state()
        assert state_file.exists()

    def test_should_skip_save_when_none(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._state_file = None
        plugin._save_state()

    def test_should_handle_save_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        plugin._state_file = MagicMock()
        plugin._state_file.write_text = MagicMock(side_effect=OSError("disk"))
        plugin._save_state()


class TestCheckAlertConditions:
    @pytest.mark.asyncio
    async def test_should_pass(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = PolymarketMonitorPlugin(ctx)
        await plugin._check_alert_conditions([])
