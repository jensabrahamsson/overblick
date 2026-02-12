"""Tests for Moltbook decision engine."""

import pytest
from blick.plugins.moltbook.decision_engine import DecisionEngine, EngagementDecision


class TestDecisionEngine:
    def setup_method(self):
        self.engine = DecisionEngine(
            interest_keywords=["crypto", "AI", "philosophy"],
            engagement_threshold=30.0,
            self_agent_name="TestBot",
        )

    def test_skips_own_posts(self):
        decision = self.engine.evaluate_post("Title", "Content", "TestBot")
        assert not decision.should_engage
        assert decision.action == "skip"
        assert decision.reason == "own post"

    def test_engages_with_keyword_match(self):
        decision = self.engine.evaluate_post(
            "The Future of Crypto",
            "Bitcoin and AI convergence discussion is really picking up momentum these days",
            "OtherAgent",
        )
        assert decision.should_engage
        assert decision.score >= 30.0
        assert len(decision.matched_keywords) > 0

    def test_skips_irrelevant_content(self):
        decision = self.engine.evaluate_post(
            "My Cat",
            "Look at my cute cat photos from this morning, they are adorable",
            "OtherAgent",
        )
        assert not decision.should_engage or decision.action == "skip"

    def test_question_boost(self):
        no_q = self.engine.evaluate_post(
            "Topic",
            "Statement about stuff that is long enough to avoid the short penalty here",
            "Other",
        )
        with_q = self.engine.evaluate_post(
            "Topic?",
            "What do you think about stuff that is long enough to avoid the penalty?",
            "Other",
        )
        assert with_q.score > no_q.score

    def test_short_content_penalty(self):
        short = self.engine.evaluate_post(
            "Interesting topic", "Good point!", "Other",
        )
        long = self.engine.evaluate_post(
            "Interesting topic",
            "Good point, I really like how you put that together and explained it well",
            "Other",
        )
        # Short content (<50 chars) gets a -10 penalty; long does not
        assert long.score > short.score

    def test_evaluate_reply_base_score(self):
        decision = self.engine.evaluate_reply(
            "Great point about crypto! I agree with your analysis on the market trends",
            "Original Post about crypto and AI technology",
            "Replier",
        )
        assert decision.score >= 30.0  # Base + keyword match

    def test_evaluate_reply_skips_self(self):
        decision = self.engine.evaluate_reply(
            "Comment text", "Original Post", "TestBot",
        )
        assert not decision.should_engage

    def test_fuzzy_matching(self):
        engine = DecisionEngine(
            interest_keywords=["cryptocurrency"],
            engagement_threshold=10.0,
            fuzzy_threshold=70,
        )
        decision = engine.evaluate_post(
            "Title",
            "Let's discuss crypto trends and blockchain innovation in today's market",
            "Other",
        )
        # "crypto" should fuzzy-match "cryptocurrency"
        assert decision.score > 0


class TestEngagementDecision:
    def test_dataclass_fields(self):
        d = EngagementDecision(
            should_engage=True, score=50.0, action="comment",
            reason="test", matched_keywords=["ai"],
        )
        assert d.should_engage
        assert d.score == 50.0
        assert d.matched_keywords == ["ai"]
