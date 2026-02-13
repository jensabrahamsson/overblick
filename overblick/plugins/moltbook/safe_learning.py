"""Backward-compatibility shim — module moved to overblick.capabilities.knowledge.safe_learning."""
from overblick.capabilities.knowledge.safe_learning import *  # noqa: F401,F403
from overblick.capabilities.knowledge.safe_learning import (  # noqa: F401
    SafeLearningModule, LearningCategory, ProposedLearning,
    ReviewResult, extract_potential_learnings, REVIEW_PROMPT,
)
