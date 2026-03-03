"""Pydantic models for the Stage plugin."""

import time
from typing import Any, Optional

from pydantic import BaseModel, Field


class Constraint(BaseModel):
    """A behavioral constraint to evaluate against LLM output."""

    type: str  # keyword_present, keyword_absent, max_length, min_length, tone, etc.
    description: str = ""
    value: Any = None
    expected: str = ""
    keywords: list[str] = Field(default_factory=list)


class ScenarioStep(BaseModel):
    """A single step in a behavioral scenario."""

    input: str
    constraints: list[Constraint] = Field(default_factory=list)
    description: str = ""


class Scenario(BaseModel):
    """A complete behavioral test scenario for an identity."""

    name: str
    identity: str
    description: str = ""
    steps: list[ScenarioStep] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ConstraintResult(BaseModel):
    """Result of evaluating a single constraint."""

    constraint_type: str
    passed: bool
    message: str = ""
    expected: str = ""
    actual: str = ""


class StepResult(BaseModel):
    """Result of running a single scenario step."""

    step_index: int
    input_text: str
    output_text: str = ""
    constraint_results: list[ConstraintResult] = Field(default_factory=list)
    passed: bool = True
    error: str | None = None

    @property
    def failed_constraints(self) -> list[ConstraintResult]:
        return [c for c in self.constraint_results if not c.passed]


class ScenarioResult(BaseModel):
    """Result of running a complete scenario."""

    scenario_name: str
    identity: str
    step_results: list[StepResult] = Field(default_factory=list)
    passed: bool = True
    total_constraints: int = 0
    passed_constraints: int = 0
    failed_constraints: int = 0
    duration_ms: float = 0.0
    run_at: float = Field(default_factory=time.time)
    error: str | None = None

    @property
    def pass_rate(self) -> float:
        if self.total_constraints == 0:
            return 1.0
        return self.passed_constraints / self.total_constraints
