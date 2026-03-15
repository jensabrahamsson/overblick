"""
LLM Analyzer for Polymarket using Qwen 3.5.

Specialized analysis for prediction markets using local Qwen 3.5 model.
Provides detailed market analysis, probability estimation, and trading insights.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from overblick.core.security.input_sanitizer import wrap_external_content

from .models import PolymarketMarket, TradingOpportunity

logger = logging.getLogger(__name__)


class PolymarketLLMAnalyzer:
    """LLM-based analyzer for Polymarket prediction markets."""

    def __init__(self, llm_pipeline):
        """
        Initialize analyzer with LLM pipeline.

        Args:
            llm_pipeline: SafeLLMPipeline instance
        """
        self.llm_pipeline = llm_pipeline
        logger.info("Initialized PolymarketLLMAnalyzer")

    async def analyze_market(self, market: PolymarketMarket) -> Dict[str, Any]:
        """
        Perform comprehensive LLM analysis of a market.

        Returns:
            Dictionary with analysis results including:
            - probability_estimate: LLM's probability estimate (0-1)
            - confidence_score: Confidence in estimate (0-100)
            - reasoning: LLM's reasoning process
            - factors_considered: Key factors identified
            - risk_assessment: Risk analysis
        """
        if not self.llm_pipeline:
            logger.warning("No LLM pipeline available for market analysis")
            return self._get_fallback_analysis(market)

        # Build detailed market context
        market_context = self._build_detailed_context(market)
        wrapped_context = wrap_external_content(market_context, source="polymarket")

        # Prepare analysis prompt for Qwen 3.5
        messages = [
            {
                "role": "system",
                "content": self._get_analysis_system_prompt(),
            },
            {
                "role": "user",
                "content": self._get_analysis_user_prompt(market, wrapped_context),
            },
        ]

        try:
            result = await self.llm_pipeline.chat(messages)

            if result and not result.blocked and result.content:
                return self._parse_analysis_result(result.content, market)
            else:
                logger.warning(f"LLM analysis blocked or empty: {result}")
                return self._get_fallback_analysis(market)

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return self._get_fallback_analysis(market)

    async def evaluate_trading_opportunity(
        self, market: PolymarketMarket, market_price: float, our_probability: float
    ) -> Dict[str, Any]:
        """
        Evaluate a potential trading opportunity.

        Args:
            market: The market
            market_price: Current market price (0-1)
            our_probability: Our estimated probability (0-1)

        Returns:
            Trading opportunity evaluation
        """
        if not self.llm_pipeline:
            return self._get_fallback_opportunity_evaluation(market, market_price, our_probability)

        edge = abs(our_probability - market_price)

        messages = [
            {
                "role": "system",
                "content": self._get_trading_system_prompt(),
            },
            {
                "role": "user",
                "content": self._get_trading_user_prompt(
                    market, market_price, our_probability, edge
                ),
            },
        ]

        try:
            result = await self.llm_pipeline.chat(messages)

            if result and not result.blocked and result.content:
                return self._parse_trading_evaluation(
                    result.content, market, market_price, our_probability, edge
                )
            else:
                logger.warning("LLM trading evaluation blocked")
                return self._get_fallback_opportunity_evaluation(
                    market, market_price, our_probability
                )

        except Exception as e:
            logger.error(f"Trading evaluation failed: {e}")
            return self._get_fallback_opportunity_evaluation(market, market_price, our_probability)

    async def generate_market_report(self, markets: List[PolymarketMarket]) -> str:
        """
        Generate comprehensive market analysis report.

        Args:
            markets: List of markets to analyze

        Returns:
            Markdown-formatted report
        """
        if not self.llm_pipeline or len(markets) == 0:
            return self._get_fallback_report(markets)

        # Summarize market data
        market_summaries = []
        for i, market in enumerate(markets[:10]):  # Limit to 10 markets
            summary = {
                "id": market.id,
                "question": market.question[:100] + ("..." if len(market.question) > 100 else ""),
                "category": market.category.value,
                "status": market.status.value,
                "volume_24h": market.volume_24h,
                "implied_probability": market.implied_probability,
            }
            market_summaries.append(summary)

        messages = [
            {
                "role": "system",
                "content": self._get_report_system_prompt(),
            },
            {
                "role": "user",
                "content": self._get_report_user_prompt(market_summaries),
            },
        ]

        try:
            result = await self.llm_pipeline.chat(messages)

            if result and not result.blocked and result.content:
                return result.content
            else:
                return self._get_fallback_report(markets)

        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            return self._get_fallback_report(markets)

    def _build_detailed_context(self, market: PolymarketMarket) -> str:
        """Build detailed context for LLM analysis."""
        context_lines = [
            f"MARKET ANALYSIS REQUEST",
            f"========================",
            f"Market ID: {market.id}",
            f"Question: {market.question}",
            f"Category: {market.category.value}",
            f"Status: {market.status.value}",
            f"Created: {market.created_time.strftime('%Y-%m-%d') if market.created_time else 'Unknown'}",
            f"Ends: {market.end_time.strftime('%Y-%m-%d') if market.end_time else 'No end date'}",
            "",
            f"TRADING METRICS:",
            f"24h Volume: ${market.volume_24h:,.2f}",
            f"Liquidity: ${market.liquidity:,.2f}",
            f"Open Interest: ${market.open_interest:,.2f}",
            "",
            f"OUTCOMES:",
        ]

        for outcome in market.outcomes:
            context_lines.append(
                f"  - {outcome.name}: ${outcome.price:.4f} (Volume 24h: ${outcome.volume_24h:,.2f})"
            )

        if market.implied_probability is not None:
            context_lines.append(f"\nIMPLIED PROBABILITY: {market.implied_probability:.2%}")

        if market.description:
            context_lines.append(f"\nDESCRIPTION:\n{market.description[:500]}")

        # Add market-specific context based on category
        category_context = self._get_category_context(market.category)
        if category_context:
            context_lines.append(f"\nCATEGORY CONTEXT:\n{category_context}")

        return "\n".join(context_lines)

    def _get_category_context(self, category) -> str:
        """Get category-specific context for LLM."""
        context_map = {
            "politics": (
                "Political markets involve elections, policy decisions, and geopolitical events. "
                "Consider polling data, expert analysis, historical patterns, and current events. "
                "Be aware of potential biases in media coverage."
            ),
            "finance": (
                "Financial markets involve economic indicators, monetary policy, and market movements. "
                "Consider economic data, central bank actions, market sentiment, and technical analysis. "
                "Be quantitative and data-driven."
            ),
            "crypto": (
                "Cryptocurrency markets involve blockchain projects, token prices, and ecosystem developments. "
                "Consider on-chain metrics, developer activity, community sentiment, and regulatory developments. "
                "High volatility is common."
            ),
            "technology": (
                "Technology markets involve product launches, company performance, and industry trends. "
                "Consider product roadmaps, competitive landscape, innovation cycles, and adoption metrics."
            ),
            "sports": (
                "Sports markets involve game outcomes, player performance, and tournament results. "
                "Consider team statistics, player form, historical matchups, and injury reports."
            ),
            "entertainment": (
                "Entertainment markets involve awards, box office performance, and cultural events. "
                "Consider critic reviews, audience reception, historical patterns, and industry trends."
            ),
        }

        return context_map.get(
            category.value,
            "General prediction market. Consider all available information objectively.",
        )

    def _get_analysis_system_prompt(self) -> str:
        """Get system prompt for market analysis."""
        return """You are PolyTrader, a quantitative prediction market analyst powered by Qwen 3.5.

YOUR ROLE:
- Analyze prediction markets with statistical rigor
- Estimate objective probabilities based on available information
- Identify key factors influencing outcomes
- Assess risks and uncertainties
- Provide calibrated probability estimates

ANALYSIS FRAMEWORK:
1. Information Assessment: Evaluate quality and relevance of available data
2. Base Rate Consideration: Consider historical frequencies and base rates
3. Bayesian Updating: Update probabilities based on new evidence
4. Confidence Calibration: Assess confidence level based on information quality
5. Risk Identification: Identify potential risks and black swan events

RESPONSE FORMAT:
Return a JSON object with these fields:
{
  "probability_estimate": 0.65,  # Your probability estimate (0-1)
  "confidence_score": 75,        # Confidence in estimate (0-100)
  "reasoning": "Brief explanation of your reasoning...",
  "factors_considered": ["Factor 1", "Factor 2", "Factor 3"],
  "risk_assessment": "Assessment of risks and uncertainties..."
}

Be analytical, quantitative, and objective. Avoid emotional language."""

    def _get_analysis_user_prompt(self, market: PolymarketMarket, context: str) -> str:
        """Get user prompt for market analysis."""
        return f"""Analyze this prediction market:

{context}

Provide your analysis in the specified JSON format."""

    def _get_trading_system_prompt(self) -> str:
        """Get system prompt for trading evaluation."""
        return """You are PolyTrader, a risk-aware trading analyst.

TRADING PRINCIPLES:
1. Expected Value: Only trade when expected value is positive
2. Kelly Criterion: Consider optimal position sizing
3. Risk Management: Never risk more than 5% of portfolio on single trade
4. Edge Requirement: Minimum 3% probability edge vs market price
5. Liquidity Consideration: Ensure sufficient market liquidity

EVALUATION CRITERIA:
- Probability Edge: Difference between our estimate and market price
- Confidence Level: How confident are we in our estimate?
- Market Liquidity: Is there sufficient volume for our position?
- Time Horizon: How long until market resolution?
- Risk Factors: What could go wrong?

RESPONSE FORMAT:
Return a JSON object:
{
  "recommended_action": "BUY_YES|BUY_NO|HOLD|AVOID",
  "confidence_level": "high|medium|low",
  "position_size_percent": 2.5,  # Recommended % of portfolio (0-5)
  "expected_value_score": 75,    # 0-100 score of expected value
  "risk_adjustment": "Explanation of risk considerations...",
  "key_risks": ["Risk 1", "Risk 2"]
}"""

    def _get_trading_user_prompt(
        self, market: PolymarketMarket, market_price: float, our_probability: float, edge: float
    ) -> str:
        """Get user prompt for trading evaluation."""
        return f"""Evaluate this trading opportunity:

MARKET: {market.question}
CATEGORY: {market.category.value}
MARKET PRICE (YES): {market_price:.2%}
OUR PROBABILITY ESTIMATE: {our_probability:.2%}
PROBABILITY EDGE: {edge:.2%} ({"FAVORABLE" if edge >= 0.03 else "INSUFFICIENT"})

MARKET METRICS:
- 24h Volume: ${market.volume_24h:,.2f}
- Liquidity: ${market.liquidity:,.2f}
- Status: {market.status.value}
- Time to Resolution: {self._get_time_to_resolution(market)}

OUTCOMES:
{chr(10).join(f"  - {o.name}: ${o.price:.4f}" for o in market.outcomes)}

Provide your trading evaluation in the specified JSON format."""

    def _get_report_system_prompt(self) -> str:
        """Get system prompt for market report."""
        return """You are PolyTrader, generating a market analysis report.

REPORT STRUCTURE:
1. Executive Summary: Overall market landscape
2. Category Analysis: Trends by market category
3. Top Opportunities: Most promising trading opportunities
4. Risk Assessment: Key risks and uncertainties
5. Recommendations: Trading strategy suggestions

STYLE:
- Professional, data-driven analysis
- Clear bullet points and sections
- Quantitative where possible
- Actionable insights

FORMAT:
Use Markdown with clear headings and bullet points."""

    def _get_report_user_prompt(self, market_summaries: List[Dict]) -> str:
        """Get user prompt for market report."""
        summaries_json = json.dumps(market_summaries, indent=2)
        return f"""Generate a market analysis report based on these {len(market_summaries)} markets:

{summaries_json}

Provide a comprehensive Markdown report."""

    def _parse_analysis_result(self, content: str, market: PolymarketMarket) -> Dict[str, Any]:
        """Parse LLM analysis result."""
        try:
            # Try to parse as JSON
            result = json.loads(content.strip())

            # Validate required fields
            if "probability_estimate" not in result:
                raise ValueError("Missing probability_estimate")

            probability = float(result["probability_estimate"])
            confidence = result.get("confidence_score", 50)

            # Clamp values
            probability = max(0.0, min(1.0, probability))
            confidence = max(0, min(100, confidence))

            return {
                "probability_estimate": probability,
                "confidence_score": confidence,
                "reasoning": result.get("reasoning", "No reasoning provided"),
                "factors_considered": result.get("factors_considered", []),
                "risk_assessment": result.get("risk_assessment", "No risk assessment"),
                "source": "llm_analysis",
                "market_id": market.id,
                "analyzed_at": datetime.now().isoformat(),
            }

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse LLM analysis as JSON: {e}")
            return self._parse_text_analysis(content, market)

    def _parse_text_analysis(self, content: str, market: PolymarketMarket) -> Dict[str, Any]:
        """Fallback: parse text analysis for probability."""
        import re

        # Try to extract probability from text
        probability_patterns = [
            r"probability[:\s]*(\d+(?:\.\d+)?)\s*%",
            r"estimate[:\s]*(\d+(?:\.\d+)?)\s*%",
            r"(\d+(?:\.\d+)?)\s*percent",
            r"(\d+(?:\.\d+)?)\s*%",
        ]

        probability = None
        for pattern in probability_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    probability_percent = float(match.group(1))
                    probability = probability_percent / 100.0
                    probability = max(0.0, min(1.0, probability))
                    break
                except ValueError:
                    continue

        # Fallback to market price if no probability found
        if probability is None and market.implied_probability is not None:
            probability = market.implied_probability

        return {
            "probability_estimate": probability or 0.5,
            "confidence_score": 50,
            "reasoning": content[:500] + ("..." if len(content) > 500 else ""),
            "factors_considered": ["Text analysis fallback"],
            "risk_assessment": "Limited analysis due to parsing issues",
            "source": "llm_text_fallback",
            "market_id": market.id,
            "analyzed_at": datetime.now().isoformat(),
        }

    def _parse_trading_evaluation(
        self,
        content: str,
        market: PolymarketMarket,
        market_price: float,
        our_probability: float,
        edge: float,
    ) -> Dict[str, Any]:
        """Parse trading evaluation result."""
        try:
            result = json.loads(content.strip())

            # Default values
            recommended_action = result.get("recommended_action", "HOLD")
            confidence_level = result.get("confidence_level", "medium")
            position_size = float(result.get("position_size_percent", 2.0))
            expected_value = result.get("expected_value_score", 50)

            # Validate and clamp
            position_size = max(0.0, min(5.0, position_size))
            expected_value = max(0, min(100, expected_value))

            return {
                "market_id": market.id,
                "market_question": market.question,
                "market_price": market_price,
                "our_probability": our_probability,
                "probability_edge": edge,
                "recommended_action": recommended_action,
                "confidence_level": confidence_level,
                "position_size_percent": position_size,
                "expected_value_score": expected_value,
                "risk_adjustment": result.get("risk_adjustment", ""),
                "key_risks": result.get("key_risks", []),
                "source": "llm_trading_evaluation",
                "evaluated_at": datetime.now().isoformat(),
            }

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse trading evaluation: {e}")
            return self._get_fallback_opportunity_evaluation(market, market_price, our_probability)

    def _get_fallback_analysis(self, market: PolymarketMarket) -> Dict[str, Any]:
        """Get fallback analysis when LLM is unavailable."""
        probability = market.implied_probability or 0.5

        return {
            "probability_estimate": probability,
            "confidence_score": 30,
            "reasoning": "Fallback analysis using market price only",
            "factors_considered": ["Market price"],
            "risk_assessment": "Limited analysis available",
            "source": "fallback",
            "market_id": market.id,
            "analyzed_at": datetime.now().isoformat(),
        }

    def _get_fallback_opportunity_evaluation(
        self, market: PolymarketMarket, market_price: float, our_probability: float
    ) -> Dict[str, Any]:
        """Get fallback trading evaluation."""
        edge = abs(our_probability - market_price)

        # Simple rule-based evaluation
        if edge >= 0.03 and market.volume_24h >= 1000:
            recommended_action = "BUY_YES" if our_probability > market_price else "BUY_NO"
            position_size = min(5.0, edge * 100)  # Up to 5% based on edge
        else:
            recommended_action = "HOLD"
            position_size = 0.0

        return {
            "market_id": market.id,
            "market_question": market.question,
            "market_price": market_price,
            "our_probability": our_probability,
            "probability_edge": edge,
            "recommended_action": recommended_action,
            "confidence_level": "low",
            "position_size_percent": position_size,
            "expected_value_score": 50,
            "risk_adjustment": "Fallback evaluation - limited analysis",
            "key_risks": ["Limited analysis", "Market volatility"],
            "source": "fallback",
            "evaluated_at": datetime.now().isoformat(),
        }

    def _get_fallback_report(self, markets: List[PolymarketMarket]) -> str:
        """Get fallback market report."""
        if not markets:
            return "# Market Analysis Report\n\nNo markets available for analysis."

        report_lines = [
            "# Polymarket Analysis Report",
            f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            f"*Markets Analyzed: {len(markets)}*",
            "",
            "## Executive Summary",
            f"Analyzed {len(markets)} prediction markets across various categories.",
            "Limited analysis available (LLM not accessible).",
            "",
            "## Market Categories",
        ]

        # Count by category
        categories = {}
        for market in markets:
            cat = market.category.value
            categories[cat] = categories.get(cat, 0) + 1

        for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
            report_lines.append(f"- **{cat}**: {count} markets")

        report_lines.extend(
            [
                "",
                "## Trading Considerations",
                "- **Volume Focus**: Consider markets with >$1,000 daily volume",
                "- **Edge Requirement**: Look for >3% probability edge",
                "- **Risk Management**: Limit positions to 5% of portfolio",
                "",
                "## Notes",
                "This is a fallback report. Enable LLM analysis for detailed insights.",
            ]
        )

        return "\n".join(report_lines)

    def _get_time_to_resolution(self, market: PolymarketMarket) -> str:
        """Get time to resolution description."""
        if not market.end_time:
            return "Unknown"

        from datetime import datetime

        now = datetime.now()
        delta = market.end_time - now

        if delta.days > 365:
            return f"{delta.days // 365} years"
        elif delta.days > 30:
            return f"{delta.days // 30} months"
        elif delta.days > 0:
            return f"{delta.days} days"
        elif delta.seconds > 3600:
            return f"{delta.seconds // 3600} hours"
        else:
            return "Less than 1 hour"
