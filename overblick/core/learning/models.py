"""
Data models for the platform-level learning system.

Learnings are per-identity knowledge items that go through ethos review
before being approved and made available for context injection.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LearningStatus(str, Enum):
    """Status of a learning in the review pipeline."""
    CANDIDATE = "candidate"   # Proposed, awaiting ethos review
    APPROVED = "approved"     # Passed ethos review
    REJECTED = "rejected"     # Failed ethos review


class Learning(BaseModel):
    """A single learning item with review lifecycle."""
    id: Optional[int] = None
    content: str                                          # The learned insight
    category: str = "general"                             # factual, social, opinion, pattern, correction
    source: str = ""                                      # "moltbook", "email", "reflection", "irc"
    source_context: str = ""                              # What triggered the learning
    status: LearningStatus = LearningStatus.CANDIDATE
    review_reason: str = ""                               # Why approved/rejected
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    embedding: Optional[list[float]] = None               # Vector for similarity search
    created_at: str = ""
    reviewed_at: Optional[str] = None
