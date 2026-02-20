"""Pydantic models for the Kontrast plugin."""

import time
from typing import Optional

from pydantic import BaseModel, Field


class PerspectiveEntry(BaseModel):
    """A single identity's perspective on a topic."""

    identity_name: str
    display_name: str = ""
    content: str
    generated_at: float = Field(default_factory=time.time)
    word_count: int = 0

    def model_post_init(self, __context) -> None:
        if not self.word_count:
            object.__setattr__(self, "word_count", len(self.content.split()))


class KontrastPiece(BaseModel):
    """A complete Kontrast piece — multiple perspectives on one topic."""

    topic: str
    topic_hash: str = ""
    source_summary: str = ""
    perspectives: list[PerspectiveEntry] = []
    created_at: float = Field(default_factory=time.time)
    article_count: int = 0

    @property
    def identity_count(self) -> int:
        return len(self.perspectives)

    @property
    def is_complete(self) -> bool:
        return len(self.perspectives) >= 2
