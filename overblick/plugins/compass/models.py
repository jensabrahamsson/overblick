"""Pydantic models for the Compass plugin."""

import time
from typing import Optional

from pydantic import BaseModel, Field


class StyleMetrics(BaseModel):
    """Stylometric measurements for a text sample."""

    avg_sentence_length: float = 0.0
    avg_word_length: float = 0.0
    vocabulary_richness: float = 0.0  # Type-token ratio
    punctuation_frequency: float = 0.0
    question_ratio: float = 0.0
    exclamation_ratio: float = 0.0
    comma_frequency: float = 0.0
    formality_score: float = 0.0
    word_count: int = 0


class BaselineProfile(BaseModel):
    """Established baseline metrics for an identity."""

    identity_name: str
    metrics: StyleMetrics
    sample_count: int = 0
    established_at: float = Field(default_factory=time.time)
    # Standard deviations for each metric (for drift calculation)
    std_devs: dict[str, float] = {}


class DriftMetrics(BaseModel):
    """Drift measurement for a single analysis window."""

    identity_name: str
    current_metrics: StyleMetrics
    drift_score: float = 0.0  # 0 = perfect match, higher = more drift
    drifted_dimensions: list[str] = []
    sample_count: int = 0
    measured_at: float = Field(default_factory=time.time)


class DriftAlert(BaseModel):
    """Alert fired when drift exceeds threshold."""

    identity_name: str
    drift_score: float
    threshold: float
    drifted_dimensions: list[str] = []
    message: str = ""
    fired_at: float = Field(default_factory=time.time)
    acknowledged: bool = False
