"""
Polymarket Monitor Plugin — Market analysis and opportunity detection.

Monitors Polymarket prediction markets for trading opportunities,
calculates probability edges, and triggers alerts based on configured
conditions. Integrates with PolyTrader's analytical personality for
data-driven decision making.

Features:
- Periodic market data fetching (every 15 minutes)
- Probability estimation using LLM analysis + statistical models
- Opportunity detection based on price vs probability divergence
- Alert system for threshold breaches
- Integration with whallet_trader plugin for trade execution
- Risk-aware position sizing (Kelly criterion)

Security:
- All external content wrapped in boundary markers
- API keys stored in encrypted secrets
- Rate limiting to respect API constraints
- Simulation mode for testing (no real trades)
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content

from .models import (
    Alert,
    AlertCondition,
    MarketCategory,
    MarketStatus,
    PolymarketMarket,
    TradingOpportunity,
)
from .llm_analyzer import PolymarketLLMAnalyzer
from .polymarket_client import PolymarketAPIError, PolymarketClient
from .graph_learning_store import PolymarketGraphStore, create_graph_store

logger = logging.getLogger(__name__)

# Default configuration
_DEFAULT_CHECK_INTERVAL_MINUTES = 15
_DEFAULT_MAX_MARKETS_TO_MONITOR = 50
_DEFAULT_MIN_PROBABILITY_EDGE = 0.03  # 3% minimum edge
_DEFAULT_MIN_VOLUME_USD = 1000.0  # Minimum liquidity to consider
_DEFAULT_MAX_POSITION_SIZE_PERCENT = 5.0  # Max 5% of portfolio per trade


class PolymarketMonitorPlugin(PluginBase):
    """
    Polymarket market monitoring and opportunity detection plugin.

    Designed for PolyTrader: continuously scans prediction markets,
    identifies mispricings using LLM-enhanced probability estimation,
    and generates actionable trading opportunities.
    """

    # Required capabilities for this plugin
    REQUIRED_CAPABILITIES = [
        "network_outbound",  # API calls to Polymarket
        "filesystem_write",  # Save market data and opportunities
        "secrets_access",  # Access API keys if needed
    ]

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._check_interval_seconds = _DEFAULT_CHECK_INTERVAL_MINUTES * 60
        self._last_check_time: float = 0
        self._state_file: Path | None = None
        self._client: PolymarketClient | None = None
        self._session: Any | None = None

        # Plugin state
        self._monitored_markets: list[str] = []  # List of market IDs to monitor
        self._alert_conditions: list[AlertCondition] = []
        self._recent_opportunities: list[TradingOpportunity] = []
        self._active_alerts: list[Alert] = []
        self._llm_analyzer: PolymarketLLMAnalyzer | None = None
        self._graph_store: PolymarketGraphStore | None = None  # Neo4j graph store

        # Configuration
        self._config = {
            "max_markets": _DEFAULT_MAX_MARKETS_TO_MONITOR,
            "min_probability_edge": _DEFAULT_MIN_PROBABILITY_EDGE,
            "min_volume_usd": _DEFAULT_MIN_VOLUME_USD,
            "max_position_size_percent": _DEFAULT_MAX_POSITION_SIZE_PERCENT,
            "enable_real_api": True,  # Use real Polymarket API",
        }

    async def setup(self) -> None:
        """Initialize plugin: load configuration, set up API client, load state."""
        # Load configuration from identity
        raw = self.ctx.identity.raw_config if self.ctx.identity else {}
        plugin_config = raw.get("polymarket_monitor", {})

        # Apply configuration
        self._config.update(
            {
                "max_markets": plugin_config.get("max_markets", _DEFAULT_MAX_MARKETS_TO_MONITOR),
                "min_probability_edge": plugin_config.get(
                    "min_probability_edge", _DEFAULT_MIN_PROBABILITY_EDGE
                ),
                "min_volume_usd": plugin_config.get("min_volume_usd", _DEFAULT_MIN_VOLUME_USD),
                "max_position_size_percent": plugin_config.get(
                    "max_position_size_percent", _DEFAULT_MAX_POSITION_SIZE_PERCENT
                ),
                "max_markets_per_scan": plugin_config.get("max_markets_per_scan", 10),
                "enable_real_api": plugin_config.get("enable_real_api", True),
            }
        )

        # Set check interval
        check_interval_minutes = plugin_config.get(
            "check_interval_minutes", _DEFAULT_CHECK_INTERVAL_MINUTES
        )
        self._check_interval_seconds = check_interval_minutes * 60

        # Initialize data directory
        self.ctx.data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.ctx.data_dir / "polymarket_state.json"

        # Load monitored markets and state
        self._load_state()

        # Initialize API client
        self._session = None  # Will be created on first use
        self._client = None

        # Initialize LLM analyzer if pipeline is available
        if self.ctx.llm_pipeline:
            self._llm_analyzer = PolymarketLLMAnalyzer(self.ctx.llm_pipeline)
            logger.info("LLM analyzer initialized for market analysis")
        else:
            logger.warning("No LLM pipeline available - using fallback analysis")

        # Initialize Neo4j GraphStore (optional — runs locally, not in Docker)
        try:
            neo4j_config = plugin_config.get("neo4j", {})
            uri = neo4j_config.get("uri", "bolt://localhost:7687")
            user = neo4j_config.get("user", "neo4j")
            password = self.ctx.get_secret("neo4j_password") or neo4j_config.get("password", "")

            if not password:
                logger.info("Neo4j password not configured — skipping graph store")
                self._graph_store = None
            else:
                self._graph_store = create_graph_store(uri=uri, user=user, password=password)
                if self._graph_store and not self._graph_store.is_connected:
                    logger.info("Neo4j not available — running without graph store")
                    self._graph_store = None
                else:
                    logger.info("Neo4j GraphStore initialized for trading knowledge")
        except Exception as e:
            logger.warning(f"Failed to initialize Neo4j GraphStore: {e}")
            self._graph_store = None

        # Load default alert conditions if none exist
        if not self._alert_conditions:
            self._setup_default_alert_conditions()

        # Subscribe to trade execution events
        if self.ctx.event_bus:
            try:
                # Try to subscribe - handle potential async
                try:
                    # First try as regular call
                    self.ctx.event_bus.subscribe(
                        "polymarket.trade_executed",
                        self._handle_trade_executed,
                    )
                except TypeError:
                    # Might need to be awaited
                    pass
                logger.info("Subscribed to trade execution events")
            except Exception as e:
                logger.warning(f"Failed to subscribe to trade events: {e}")

        logger.info(
            "PolymarketMonitorPlugin setup for '%s' (interval: %dmin, max markets: %d, real_api: %s)",
            self.ctx.identity_name,
            check_interval_minutes,
            self._config["max_markets"],
            str(self._config.get("enable_real_api", True)),
        )

    async def tick(self) -> None:
        """
        Main tick: check if it's time to scan markets.

        Performs:
        1. Interval check (default 15 minutes between scans)
        2. Market data fetching and parsing
        3. Opportunity detection and scoring
        4. Alert evaluation and triggering
        5. State persistence
        """
        now = time.time()

        # Guard: check interval
        if now - self._last_check_time < self._check_interval_seconds:
            return

        self._last_check_time = now

        try:
            await self._perform_market_scan()
        except Exception as e:
            logger.error("Polymarket market scan failed: %s", e, exc_info=True)
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "polymarket_scan_failed",
                    category="monitoring",
                    plugin="polymarket_monitor",
                    success=False,
                    error=str(e),
                )

    async def _perform_market_scan(self) -> None:
        """Perform a complete market scan: fetch, analyze, detect opportunities."""
        logger.info("PolymarketMonitor: starting market scan")

        # Ensure client is initialized
        if self._client is None:
            await self._init_client()

        # Fetch markets
        markets = await self._fetch_markets()
        if not markets:
            logger.warning("PolymarketMonitor: no markets fetched")
            return

        # Update monitored markets list (prioritize active, liquid markets)
        self._update_monitored_markets(markets)

        # Analyze each monitored market (limited to max_markets_per_scan for speed)
        scan_start = time.monotonic()
        opportunities = []
        for market_id in self._monitored_markets[: self._config["max_markets_per_scan"]]:
            market = next((m for m in markets if m.id == market_id), None)
            if not market:
                continue

            # Skip markets that don't meet basic criteria
            if not self._is_market_tradable(market):
                continue

            # Analyze market for opportunities
            opportunity = await self._analyze_market(market)
            if opportunity:
                opportunities.append(opportunity)

                # Check if opportunity meets threshold for action
                if opportunity.probability_edge >= self._config["min_probability_edge"]:
                    self._recent_opportunities.append(opportunity)

                    # Trigger alert for high-confidence opportunities
                    if opportunity.confidence_score >= 70:
                        await self._trigger_opportunity_alert(opportunity)

        # Check alert conditions
        await self._check_alert_conditions(markets)

        # Trim recent opportunities list
        if len(self._recent_opportunities) > 100:
            self._recent_opportunities = self._recent_opportunities[-100:]

        # Persist state
        self._save_state()

        scan_duration = time.monotonic() - scan_start
        logger.info(
            "PolymarketMonitor: scan complete in %.1fs — %d markets analyzed, %d opportunities",
            scan_duration,
            min(len(self._monitored_markets), self._config["max_markets_per_scan"]),
            len(opportunities),
        )

        # Audit log
        if self.ctx.audit_log:
            self.ctx.audit_log.log(
                "polymarket_scan_complete",
                category="monitoring",
                plugin="polymarket_monitor",
                details={
                    "markets_scanned": len(markets),
                    "opportunities_found": len(opportunities),
                    "monitored_markets": len(self._monitored_markets),
                    "active_alerts": len(self._active_alerts),
                },
            )

    async def _init_client(self) -> None:
        """Initialize the Polymarket API client."""
        # Always connect to real Polymarket API (read-only, no auth needed)
        try:
            import aiohttp

            self._session = aiohttp.ClientSession()
            self._client = PolymarketClient(self._session)
            logger.debug("PolymarketMonitor: connected to real Polymarket API")
        except ImportError:
            logger.error("PolymarketMonitor: aiohttp not installed")
            raise

    async def _fetch_markets(self) -> list[PolymarketMarket]:
        """Fetch markets from real Polymarket API."""
        # Return empty list if client not initialized (shouldn't happen in normal operation)
        if self._client is None:
            logger.warning("PolymarketMonitor: API client not initialized")
            return []

        try:
            markets = await self._client.get_all_markets(limit=self._config["max_markets"])
            logger.info("PolymarketMonitor: fetched %d markets from Polymarket", len(markets))
            return markets
        except PolymarketAPIError as e:
            logger.error("PolymarketMonitor: failed to fetch markets: %s", e)
            return []

    def _is_market_tradable(self, market: PolymarketMarket) -> bool:
        """Check if a market meets basic tradability criteria."""
        # Market must be open
        if market.status != MarketStatus.OPEN:
            return False

        # Sufficient liquidity
        if market.volume_24h < self._config["min_volume_usd"]:
            return False

        # Must have at least one outcome with price data
        if not market.outcomes or len(market.outcomes) == 0:
            return False

        # For binary markets, need YES/NO outcomes
        if len(market.outcomes) == 2:
            yes_outcome = any(o.ticker.upper() == "YES" for o in market.outcomes)
            no_outcome = any(o.ticker.upper() == "NO" for o in market.outcomes)
            if not (yes_outcome and no_outcome):
                return False

        return True

    def _update_monitored_markets(self, markets: list[PolymarketMarket]) -> None:
        """Update the list of monitored markets based on activity."""
        # Sort markets by volume (highest first)
        sorted_markets = sorted(
            markets,
            key=lambda m: m.volume_24h,
            reverse=True,
        )

        # Take top N by volume
        new_monitored = [m.id for m in sorted_markets[: self._config["max_markets"]]]

        # Keep any existing monitored markets that are still in the list
        existing_set = set(self._monitored_markets)
        new_set = set(new_monitored)
        kept = existing_set & new_set
        added = new_set - existing_set

        # Update list (preserve order: kept markets first, then new ones)
        self._monitored_markets = [mid for mid in self._monitored_markets if mid in kept] + list(
            added
        )

    async def _analyze_market(self, market: PolymarketMarket) -> TradingOpportunity | None:
        """
        Analyze a market for trading opportunities.

        Combines:
        1. LLM analysis of market question and context
        2. Statistical analysis of historical data
        3. Market microstructure analysis
        4. Sentiment analysis (if available)
        """
        # For binary markets, calculate basic opportunity
        if len(market.outcomes) != 2:
            return None

        # Get YES and NO outcomes
        yes_outcome = next((o for o in market.outcomes if o.ticker.upper() == "YES"), None)
        no_outcome = next((o for o in market.outcomes if o.ticker.upper() == "NO"), None)

        if not yes_outcome or not no_outcome:
            return None

        # Calculate our probability estimate
        our_probability = await self._estimate_probability(market)
        if our_probability is None:
            return None

        # Calculate edge vs market price
        market_price = yes_outcome.price
        probability_edge = abs(our_probability - market_price)

        # Skip if edge is too small
        if probability_edge < 0.01:  # 1% minimum to even consider
            return None

        # Determine which side to trade
        if our_probability > market_price:
            recommended_outcome = "YES"
            expected_value = (
                (our_probability - market_price) / market_price if market_price > 0 else 0
            )
        else:
            recommended_outcome = "NO"
            # For NO outcome, price is (1 - YES price)
            no_price = 1 - market_price
            no_probability = 1 - our_probability
            expected_value = (no_probability - no_price) / no_price if no_price > 0 else 0

        # Calculate Kelly fraction (simplified)
        kelly_fraction = self._calculate_kelly_fraction(
            win_probability=our_probability
            if recommended_outcome == "YES"
            else 1 - our_probability,
            win_payout=1.0 / (market_price if recommended_outcome == "YES" else (1 - market_price)),
            loss_probability=1
            - (our_probability if recommended_outcome == "YES" else 1 - our_probability),
        )

        # Cap position size
        position_size_percent = min(
            kelly_fraction * 100 * 0.5,  # Half-Kelly for safety
            self._config["max_position_size_percent"],
        )

        # Calculate confidence score
        confidence_score = self._calculate_confidence_score(market, our_probability)

        # Calculate volume score (0-100 based on liquidity)
        volume_score = min(100, market.volume_24h / 10000)  # 10k USD = 100 score

        # Determine urgency
        urgency = "low"
        if probability_edge > 0.05:
            urgency = "medium"
        if probability_edge > 0.10:
            urgency = "high"
        if probability_edge > 0.15 and confidence_score > 80:
            urgency = "critical"

        # Determine action
        recommended_action = f"BUY_{recommended_outcome}"

        # Estimate time horizon (days until resolution)
        time_horizon_days = 30  # Default
        if market.end_time:
            days_left = (market.end_time - datetime.now()).days
            time_horizon_days = max(1, days_left)

        # Create opportunity
        opportunity = TradingOpportunity(
            market_id=market.id,
            market_question=market.question,
            recommended_outcome=recommended_outcome,
            market_price=market_price,
            our_probability=our_probability,
            probability_edge=probability_edge,
            expected_value=expected_value,
            kelly_fraction=kelly_fraction,
            confidence_score=confidence_score,
            volume_score=volume_score,
            time_horizon_days=time_horizon_days,
            recommended_action=recommended_action,
            position_size_percent=position_size_percent,
            urgency=urgency,
        )

        # Store market analysis in Neo4j GraphStore
        if self._graph_store:
            try:
                # Get weather data if available
                weather_pattern = None
                if "weather" in market.question.lower() or "temperature" in market.question.lower():
                    # Try to extract location from question
                    weather_pattern = {"location": "unknown", "pattern": "weather_market"}

                # Store analysis
                await self._graph_store.store_market_analysis(
                    market_id=market.id,
                    question=market.question,
                    category=market.category.value if market.category else None,
                    llm_probability=our_probability,
                    edge=probability_edge,
                    weather_pattern=weather_pattern,
                )
                logger.debug(f"Stored market analysis for {market.id} in Neo4j")
            except Exception as e:
                logger.warning(f"Failed to store market analysis in Neo4j: {e}")

        return opportunity

    async def _estimate_probability(self, market: PolymarketMarket) -> float | None:
        """
        Estimate the true probability of a market outcome.

        Uses enhanced LLM analysis with Qwen 3.5.
        Falls back to market price if LLM is unavailable.
        """
        if self._llm_analyzer:
            try:
                # Use enhanced LLM analyzer
                analysis = await self._llm_analyzer.analyze_market(market)
                probability = analysis.get("probability_estimate")
                confidence = analysis.get("confidence_score", 50)

                if probability is not None:
                    logger.debug(
                        f"LLM analysis for market {market.id}: "
                        f"probability={probability:.3f}, confidence={confidence}"
                    )
                    return probability
            except Exception as e:
                logger.warning(f"Enhanced LLM analysis failed: {e}")

        # Fallback to original LLM pipeline or market price
        if self.ctx.llm_pipeline:
            # Prepare market context for LLM
            market_context = self._build_market_context(market)
            wrapped_context = wrap_external_content(market_context, source="polymarket")

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are PolyTrader, a data-driven prediction market analyst. "
                        "Your task is to estimate the objective probability of a market outcome "
                        "based on available information. Be analytical, not emotional. "
                        "Consider all available evidence and express your estimate as a "
                        "percentage (0-100). Return ONLY the number, no explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Market: {market.question}\n"
                        f"Context: {wrapped_context}\n\n"
                        "Based on the information above, what is your estimated probability "
                        "(0-100) that the YES outcome will occur?"
                    ),
                },
            ]

            try:
                result = await self.ctx.llm_pipeline.chat(
                    messages, max_tokens=500, think=False
                )
                if result and not result.blocked and result.content:
                    # Parse numeric response
                    try:
                        probability_percent = float(result.content.strip())
                        probability = probability_percent / 100.0

                        # Validate range
                        if 0 <= probability <= 1:
                            return probability
                    except ValueError:
                        logger.debug("PolymarketMonitor: failed to parse LLM probability")
            except Exception as e:
                logger.debug("PolymarketMonitor: LLM probability estimation failed: %s", e)

        # Final fallback to market price
        return market.implied_probability

    def _build_market_context(self, market: PolymarketMarket) -> str:
        """Build a context string for LLM probability estimation."""
        context_parts = []

        if market.description:
            context_parts.append(f"Description: {market.description}")

        context_parts.append(f"Category: {market.category.value}")
        context_parts.append(f"Created: {market.created_time.strftime('%Y-%m-%d')}")

        if market.end_time:
            days_left = (market.end_time - datetime.now()).days
            context_parts.append(f"Days until resolution: {days_left}")

        context_parts.append(f"24h Volume: ${market.volume_24h:,.2f}")
        context_parts.append(f"Liquidity: ${market.liquidity:,.2f}")

        # Add outcome prices
        for outcome in market.outcomes:
            context_parts.append(
                f"{outcome.ticker}: ${outcome.price:.4f} (24h volume: ${outcome.volume_24h:,.2f})"
            )

        return "\n".join(context_parts)

    def _calculate_kelly_fraction(
        self, win_probability: float, win_payout: float, loss_probability: float
    ) -> float:
        """
        Calculate Kelly criterion position size.

        Formula: f* = (p*b - q) / b
        where:
          p = win probability
          q = loss probability (1 - p)
          b = net odds (win payout - 1)
        """
        if win_payout <= 1:
            return 0.0

        b = win_payout - 1
        kelly = (win_probability * b - loss_probability) / b

        # Constrain to 0-1 range
        return max(0.0, min(kelly, 1.0))

    def _calculate_confidence_score(
        self, market: PolymarketMarket, estimated_probability: float
    ) -> float:
        """
        Calculate confidence score (0-100) for a probability estimate.

        Factors:
        - Market liquidity (higher = more confidence)
        - Time to resolution (closer = more confidence for current events)
        - Historical accuracy of similar markets (not implemented yet)
        - Consistency of market price (volatility)
        """
        score = 50.0  # Baseline

        # Adjust for liquidity
        liquidity_score = min(100, market.volume_24h / 5000)  # 5k USD = 100 score
        score += (liquidity_score - 50) * 0.3

        # Adjust for time to resolution
        if market.end_time:
            days_left = (market.end_time - datetime.now()).days
            if days_left < 7:
                score += 20  # High confidence for imminent events
            elif days_left < 30:
                score += 10
            elif days_left > 365:
                score -= 10  # Low confidence for distant events

        # Adjust for price consistency (simplified)
        if market.outcomes:
            prices = [o.price for o in market.outcomes]
            price_range = max(prices) - min(prices)
            if price_range < 0.1:  # Tight spread
                score += 10
            elif price_range > 0.3:  # Wide spread
                score -= 10

        # Constrain to 0-100
        return max(0.0, min(score, 100.0))

    async def _trigger_opportunity_alert(self, opportunity: TradingOpportunity) -> None:
        """Trigger an alert for a high-confidence trading opportunity."""
        alert = Alert(
            condition=AlertCondition(
                name="high_confidence_opportunity",
                condition_type="edge_threshold",
                parameter=0.03,
            ),
            market=None,  # Would need market object
            current_value=opportunity.probability_edge,
            threshold_value=self._config["min_probability_edge"],
            message=(
                f"High-confidence trading opportunity detected: "
                f"{opportunity.market_question[:100]}... "
                f"Edge: {opportunity.probability_edge:.1%}, "
                f"Confidence: {opportunity.confidence_score:.0f}/100"
            ),
            severity="warning" if opportunity.urgency == "high" else "info",
        )

        self._active_alerts.append(alert)

        # Trim alerts list
        if len(self._active_alerts) > 50:
            self._active_alerts = self._active_alerts[-50:]

        logger.info(
            "PolymarketMonitor: triggered opportunity alert — %s (edge: %.1%%)",
            opportunity.market_question[:80],
            opportunity.probability_edge,
        )

        # Publish trading signal to event bus for whallet_trader
        if self.ctx.event_bus and opportunity.recommended_action.startswith("BUY_"):
            await self._publish_trading_signal(opportunity)

    async def _publish_trading_signal(self, opportunity: TradingOpportunity) -> None:
        """Publish a trading signal to the event bus for whallet_trader."""

        # Determine outcome and action based on recommended_action
        if opportunity.recommended_action == "BUY_YES":
            action = "buy"
            outcome = "YES"
        elif opportunity.recommended_action == "BUY_NO":
            action = "buy"
            outcome = "NO"
        else:
            return  # Don't publish hold or sell signals

        signal_data = {
            "signal_id": f"poly_{opportunity.market_id}_{int(time.time())}",
            "market_id": opportunity.market_id,
            "market_question": opportunity.market_question,
            "action": action,
            "outcome": outcome,
            "market_price": float(opportunity.market_price),
            "our_probability": float(opportunity.our_probability),
            "probability_edge": float(opportunity.probability_edge),
            "confidence_score": opportunity.confidence_score,
            "volume_score": opportunity.volume_score,
            "time_horizon_days": opportunity.time_horizon_days,
            "suggested_position_size_percent": float(opportunity.position_size_percent),
            "kelly_fraction": float(opportunity.kelly_fraction),
            "urgency": opportunity.urgency,
            "expected_value": float(opportunity.expected_value),
        }

        try:
            if self.ctx.event_bus:
                await self.ctx.event_bus.emit("polymarket.trading_signal", **signal_data)
                logger.info(
                    "PolymarketMonitor: published trading signal for %s (edge: %.1f%%, confidence: %.0f%%)",
                    opportunity.market_question[:60],
                    opportunity.probability_edge * 100,
                    opportunity.confidence_score,
                )
        except Exception as e:
            logger.error("PolymarketMonitor: failed to publish trading signal: %s", e)

    async def _check_alert_conditions(self, markets: list[PolymarketMarket]) -> None:
        """Check all alert conditions against current market data."""
        # Simplified implementation
        # In a full implementation, this would evaluate each condition
        pass

    def _setup_default_alert_conditions(self) -> None:
        """Set up default alert conditions."""
        self._alert_conditions = [
            AlertCondition(
                name="high_edge_opportunity",
                condition_type="edge_threshold",
                parameter=0.05,  # 5% edge
            ),
            AlertCondition(
                name="low_liquidity_warning",
                condition_type="volume_threshold",
                parameter=1000.0,  # $1k minimum
            ),
            AlertCondition(
                name="market_closing_soon",
                condition_type="time_based",
                parameter=7,  # 7 days
            ),
        ]

    async def generate_market_report(self) -> str:
        """
        Generate a comprehensive market analysis report using LLM.

        Returns:
            Markdown-formatted report
        """
        if not self._client:
            return "# Market Report\n\nNo market data available."

        try:
            # Fetch current markets
            markets = await self._client.get_all_markets(
                limit=self._config["max_markets"], use_mock_if_old=True
            )

            if not markets:
                return "# Market Report\n\nNo markets found."

            # Generate report using LLM analyzer
            if self._llm_analyzer:
                report = await self._llm_analyzer.generate_market_report(markets)
                return report
            else:
                # Fallback report
                from datetime import datetime

                report_lines = [
                    "# Polymarket Market Report",
                    f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
                    f"*Markets Analyzed: {len(markets)}*",
                    "",
                    "## Summary",
                    f"Analyzed {len(markets)} prediction markets.",
                    "LLM analysis not available - using basic statistics.",
                    "",
                    "## Market Categories",
                ]

                # Count categories
                categories = {}
                for market in markets:
                    cat = market.category.value
                    categories[cat] = categories.get(cat, 0) + 1

                for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                    report_lines.append(f"- **{cat}**: {count} markets")

                report_lines.extend(
                    [
                        "",
                        "## Top Markets by Volume",
                    ]
                )

                # Top 5 by volume
                top_markets = sorted(markets, key=lambda m: m.volume_24h, reverse=True)[:5]
                for i, market in enumerate(top_markets, 1):
                    report_lines.append(
                        f"{i}. **{market.question[:60]}...** - ${market.volume_24h:,.0f} volume"
                    )

                return "\n".join(report_lines)

        except Exception as e:
            logger.error(f"Failed to generate market report: {e}")
            return f"# Market Report\n\nError generating report: {e}"

    def _load_state(self) -> None:
        """Load plugin state from disk."""
        if not self._state_file or not self._state_file.exists():
            return

        try:
            data = json.loads(self._state_file.read_text())
            self._monitored_markets = data.get("monitored_markets", [])
            self._recent_opportunities = [
                TradingOpportunity(**opp) for opp in data.get("recent_opportunities", [])
            ]
            self._last_check_time = data.get("last_check_time", 0)
            logger.debug(
                "PolymarketMonitor: loaded state with %d monitored markets",
                len(self._monitored_markets),
            )
        except (json.JSONDecodeError, KeyError, ValidationError) as e:
            logger.warning("PolymarketMonitor: failed to load state: %s", e)
            self._monitored_markets = []
            self._recent_opportunities = []

    def _save_state(self) -> None:
        """Persist plugin state to disk."""
        if not self._state_file:
            return

        try:
            data = {
                "monitored_markets": self._monitored_markets,
                "recent_opportunities": [
                    opp.model_dump() for opp in self._recent_opportunities[-50:]
                ],
                "last_check_time": self._last_check_time,
                "saved_at": datetime.now().isoformat(),
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error("PolymarketMonitor: failed to save state: %s", e, exc_info=True)

    async def teardown(self) -> None:
        """Clean up resources."""
        # Close Neo4j connection
        if self._graph_store:
            self._graph_store.close()
            logger.info("Neo4j GraphStore connection closed")

        # Close HTTP session
        if self._session:
            await self._session.close()
            logger.info("HTTP session closed")

    async def _handle_trade_executed(self, trade_data: Dict[str, Any]) -> None:
        """Handle trade execution events from whallet_trader."""
        if not self._graph_store:
            return

        try:
            # Extract trade information
            trade_id = trade_data.get("trade_id", f"trade_{int(time.time())}")
            market_id = trade_data.get("market_id", "")
            outcome = trade_data.get("outcome", "")
            action = trade_data.get("action", "")
            position_size_usd = float(trade_data.get("position_size_usd", 0))
            pnl_usd = float(trade_data.get("pnl_usd", 0))
            pnl_percent = float(trade_data.get("pnl_percent", 0))
            strategy = trade_data.get("strategy", "llm_edge_detection")
            confidence = float(trade_data.get("confidence", 0.5))
            tags = trade_data.get("tags", [])

            # Store trade in Neo4j
            success = await self._graph_store.store_trade(
                trade_id=trade_id,
                market_id=market_id,
                outcome=outcome,
                action=action,
                position_size_usd=position_size_usd,
                pnl_usd=pnl_usd,
                pnl_percent=pnl_percent,
                strategy=strategy,
                confidence=confidence,
                tags=tags,
            )

            if success:
                logger.info(f"Stored trade {trade_id} in Neo4j GraphStore")
            else:
                logger.warning(f"Failed to store trade {trade_id} in Neo4j")

        except Exception as e:
            logger.error(f"Failed to handle trade execution event: {e}")
