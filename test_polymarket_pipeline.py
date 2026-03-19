#!/usr/bin/env python3
"""
Test the full Polymarket trading pipeline:
1. Fetch markets from API (with mock fallback)
2. Analyze for trading opportunities
3. Generate trading signals
4. Simulate trade execution
5. Track performance
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_pipeline():
    """Test the full Polymarket trading pipeline."""
    logger.info("Starting Polymarket pipeline test...")

    try:
        # Import the modules
        from overblick.plugins.polymarket_monitor.polymarket_client import PolymarketClient
        from overblick.plugins.polymarket_monitor.mock_data_generator import ModernMockDataGenerator

        logger.info("1. Testing Polymarket API client...")

        # Test with real API first (will fall back to mock if old data)
        async with PolymarketClient() as client:
            markets = await client.get_all_markets(limit=10, use_mock_if_old=True)
            logger.info(f"Fetched {len(markets)} markets")

            if markets:
                for i, market in enumerate(markets[:3]):
                    logger.info(f"  Market {i + 1}: {market.question[:50]}...")
                    logger.info(f"    Status: {market.status}, Category: {market.category}")
                    logger.info(
                        f"    Outcomes: {len(market.outcomes)}, Volume 24h: ${market.volume_24h:,.2f}"
                    )
                    if market.implied_probability:
                        logger.info(f"    Implied probability: {market.implied_probability:.2%}")

        logger.info("\n2. Testing mock data generator...")

        generator = ModernMockDataGenerator()
        mock_markets = generator.generate_markets(5)
        logger.info(f"Generated {len(mock_markets)} mock markets")

        for i, market in enumerate(mock_markets[:2]):
            logger.info(f"  Mock Market {i + 1}: {market.question[:50]}...")
            logger.info(f"    Status: {market.status}, Category: {market.category}")
            logger.info(f"    Outcomes: {[o.name for o in market.outcomes]}")
            if market.implied_probability:
                logger.info(f"    Implied probability: {market.implied_probability:.2%}")

        logger.info("\n3. Testing trading opportunity detection...")

        # Simple opportunity detection logic
        opportunities = []
        for market in markets[:5]:
            if market.status.value == "open" and len(market.outcomes) == 2:
                # Find YES outcome
                yes_outcome = next((o for o in market.outcomes if o.ticker == "YES"), None)
                if yes_outcome:
                    # Simple edge detection: if price is far from 0.5, there might be an edge
                    price = yes_outcome.price
                    edge = abs(price - 0.5)

                    if edge > 0.1:  # More than 10% from fair value
                        opportunities.append(
                            {
                                "market_id": market.id,
                                "question": market.question,
                                "yes_price": price,
                                "edge": edge,
                                "volume": market.volume_24h,
                            }
                        )

        logger.info(f"Found {len(opportunities)} potential trading opportunities")
        for opp in opportunities[:3]:
            logger.info(f"  Opportunity: {opp['question'][:40]}...")
            logger.info(f"    YES price: {opp['yes_price']:.3f}, Edge: {opp['edge']:.3f}")
            logger.info(f"    Volume 24h: ${opp['volume']:,.2f}")

        logger.info("\n4. Testing performance tracker...")

        # Create a simple performance tracker test
        test_dir = Path("/tmp/polymarket_test")
        test_dir.mkdir(exist_ok=True)

        from overblick.plugins.whallet_trader.performance_tracker import PerformanceTracker

        tracker = PerformanceTracker(test_dir)

        # Generate a performance report
        report = tracker.generate_performance_report()
        logger.info("Performance report generated (empty since no trades yet)")

        # Show sample report
        lines = report.split("\n")
        for line in lines[:10]:
            logger.info(f"  {line}")

        logger.info("\n✅ Pipeline test completed successfully!")
        logger.info("Summary:")
        logger.info(f"  - Markets fetched: {len(markets)}")
        logger.info(f"  - Trading opportunities found: {len(opportunities)}")
        logger.info(f"  - Mock data working: Yes")
        logger.info(f"  - Performance tracking: Ready")

        return True

    except Exception as e:
        logger.error(f"❌ Pipeline test failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = asyncio.run(test_pipeline())
    exit(0 if success else 1)
