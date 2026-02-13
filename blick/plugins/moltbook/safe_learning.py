"""Backward-compatibility shim — module moved to blick.capabilities.knowledge.safe_learning."""
from blick.capabilities.knowledge.safe_learning import *  # noqa: F401,F403
from blick.capabilities.knowledge.safe_learning import (  # noqa: F401
    SafeLearningModule, LearningCategory, ProposedLearning,
    ReviewResult, extract_potential_learnings, REVIEW_PROMPT,
)
