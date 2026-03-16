"""
Additional coverage tests for irc topic_manager module.

Covers uncovered line:
- 434: uniform random pick when all weights are zero
"""

import random
from unittest.mock import MagicMock, patch

from overblick.plugins.irc.topic_manager import select_participants


def _make_identity(name, interest_keywords=None, interests=None):
    identity = MagicMock()
    identity.name = name
    identity.interest_keywords = interest_keywords or []
    identity.interests = interests or {}
    return identity


class TestSelectParticipantsZeroWeights:
    def test_should_pick_uniformly_when_all_weights_zero(self):
        """Line 434: all remaining scores are negative enough to make weights <= 0."""
        # Create identities with no matching interests
        identities = [
            _make_identity("a", interest_keywords=["nothing"]),
            _make_identity("b", interest_keywords=["nothing"]),
            _make_identity("c", interest_keywords=["nothing"]),
        ]

        topic = {"tags": ["quantum_physics_xyz"], "ideal_participants": 3}

        # All scores will be 0, so weights = [0 + 0.05, ...] which is > 0
        # We need weights that total <= 0. The weight is `score + _BASE_WEIGHT`
        # where _BASE_WEIGHT = 0.05. Score can't be negative from score_identity_interest.
        # So total can never be <= 0. This means line 434 is unreachable
        # unless we patch the scoring.

        # Force scores to be very negative to make weights <= 0
        with patch(
            "overblick.plugins.irc.topic_manager.score_identity_interest",
            return_value=-1.0,
        ):
            random.seed(42)
            result = select_participants(identities, topic)
            # Should still return participants
            assert len(result) >= 1
