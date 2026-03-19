"""
Modern mock data generator for Polymarket.

Generates realistic current prediction markets for testing the trading system
when the gamma API only returns old 2020 markets.
"""

import json
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .models import MarketCategory, MarketStatus, PolymarketMarket, MarketOutcome


class ModernMockDataGenerator:
    """Generates realistic modern Polymarket data for testing."""

    def __init__(self, seed: int = 42):
        random.seed(seed)
        self._current_time = datetime.now()

        # Realistic current market questions (2025-2026)
        self._market_templates = [
            {
                "category": MarketCategory.POLITICS,
                "questions": [
                    "Will Donald Trump win the 2024 US presidential election?",
                    "Will Kamala Harris be the Democratic nominee in 2028?",
                    "Will there be a government shutdown in Q1 2025?",
                    "Will the UK Labour Party win the next general election?",
                    "Will France's National Rally win the 2027 presidential election?",
                ],
            },
            {
                "category": MarketCategory.FINANCE,
                "questions": [
                    "Will the S&P 500 close above 6000 by end of 2025?",
                    "Will the Fed cut rates by at least 50bps in 2025?",
                    "Will Bitcoin reach $100,000 by end of 2025?",
                    "Will the US enter a recession in 2025?",
                    "Will the 10-year Treasury yield fall below 3.5% in 2025?",
                ],
            },
            {
                "category": MarketCategory.CRYPTO,
                "questions": [
                    "Will Ethereum ETF be approved by SEC in 2025?",
                    "Will Solana reach $300 by end of 2025?",
                    "Will Cardano implement full smart contracts in 2025?",
                    "Will Bitcoin dominance fall below 40% in 2025?",
                    "Will a major DeFi hack exceed $100M in 2025?",
                ],
            },
            {
                "category": MarketCategory.TECHNOLOGY,
                "questions": [
                    "Will Apple release Vision Pro 2 in 2025?",
                    "Will Tesla achieve full self-driving (Level 5) in 2025?",
                    "Will OpenAI release GPT-5 in 2025?",
                    "Will Microsoft acquire another major AI company in 2025?",
                    "Will Google's Gemini surpass GPT-4 in benchmarks by 2025?",
                ],
            },
            {
                "category": MarketCategory.SPORTS,
                "questions": [
                    "Will the Kansas City Chiefs win Super Bowl LIX?",
                    "Will Lionel Messi win the 2025 Ballon d'Or?",
                    "Will Novak Djokovic win Wimbledon 2025?",
                    "Will the USA win the most gold medals at Paris 2024?",
                    "Will Manchester City win the Premier League 2024-25?",
                ],
            },
        ]

        # Generate realistic market IDs
        self._market_ids = [f"mock_{i:06d}" for i in range(1, 101)]

    def generate_markets(self, count: int = 50) -> List[PolymarketMarket]:
        """Generate realistic modern markets."""
        markets = []

        for i in range(min(count, len(self._market_ids))):
            market_id = self._market_ids[i]

            # Pick random template
            template = random.choice(self._market_templates)
            category = template["category"]
            question = random.choice(template["questions"])

            # Generate realistic dates
            created_time = self._current_time - timedelta(days=random.randint(1, 90))

            # 70% open, 20% closed, 10% resolved
            status_roll = random.random()
            if status_roll < 0.7:
                status = MarketStatus.OPEN
                end_time = self._current_time + timedelta(days=random.randint(30, 365))
                resolution_time = None
            elif status_roll < 0.9:
                status = MarketStatus.CLOSED
                end_time = self._current_time - timedelta(days=random.randint(1, 30))
                resolution_time = None
            else:
                status = MarketStatus.RESOLVED
                end_time = self._current_time - timedelta(days=random.randint(1, 30))
                resolution_time = end_time + timedelta(days=random.randint(1, 7))

            # Generate outcomes (mostly binary, some multi-outcome)
            if random.random() < 0.8:
                # Binary market
                outcomes = [
                    MarketOutcome(
                        name="Yes",
                        ticker="YES",
                        price=random.uniform(0.3, 0.7),
                        volume_24h=random.uniform(1000, 50000),
                        last_updated=self._current_time - timedelta(minutes=random.randint(1, 60)),
                    ),
                    MarketOutcome(
                        name="No",
                        ticker="NO",
                        price=1.0 - 0.0,  # Will be calculated below
                        volume_24h=random.uniform(1000, 50000),
                        last_updated=self._current_time - timedelta(minutes=random.randint(1, 60)),
                    ),
                ]
                # Ensure prices sum to ~1.0 (allowing for small spread)
                outcomes[1].price = max(
                    0.01, min(0.99, 1.0 - outcomes[0].price + random.uniform(-0.02, 0.02))
                )
            else:
                # Multi-outcome market (3-5 outcomes)
                num_outcomes = random.randint(3, 5)
                outcome_names = [f"Option {chr(65 + i)}" for i in range(num_outcomes)]
                base_prices = [random.random() for _ in range(num_outcomes)]
                total = sum(base_prices)

                outcomes = []
                for j, name in enumerate(outcome_names):
                    price = base_prices[j] / total
                    outcomes.append(
                        MarketOutcome(
                            name=name,
                            ticker=f"OPT_{chr(65 + j)}",
                            price=max(0.01, min(0.99, price)),
                            volume_24h=random.uniform(500, 20000),
                            last_updated=self._current_time
                            - timedelta(minutes=random.randint(1, 60)),
                        )
                    )

            # Generate realistic trading metrics
            volume_24h = sum(o.volume_24h for o in outcomes) * random.uniform(1.0, 2.0)
            liquidity = volume_24h * random.uniform(0.5, 2.0)
            open_interest = volume_24h * random.uniform(0.2, 1.5)

            # Calculate implied probability for binary markets
            implied_probability = None
            if len(outcomes) == 2:
                yes_outcome = next((o for o in outcomes if o.ticker == "YES"), None)
                if yes_outcome:
                    implied_probability = yes_outcome.price

            # Create slug from question
            slug = question.lower().replace(" ", "-").replace("?", "").replace("'", "")[:50]

            market = PolymarketMarket(
                id=market_id,
                slug=slug,
                question=question,
                description=f"Market for: {question}",
                category=category,
                status=status,
                resolution=None,  # Mock doesn't know resolution
                created_time=created_time,
                end_time=end_time,
                resolution_time=resolution_time,
                outcomes=outcomes,
                volume_24h=volume_24h,
                liquidity=liquidity,
                open_interest=open_interest,
                implied_probability=implied_probability,
            )

            markets.append(market)

        return markets

    def generate_gamma_api_format(self, count: int = 50) -> List[Dict[str, Any]]:
        """Generate markets in gamma API format for compatibility."""
        markets = self.generate_markets(count)
        gamma_markets = []

        for market in markets:
            # Convert to gamma API format
            outcomes_list = [o.name for o in market.outcomes]
            prices_list = [o.price for o in market.outcomes]

            gamma_market = {
                "id": market.id,
                "slug": market.slug,
                "question": market.question,
                "description": market.description,
                "category": market.category.value,
                "closed": market.status == MarketStatus.CLOSED
                or market.status == MarketStatus.RESOLVED,
                "archived": market.status == MarketStatus.RESOLVED,
                "createdAt": market.created_time.isoformat() + "Z",
                "endDate": market.end_time.isoformat() + "Z" if market.end_time else None,
                "outcomes": json.dumps(outcomes_list),
                "outcomePrices": json.dumps(prices_list),
                "volumeNum": market.volume_24h,
                "liquidityNum": market.liquidity,
                "openInterest": market.open_interest,
            }

            gamma_markets.append(gamma_market)

        return gamma_markets

    def update_prices(self, markets: List[PolymarketMarket]) -> List[PolymarketMarket]:
        """Update prices with realistic random walk."""
        updated_markets = []

        for market in markets:
            if market.status != MarketStatus.OPEN:
                updated_markets.append(market)
                continue

            updated_outcomes = []
            for outcome in market.outcomes:
                # Random walk: ±5% max change
                change = random.uniform(-0.05, 0.05)
                new_price = max(0.01, min(0.99, outcome.price + change))

                # Adjust volume slightly
                volume_change = random.uniform(-0.2, 0.5)
                new_volume = max(100, outcome.volume_24h * (1 + volume_change))

                updated_outcome = MarketOutcome(
                    name=outcome.name,
                    ticker=outcome.ticker,
                    price=new_price,
                    volume_24h=new_volume,
                    last_updated=self._current_time,
                )
                updated_outcomes.append(updated_outcome)

            # For binary markets, ensure prices sum to ~1.0
            if len(updated_outcomes) == 2:
                total = sum(o.price for o in updated_outcomes)
                if total > 1.02 or total < 0.98:  # Too far from 1.0
                    # Adjust proportionally
                    for outcome in updated_outcomes:
                        outcome.price = outcome.price / total

            # Update market metrics
            new_volume_24h = sum(o.volume_24h for o in updated_outcomes) * random.uniform(0.8, 1.2)
            new_liquidity = market.liquidity * random.uniform(0.9, 1.1)
            new_open_interest = market.open_interest * random.uniform(0.95, 1.05)

            # Recalculate implied probability
            implied_probability = None
            if len(updated_outcomes) == 2:
                yes_outcome = next((o for o in updated_outcomes if o.ticker == "YES"), None)
                if yes_outcome:
                    implied_probability = yes_outcome.price

            updated_market = PolymarketMarket(
                id=market.id,
                slug=market.slug,
                question=market.question,
                description=market.description,
                category=market.category,
                status=market.status,
                resolution=market.resolution,
                created_time=market.created_time,
                end_time=market.end_time,
                resolution_time=market.resolution_time,
                outcomes=updated_outcomes,
                volume_24h=new_volume_24h,
                liquidity=new_liquidity,
                open_interest=new_open_interest,
                implied_probability=implied_probability,
            )

            updated_markets.append(updated_market)

        return updated_markets
