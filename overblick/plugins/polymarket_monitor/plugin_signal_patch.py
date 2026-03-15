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
            "urgency": opportunity.urgency,
            "expected_value": float(opportunity.expected_value),
        }

        try:
            await self.ctx.event_bus.emit("polymarket.trading_signal", **signal_data)
            logger.info(
                "PolymarketMonitor: published trading signal for %s (edge: %.1%%, confidence: %.0f%%)",
                opportunity.market_question[:60],
                opportunity.probability_edge,
                opportunity.confidence_score,
            )
        except Exception as e:
            logger.error("PolymarketMonitor: failed to publish trading signal: %s", e)
