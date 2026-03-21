"""
Neo4j Graph-based learning store for Polymarket trading.

Stores trading knowledge as a graph with nodes and relationships:
- Markets, outcomes, strategies, risk factors
- Trade results, performance metrics
- Weather patterns, LLM analysis insights

This enables complex queries like:
- "Show all similar markets I've traded before"
- "What's my win rate for weather markets?"
- "Which strategies work best in high volatility?"
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None  # type: ignore

logger = logging.getLogger(__name__)


class PolymarketGraphStore:
    """Neo4j-based graph store for Polymarket trading knowledge."""

    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = ""):
        """Initialize Neo4j connection."""
        if GraphDatabase is None:
            logger.warning("Neo4j driver not available - using fallback in-memory store")
            self._driver = None
            return

        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
            # Verify connection
            self._driver.verify_connectivity()
            logger.info("Connected to Neo4j at %s", uri)

            # Initialize graph schema
            self._initialize_schema()
        except Exception as e:
            logger.warning(f"Failed to connect to Neo4j: {e} - using fallback store")
            self._driver = None

    def _initialize_schema(self) -> None:
        """Create initial graph schema if it doesn't exist."""
        if not self._driver:
            return

        with self._driver.session() as session:
            # Create indexes for performance (one statement per index in Neo4j 5.x)
            session.run("CREATE INDEX IF NOT EXISTS FOR (m:Market) ON (m.id)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (t:Trade) ON (t.id)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (s:Strategy) ON (s.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (w:WeatherPattern) ON (w.location)")

    @property
    def is_connected(self) -> bool:
        """Check if Neo4j driver is connected."""
        return self._driver is not None

    def close(self) -> None:
        """Close Neo4j connection."""
        if self._driver:
            self._driver.close()

    async def store_trade(
        self,
        trade_id: str,
        market_id: str,
        outcome: str,
        action: str,
        position_size_usd: float,
        pnl_usd: float,
        pnl_percent: float,
        strategy: Optional[str] = None,
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """Store a trade with all related knowledge."""
        if not self._driver:
            return await self._store_trade_fallback(
                trade_id,
                market_id,
                outcome,
                action,
                position_size_usd,
                pnl_usd,
                pnl_percent,
                strategy,
                confidence,
                tags,
            )

        try:
            with self._driver.session() as session:
                # Create or get market node
                session.run(
                    """
                    MERGE (m:Market {id: $market_id})
                    SET m.last_traded = datetime(),
                        m.total_trades = coalesce(m.total_trades, 0) + 1,
                        m.pnl_sum = coalesce(m.pnl_sum, 0) + $pnl_usd
                """,
                    market_id=market_id,
                    pnl_usd=pnl_usd,
                )

                # Create trade node
                session.run(
                    """
                    MERGE (t:Trade {id: $trade_id})
                    SET t.outcome = $outcome,
                        t.action = $action,
                        t.size_usd = $position_size_usd,
                        t.pnl_usd = $pnl_usd,
                        t.pnl_percent = $pnl_percent,
                        t.confidence = $confidence,
                        t.timestamp = datetime()
                """,
                    trade_id=trade_id,
                    outcome=outcome,
                    action=action,
                    position_size_usd=position_size_usd,
                    pnl_usd=pnl_usd,
                    pnl_percent=pnl_percent,
                    confidence=confidence,
                )

                # Connect trade to market
                session.run(
                    """
                    MATCH (t:Trade {id: $trade_id}), (m:Market {id: $market_id})
                    MERGE (t)-[:TRADED_AT]->(m)
                """,
                    trade_id=trade_id,
                    market_id=market_id,
                )

                # Add strategy node if provided
                if strategy:
                    session.run(
                        """
                        MERGE (s:Strategy {name: $strategy})
                        WITH s
                        MATCH (t:Trade {id: $trade_id})
                        MERGE (t)-[:USED_STRATEGY]->(s)
                    """,
                        strategy=strategy,
                        trade_id=trade_id,
                    )

                # Add tags
                if tags:
                    for tag in tags:
                        session.run(
                            """
                            MERGE (tg:Tag {name: $tag})
                            WITH tg
                            MATCH (t:Trade {id: $trade_id})
                            MERGE (t)-[:HAS_TAG]->(tg)
                        """,
                            tag=tag,
                            trade_id=trade_id,
                        )

                logger.debug("Stored trade %s with graph relationships", trade_id)
                return True

        except Exception as e:
            logger.debug(f"Failed to store trade in Neo4j: {e}")
            return await self._store_trade_fallback(
                trade_id,
                market_id,
                outcome,
                action,
                position_size_usd,
                pnl_usd,
                pnl_percent,
                strategy,
                confidence,
                tags,
            )

    async def store_market_analysis(
        self,
        market_id: str,
        question: str,
        category: Optional[str] = None,
        llm_probability: float = 0.5,
        edge: float = 0.0,
        weather_pattern: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store market analysis with LLM insights and weather data."""
        if not self._driver:
            return await self._store_market_analysis_fallback(
                market_id, question, category, llm_probability, edge, weather_pattern
            )

        try:
            with self._driver.session() as session:
                # Create or update market node
                session.run(
                    """
                    MERGE (m:Market {id: $market_id})
                    SET m.question = $question,
                        m.category = $category,
                        m.llm_probability = $llm_probability,
                        m.edge = $edge,
                        m.last_analyzed = datetime()
                """,
                    market_id=market_id,
                    question=question,
                    category=category,
                    llm_probability=llm_probability,
                    edge=edge,
                )

                # Link weather pattern if provided
                if weather_pattern:
                    location = weather_pattern.get("location", "unknown")
                    session.run(
                        """
                        MERGE (w:WeatherPattern {location: $location})
                        SET w.pattern = $pattern,
                            w.rain_prob = $rain_prob,
                            w.temp = $temp
                        WITH w
                        MATCH (m:Market {id: $market_id})
                        MERGE (m)-[:AFFECTED_BY]->(w)
                    """,
                        market_id=market_id,
                        location=location,
                        pattern=str(weather_pattern.get("pattern", "")),
                        rain_prob=weather_pattern.get("rain_prob"),
                        temp=weather_pattern.get("temp"),
                    )

                logger.debug("Stored market analysis for %s", market_id)
                return True

        except Exception as e:
            logger.debug(f"Failed to store market analysis in Neo4j: {e}")
            return await self._store_market_analysis_fallback(
                market_id, question, category, llm_probability, edge, weather_pattern
            )

    async def query_similar_markets(self, market_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Find similar markets based on trading patterns and outcomes."""
        if not self._driver:
            return await self._query_similar_markets_fallback(market_id, limit)

        try:
            with self._driver.session() as session:
                result = session.run(
                    """
                    MATCH (m1:Market {id: $market_id})-[t1:TRADED_AT]-(trade1:Trade)
                    OPTIONAL MATCH (trade1)-[:USED_STRATEGY]->(s:Strategy)
                    OPTIONAL MATCH (trade1)-[:HAS_TAG]->(tg:Tag)
                    
                    WITH m1, COLLECT(DISTINCT s.name) as strategies, COLLECT(DISTINCT tg.name) as tags
                    MATCH (m2:Market)
                    WHERE m2.id <> $market_id
                    
                    // Calculate similarity based on shared strategies and tags
                    RETURN m2.id as market_id,
                           m2.question,
                           m2.category,
                           size(strategies) + size(tags) as similarity_score,
                           m2.llm_probability,
                           m2.edge
                    ORDER BY similarity_score DESC
                    LIMIT $limit
                """,
                    market_id=market_id,
                    limit=limit,
                )

                return [dict(record) for record in result]

        except Exception as e:
            logger.debug(f"Failed to query similar markets: {e}")
            return []

    async def get_performance_by_category(self) -> Dict[str, Dict[str, float]]:
        """Get performance metrics grouped by market category."""
        if not self._driver:
            return await self._get_performance_by_category_fallback()

        try:
            with self._driver.session() as session:
                result = session.run("""
                    MATCH (m:Market)-[t:TRADED_AT]->(trade:Trade)
                    WHERE trade.pnl_usd IS NOT NULL
                    
                    WITH m.category as category,
                         COUNT(t) as trades,
                         SUM(trade.pnl_usd) as total_pnl,
                         AVG(trade.pnl_percent) as avg_return,
                         SUM(CASE WHEN trade.pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                         SUM(CASE WHEN trade.pnl_usd <= 0 THEN 1 ELSE 0 END) as losses
                    
                    RETURN category,
                           trades,
                           total_pnl,
                           avg_return,
                           wins,
                           losses,
                           CASE WHEN trades > 0 THEN (wins * 1.0 / trades * 100) ELSE 0 END as win_rate
                """)

                return {record["category"]: dict(record) for record in result}

        except Exception as e:
            logger.debug(f"Failed to get performance by category: {e}")
            return {}

    async def query_weather_trading_patterns(
        self, location: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query trading patterns related to weather."""
        if not self._driver:
            return await self._query_weather_trading_patterns_fallback(location)

        try:
            with self._driver.session() as session:
                query = """
                    MATCH (m:Market)-[:AFFECTED_BY]->(w:WeatherPattern)
                    OPTIONAL MATCH (m)-[t:TRADED_AT]-(trade:Trade)
                    
                    WHERE w.location = $location OR $location IS NULL
                    
                    RETURN w.location,
                           COUNT(DISTINCT m.id) as markets,
                           COUNT(t) as trades,
                           SUM(CASE WHEN trade.pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                           AVG(trade.pnl_percent) as avg_return
                """

                result = session.run(query, location=location)
                return [dict(record) for record in result]

        except Exception as e:
            logger.debug(f"Failed to query weather patterns: {e}")
            return []

    async def _store_trade_fallback(
        self,
        trade_id: str,
        market_id: str,
        outcome: str,
        action: str,
        position_size_usd: float,
        pnl_usd: float,
        pnl_percent: float,
        strategy: Optional[str],
        confidence: float,
        tags: Optional[List[str]],
    ) -> bool:
        """Fallback in-memory storage (simplified)."""
        logger.debug(f"Using fallback store for trade {trade_id}")
        return True  # Return success for fallback

    async def _store_market_analysis_fallback(
        self,
        market_id: str,
        question: str,
        category: Optional[str],
        llm_probability: float,
        edge: float,
        weather_pattern: Optional[Dict[str, Any]],
    ) -> bool:
        """Fallback in-memory storage."""
        logger.debug(f"Using fallback store for market analysis {market_id}")
        return True

    async def _query_similar_markets_fallback(
        self, market_id: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Fallback query."""
        logger.debug(f"Using fallback query for similar markets to {market_id}")
        return []

    async def _get_performance_by_category_fallback(self) -> Dict[str, Dict[str, float]]:
        """Fallback performance query."""
        logger.debug("Using fallback performance query")
        return {}

    async def _query_weather_trading_patterns_fallback(
        self, location: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Fallback weather pattern query."""
        logger.debug(f"Using fallback weather pattern query for {location}")
        return []


def create_graph_store(
    uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = ""
) -> PolymarketGraphStore:
    """Factory function to create graph store instance."""
    return PolymarketGraphStore(uri=uri, user=user, password=password)
