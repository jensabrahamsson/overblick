"""
Scenario test assertion engine for the personality test system.

Provides assertion checking, YAML scenario loading, and utility functions
for validating LLM responses against personality definitions.

Scenarios are organized by LLM model:

    tests/personalities/scenarios/
        qwen3_8b/           # Tuned for Qwen3:8b
            anomal.yaml
            blixt.yaml
            conversations/
            forum_posts/
        mistral_7b/         # Tuned for Mistral 7B
            ...

Set the model via OVERBLICK_TEST_MODEL env var (default: qwen3_8b).

Assertion types:
    Hard (cause test failure):
        - must_contain_any: keywords list with optional min_matches
        - must_not_contain: list of forbidden strings
        - check_banned_words: validate against personality's banned word list
        - min_length / max_length: character count bounds
        - must_contain_question: response must include "?"

    Soft (signal prompt tuning needed via pytest.xfail):
        - tone_keywords: expected tone markers
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from overblick.personalities import Personality

# Base directory for scenario YAML files
_SCENARIOS_BASE = Path(__file__).parent / "scenarios"


def _model_slug() -> str:
    """
    Get the current LLM model slug for scenario lookup.

    Reads OVERBLICK_TEST_MODEL env var, defaulting to 'qwen3_8b'.
    The slug is used to select the scenario directory.

    Examples:
        qwen3_8b, mistral_7b, llama3_8b, gpt4o
    """
    return os.environ.get("OVERBLICK_TEST_MODEL", "qwen3_8b")


def _scenarios_dir() -> Path:
    """Get the model-specific scenario directory."""
    model = _model_slug()
    model_dir = _SCENARIOS_BASE / model
    if not model_dir.exists():
        available = [d.name for d in _SCENARIOS_BASE.iterdir() if d.is_dir()]
        raise FileNotFoundError(
            f"No scenarios for model '{model}'. "
            f"Available: {available}. "
            f"Set OVERBLICK_TEST_MODEL or create {model_dir}"
        )
    return model_dir


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """
    Result of running assertion checks against an LLM response.

    Attributes:
        passed: True if all hard assertions passed.
        failures: List of human-readable failure messages.
        is_soft_failure: True when only soft assertions failed (prompt tuning
            signal, not a hard test failure).
    """

    passed: bool
    failures: list[str] = field(default_factory=list)
    is_soft_failure: bool = False


# ---------------------------------------------------------------------------
# Core assertion engine
# ---------------------------------------------------------------------------

def check_assertions(
    response: str,
    assertions: dict[str, Any],
    personality: Personality,
) -> ScenarioResult:
    """
    Validate an LLM response against a set of assertions.

    Args:
        response: The raw text response from the LLM.
        assertions: Dict of assertion type -> parameters (typically from YAML).
        personality: The Personality object under test, used for banned words.

    Returns:
        ScenarioResult with pass/fail status and any failure messages.

    Supported assertion keys:
        must_contain_any   - dict with 'keywords' (list[str]) and optional
                             'min_matches' (int, default 1). Case-insensitive.
        must_not_contain   - list[str] of forbidden strings. Case-insensitive.
        check_banned_words - bool; if True, checks personality's banned words
                             using whole-word regex matching.
        min_length         - int; minimum response length in characters.
        max_length         - int; maximum response length in characters.
        must_contain_question - bool; response must contain '?'.
        tone_keywords      - dict with 'expected' (list[str]); soft assertion,
                             at least one keyword should appear.
    """
    hard_failures: list[str] = []
    soft_failures: list[str] = []
    response_lower = response.lower()

    # --- must_contain_any (hard) ---
    mca = assertions.get("must_contain_any")
    if mca is not None:
        keywords: list[str] = mca.get("keywords", [])
        min_matches: int = mca.get("min_matches", 1)
        matches = [kw for kw in keywords if kw.lower() in response_lower]
        if len(matches) < min_matches:
            hard_failures.append(
                f"must_contain_any: expected at least {min_matches} of "
                f"{keywords!r}, found {len(matches)} ({matches!r})"
            )

    # --- must_not_contain (hard) ---
    mnc = assertions.get("must_not_contain")
    if mnc is not None:
        for forbidden in mnc:
            if forbidden.lower() in response_lower:
                hard_failures.append(
                    f"must_not_contain: found forbidden string {forbidden!r}"
                )

    # --- check_banned_words (hard) ---
    if assertions.get("check_banned_words", False):
        banned_words = personality.get_banned_words()
        violations = find_banned_word_violations(response, banned_words)
        if violations:
            hard_failures.append(
                f"check_banned_words: found banned words {violations!r}"
            )

    # --- min_length (hard) ---
    min_len = assertions.get("min_length")
    if min_len is not None:
        if len(response) < min_len:
            hard_failures.append(
                f"min_length: response is {len(response)} chars, "
                f"minimum is {min_len}"
            )

    # --- max_length (hard) ---
    max_len = assertions.get("max_length")
    if max_len is not None:
        if len(response) > max_len:
            hard_failures.append(
                f"max_length: response is {len(response)} chars, "
                f"maximum is {max_len}"
            )

    # --- must_contain_question (hard) ---
    if assertions.get("must_contain_question", False):
        if "?" not in response:
            hard_failures.append(
                "must_contain_question: no '?' found in response"
            )

    # --- tone_keywords (soft) ---
    tone = assertions.get("tone_keywords")
    if tone is not None:
        expected: list[str] = tone.get("expected", [])
        if expected:
            tone_matches = [kw for kw in expected if kw.lower() in response_lower]
            if not tone_matches:
                soft_failures.append(
                    f"tone_keywords: none of {expected!r} found "
                    f"(prompt tuning signal)"
                )

    # --- Build result ---
    all_failures = hard_failures + soft_failures

    if hard_failures:
        return ScenarioResult(
            passed=False,
            failures=all_failures,
            is_soft_failure=False,
        )

    if soft_failures:
        return ScenarioResult(
            passed=False,
            failures=all_failures,
            is_soft_failure=True,
        )

    return ScenarioResult(passed=True)


def apply_scenario_result(result: ScenarioResult, response: str) -> None:
    """
    Apply a ScenarioResult to the current test.

    Raises AssertionError for hard failures and calls pytest.xfail for
    soft failures. Does nothing if the result passed.

    Args:
        result: The ScenarioResult from check_assertions.
        response: The original LLM response (included in failure messages).
    """
    if result.passed:
        return

    detail = "\n".join(f"  - {f}" for f in result.failures)

    if result.is_soft_failure:
        pytest.xfail(
            f"Soft assertion failed (prompt tuning signal):\n"
            f"{detail}\nResponse: {response[:500]}"
        )
    else:
        raise AssertionError(
            f"Hard assertion failed:\n{detail}\nResponse: {response[:500]}"
        )


# ---------------------------------------------------------------------------
# Banned word detection
# ---------------------------------------------------------------------------

def find_banned_word_violations(
    response: str,
    banned_words: list[str],
) -> list[str]:
    """
    Check for banned words using whole-word regex matching.

    Uses \\b word boundaries to prevent false positives (e.g. "ser" should
    not match inside "observer").

    Args:
        response: The LLM response text to check.
        banned_words: List of words that should not appear.

    Returns:
        List of banned words that were found in the response.
    """
    response_lower = response.lower()
    violations: list[str] = []
    for word in banned_words:
        pattern = r"\b" + re.escape(word.lower()) + r"\b"
        if re.search(pattern, response_lower):
            violations.append(word)
    return violations


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def jaccard_similarity(text_a: str, text_b: str) -> float:
    """
    Compute word-level Jaccard similarity between two texts.

    Jaccard similarity = |A intersection B| / |A union B|

    Useful for anti-repetition tests: if two responses to the same prompt
    have very high Jaccard similarity, the LLM may be producing templated
    output rather than natural variation.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Float between 0.0 (no overlap) and 1.0 (identical word sets).
        Returns 0.0 if both texts are empty.
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())

    if not words_a and not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b

    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# YAML loaders
# ---------------------------------------------------------------------------

def _load_yaml_file(path: Path) -> Any:
    """Load a YAML file, raising FileNotFoundError if it does not exist."""
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f) or []


def load_scenarios(personality_name: str) -> list[dict[str, Any]]:
    """
    Load scenario definitions for a personality.

    Reads from tests/personalities/scenarios/{personality_name}.yaml.

    Each scenario dict has:
        id: str           - unique scenario identifier
        category: str     - scenario category (e.g. "voice", "expertise")
        user_message: str - the prompt to send to the LLM
        assertions: dict  - assertion definitions for check_assertions()

    Args:
        personality_name: Name of the personality (e.g. "blixt", "bjork").

    Returns:
        List of scenario dicts.

    Raises:
        FileNotFoundError: If the scenario file does not exist.
    """
    path = _scenarios_dir() / f"{personality_name}.yaml"
    data = _load_yaml_file(path)

    if isinstance(data, dict):
        return data.get("scenarios", [])
    if isinstance(data, list):
        return data

    return []


def load_conversations(personality_name: str) -> list[dict[str, Any]]:
    """
    Load multi-turn conversation definitions for a personality.

    Reads from tests/personalities/scenarios/conversations/{personality_name}_conversations.yaml.

    Each conversation dict has:
        id: str            - unique conversation identifier
        description: str   - human-readable description
        turns: list[dict]  - list of turns, each with:
            content: str       - the user message for this turn
            assertions: dict   - assertion definitions for check_assertions()

    Args:
        personality_name: Name of the personality (e.g. "blixt", "bjork").

    Returns:
        List of conversation dicts.

    Raises:
        FileNotFoundError: If the conversation file does not exist.
    """
    path = (
        _scenarios_dir()
        / "conversations"
        / f"{personality_name}_conversations.yaml"
    )
    data = _load_yaml_file(path)

    if isinstance(data, dict):
        return data.get("conversations", [])
    if isinstance(data, list):
        return data

    return []


def load_forum_posts(personality_name: str) -> list[dict[str, Any]]:
    """
    Load forum post scenario definitions for a personality.

    Reads from tests/personalities/scenarios/forum_posts/{personality_name}_posts.yaml.

    Each post spec dict has:
        id: str             - unique post identifier
        description: str    - human-readable description
        post_content: str   - the forum post content to react to
        assertions: dict    - assertion definitions for check_assertions()

    Args:
        personality_name: Name of the personality (e.g. "blixt", "bjork").

    Returns:
        List of post spec dicts.

    Raises:
        FileNotFoundError: If the forum post file does not exist.
    """
    path = (
        _scenarios_dir()
        / "forum_posts"
        / f"{personality_name}_posts.yaml"
    )
    data = _load_yaml_file(path)

    if isinstance(data, dict):
        return data.get("forum_posts", data.get("posts", []))
    if isinstance(data, list):
        return data

    return []
