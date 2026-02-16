"""
YAML-driven single-turn scenario tests for all personalities.

Each personality has a scenarios/<name>.yaml file defining test cases with
assertions. Tests are parametrized: one test per scenario per personality.

Run with:
    pytest tests/personalities/test_single_turn_scenarios.py -v -s -m llm

Tests are marked @pytest.mark.llm and skip if Ollama is not running.

Retry strategy: Each scenario gets up to 3 attempts (1 initial + 2 retries).
LLM responses are non-deterministic — we test that the personality *can*
produce correct responses, not that every single generation is perfect.
"""

import logging

import pytest

from overblick.identities import build_system_prompt, load_personality, list_personalities
from tests.personalities.conftest import MODEL_SLUG, generate_response
from tests.personalities.helpers import (
    apply_scenario_result,
    check_assertions,
    load_scenarios,
)

logger = logging.getLogger(__name__)

# Maximum assertion-level retries per scenario
_MAX_ASSERTION_RETRIES = 2

# Discover which personalities have scenario files
_PERSONALITIES_WITH_SCENARIOS: list[str] = []
for _name in list_personalities():
    try:
        _scenarios = load_scenarios(_name)
        if _scenarios:
            _PERSONALITIES_WITH_SCENARIOS.append(_name)
    except FileNotFoundError:
        pass


def _scenario_ids() -> list[tuple[str, dict]]:
    """Build flat list of (personality_name, scenario) tuples for parametrize."""
    pairs = []
    for name in _PERSONALITIES_WITH_SCENARIOS:
        for scenario in load_scenarios(name):
            pairs.append((name, scenario))
    return pairs


def _scenario_id_func(val):
    """Generate readable test IDs."""
    if isinstance(val, dict):
        return val.get("id", "unknown")
    return str(val)


@pytest.mark.llm
class TestSingleTurnScenarios:
    """YAML-driven single-turn personality scenario tests."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "personality_name,scenario",
        _scenario_ids(),
        ids=lambda v: _scenario_id_func(v) if isinstance(v, dict) else v,
    )
    async def test_scenario(self, ollama_client, personality_name, scenario):
        """Run a single scenario with assertion-level retry.

        If the LLM response fails hard assertions, retry up to
        _MAX_ASSERTION_RETRIES times with a fresh generation. This
        accounts for natural LLM non-determinism while still validating
        that the personality can consistently produce in-character responses.
        """
        personality = load_personality(personality_name)
        prompt = build_system_prompt(personality, model_slug=MODEL_SLUG)

        for attempt in range(_MAX_ASSERTION_RETRIES + 1):
            response = await generate_response(
                ollama_client,
                prompt,
                scenario["user_message"],
            )

            result = check_assertions(
                response, scenario.get("assertions", {}), personality,
            )

            if result.passed or result.is_soft_failure:
                break

            if attempt < _MAX_ASSERTION_RETRIES:
                logger.warning(
                    "Scenario %s-%s: assertion failed (attempt %d/%d), retrying...",
                    personality_name,
                    scenario.get("id", "?"),
                    attempt + 1,
                    _MAX_ASSERTION_RETRIES + 1,
                )

        apply_scenario_result(result, response)
