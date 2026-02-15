"""
Tests for DecisionEngine — identity-driven engagement scoring.
"""

import pytest

from overblick.capabilities.engagement.decision_engine import (
    DecisionEngine,
    EngagementDecision,
)


class TestDecisionEngine:
    def test_initialization(self):
        engine = DecisionEngine(
            interest_keywords=["AI", "crypto", "philosophy"],
            engagement_threshold=35.0,
            fuzzy_threshold=75,
            self_agent_name="TestBot",
        )
        assert engine._threshold == 35.0
        assert "ai" in engine._keywords
        assert "crypto" in engine._keywords
        assert engine._self_name == "testbot"

    def test_default_initialization(self):
        engine = DecisionEngine()
        assert engine._keywords == []
        assert engine._threshold == 35.0
        assert engine._fuzzy_threshold == 75

    def test_skip_own_post(self):
        engine = DecisionEngine(
            interest_keywords=["AI"],
            self_agent_name="MyBot",
        )
        decision = engine.evaluate_post(
            title="Interesting AI Topic",
            content="This is about AI",
            agent_name="MyBot",
        )
        assert decision.should_engage is False
        assert decision.action == "skip"
        assert decision.reason == "own post"
        assert decision.score == 0.0

    def test_keyword_exact_match(self):
        engine = DecisionEngine(
            interest_keywords=["AI", "crypto"],
            engagement_threshold=30.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="AI Revolution",
            content="Let's discuss AI and crypto together with enough content to avoid length penalty",
            agent_name="OtherBot",
        )
        # Each keyword appears once: 10 (AI in title + AI in content = just 20 total for "ai")
        # + 20 (crypto) = 30. Keywords only match once per text.
        # Actually: text is lowercased title+content, so "ai" appears twice but only counts once (20)
        # and "crypto" appears once (20) = 40 total? Let me check...
        # Actually the code does: for keyword in keywords, if keyword in text: score += 20
        # So if "ai" is in the combined text, it gets 20. "crypto" gets 20. Total 40.
        # But content needs to be >50 chars to avoid -10 penalty
        assert decision.should_engage is True
        assert decision.score >= 30.0  # At least threshold
        assert "ai" in decision.matched_keywords
        assert "crypto" in decision.matched_keywords

    def test_question_boost(self):
        engine = DecisionEngine(
            interest_keywords=["AI"],
            engagement_threshold=30.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="AI Question",
            content="What do you think about AI and its implications for society?",
            agent_name="OtherBot",
        )
        # 20 (AI) + 10 (question) = 30
        assert decision.score >= 30.0
        assert decision.should_engage is True

    def test_submolt_relevance_boost(self):
        engine = DecisionEngine(
            interest_keywords=["AI"],
            engagement_threshold=20.0,  # Lower threshold for this test
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="AI Topic",
            content="AI discussion with enough content here to avoid length penalty please",
            agent_name="OtherBot",
            submolt="ai",
        )
        # 20 (AI) + 5 (submolt) = 25
        assert decision.score >= 20.0
        assert decision.should_engage is True

    def test_length_penalty(self):
        engine = DecisionEngine(
            interest_keywords=["AI"],
            engagement_threshold=30.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="AI",
            content="Short",  # Less than 50 chars
            agent_name="OtherBot",
        )
        # 20 (AI) - 10 (length penalty) = 10
        assert decision.score <= 10.0
        assert decision.should_engage is False

    def test_upvote_action(self):
        engine = DecisionEngine(
            interest_keywords=["AI"],
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="AI Topic",
            content="Some AI discussion here with enough length to avoid penalty",
            agent_name="OtherBot",
        )
        # Score is 20 (AI) which is > 0 but < threshold
        assert decision.score > 0
        assert decision.score < 35.0
        assert decision.action == "upvote"
        assert decision.should_engage is False

    def test_skip_action(self):
        engine = DecisionEngine(
            interest_keywords=["AI"],
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="Random Topic",
            content="Nothing interesting here",
            agent_name="OtherBot",
        )
        assert decision.score <= 0
        assert decision.action == "skip"
        assert decision.should_engage is False

    def test_evaluate_reply_skip_own_comment(self):
        engine = DecisionEngine(
            self_agent_name="MyBot",
        )
        decision = engine.evaluate_reply(
            comment_content="Great post!",
            original_post_title="My Post",
            commenter_name="MyBot",
        )
        assert decision.should_engage is False
        assert decision.action == "skip"
        assert decision.reason == "own comment"

    def test_evaluate_reply_base_score(self):
        engine = DecisionEngine(
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_reply(
            comment_content="I agree with your points.",
            original_post_title="My Post",
            commenter_name="OtherBot",
        )
        # Base score is 30 (someone replied to us)
        assert decision.score == 30.0
        assert decision.should_engage is False  # Just below threshold

    def test_evaluate_reply_question_boost(self):
        engine = DecisionEngine(
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_reply(
            comment_content="What do you think about this?",
            original_post_title="My Post",
            commenter_name="OtherBot",
        )
        # 30 (base) + 20 (question) = 50
        assert decision.score >= 50.0
        assert decision.should_engage is True
        assert decision.action == "comment"

    def test_evaluate_reply_length_boost(self):
        engine = DecisionEngine(
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        long_comment = "A" * 150  # More than 100 chars
        decision = engine.evaluate_reply(
            comment_content=long_comment,
            original_post_title="My Post",
            commenter_name="OtherBot",
        )
        # 30 (base) + 10 (length) = 40
        assert decision.score >= 40.0
        assert decision.should_engage is True

    def test_evaluate_reply_keyword_match(self):
        engine = DecisionEngine(
            interest_keywords=["AI", "crypto"],
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_reply(
            comment_content="I think AI is fascinating and crypto is the future!",
            original_post_title="My Post",
            commenter_name="OtherBot",
        )
        # 30 (base) + 15 (AI) + 15 (crypto) = 60
        assert decision.score >= 60.0
        assert decision.should_engage is True
        assert "ai" in decision.matched_keywords
        assert "crypto" in decision.matched_keywords

    def test_no_keywords_still_works(self):
        engine = DecisionEngine(
            interest_keywords=[],
            engagement_threshold=35.0,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="Some Post",
            content="Some content with enough length to avoid penalty here",
            agent_name="OtherBot",
        )
        assert decision.score >= 0
        # No keywords means only other factors count
        assert decision.should_engage is False

    def test_fuzzy_matching(self):
        engine = DecisionEngine(
            interest_keywords=["machine learning"],
            engagement_threshold=10.0,
            fuzzy_threshold=75,
            self_agent_name="TestBot",
        )
        decision = engine.evaluate_post(
            title="ML Discussion",
            content="Let's talk about machine-learning algorithms and their applications",
            agent_name="OtherBot",
        )
        # Should match "machine learning" via fuzzy matching
        assert decision.score > 0
        assert len(decision.matched_keywords) > 0
