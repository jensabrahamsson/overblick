"""
YAML-driven single-turn scenario tests for all personalities.

Each personality has a scenarios/<name>.yaml file defining test cases with
assertions. Tests are parametrized: one test per scenario per personality.

Run with:
    pytest tests/personalities/test_single_turn_scenarios.py -v -s -m llm

Tests are marked @pytest.mark.llm and skip if Ollama is not running.
"""

import pytest

from overblick.personalities import build_system_prompt, load_personality, list_personalities
from tests.personalities.conftest import MODEL_SLUG, generate_response
from tests.personalities.helpers import (
    apply_scenario_result,
    check_assertions,
    load_scenarios,
)

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
        """Run a single scenario: send message, check assertions."""
        personality = load_personality(personality_name)
        prompt = build_system_prompt(personality, model_slug=MODEL_SLUG)

        response = await generate_response(
            ollama_client,
            prompt,
            scenario["user_message"],
        )

        result = check_assertions(response, scenario.get("assertions", {}), personality)
        apply_scenario_result(result, response)
