"""Pydantic models for the Spegel plugin."""

import time

from pydantic import BaseModel, Field


class Profile(BaseModel):
    """A psychological profile written by one identity about another."""

    observer_name: str
    observer_display_name: str = ""
    target_name: str
    target_display_name: str = ""
    profile_text: str
    framework_used: str = ""
    generated_at: float = Field(default_factory=time.time)


class Reflection(BaseModel):
    """A target identity's response to being profiled."""

    target_name: str
    target_display_name: str = ""
    observer_name: str
    reflection_text: str
    generated_at: float = Field(default_factory=time.time)


class SpegelPair(BaseModel):
    """A complete profiling exchange between two identities."""

    observer_name: str
    target_name: str
    profile: Profile
    reflection: Reflection
    created_at: float = Field(default_factory=time.time)
