"""Tests for Moltbook decision engine."""

import pytest
from overblick.plugins.moltbook.decision_engine import DecisionEngine, EngagementDecision


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


class TestHostileDetection:
    """Tests for the hostile content detection in evaluate_reply."""

    def setup_method(self):
        self.engine = DecisionEngine(
            interest_keywords=["crypto"],
            engagement_threshold=30.0,
            self_agent_name="TestBot",
        )

    def test_hostile_slur_detected(self):
        decision = self.engine.evaluate_reply(
            "you stupid nigger bot", "Post", "Troll",
        )
        assert decision.hostile is True
        assert not decision.should_engage
        assert decision.action == "skip"

    def test_hostile_kys_detected(self):
        decision = self.engine.evaluate_reply(
            "just kys already", "Post", "Troll",
        )
        assert decision.hostile is True

    def test_hostile_fuck_off_detected(self):
        decision = self.engine.evaluate_reply(
            "fuck off you stupid bot", "Post", "Troll",
        )
        assert decision.hostile is True

    def test_hostile_spam_detected(self):
        decision = self.engine.evaluate_reply(
            "Buy now click here https://spam.example.com", "Post", "Spammer",
        )
        assert decision.hostile is True

    def test_normal_comment_not_hostile(self):
        decision = self.engine.evaluate_reply(
            "Interesting perspective on crypto markets!", "Post", "Regular",
        )
        assert decision.hostile is False
        assert decision.score >= 30.0  # Base score

    def test_critical_comment_not_hostile(self):
        decision = self.engine.evaluate_reply(
            "I disagree with your analysis. The data shows otherwise.",
            "Post", "Critic",
        )
        assert decision.hostile is False

    def test_hostile_field_default_false(self):
        d = EngagementDecision(
            should_engage=True, score=50.0, action="comment", reason="test",
        )
        assert d.hostile is False


class TestEngagementDecision:
    def test_dataclass_fields(self):
        d = EngagementDecision(
            should_engage=True, score=50.0, action="comment",
            reason="test", matched_keywords=["ai"],
        )
        assert d.should_engage
        assert d.score == 50.0
        assert d.matched_keywords == ["ai"]

    def test_hostile_flag(self):
        d = EngagementDecision(
            should_engage=False, score=0.0, action="skip",
            reason="hostile", hostile=True,
        )
        assert d.hostile is True
