"""Tests for StagePlugin — behavioral scenario engine."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.plugins.stage.models import (
    Constraint,
    Scenario,
    ScenarioResult,
    ScenarioStep,
    StepResult,
)
from overblick.plugins.stage.plugin import StagePlugin


class TestSetup:
    """Test plugin initialization and setup."""

    @pytest.mark.asyncio
    async def test_setup_success(self, stage_context):
        """Plugin sets up correctly."""
        plugin = StagePlugin(stage_context)
        await plugin.setup()
        assert plugin._tick_count == 0

    @pytest.mark.asyncio
    async def test_setup_audits(self, stage_context):
        """Plugin logs setup to audit log."""
        plugin = StagePlugin(stage_context)
        await plugin.setup()
        # Verify plugin_setup was called (scenario_dirs may be Path or str)
        calls = stage_context.audit_log.log.call_args_list
        setup_call = [c for c in calls if c[1].get("action") == "plugin_setup"]
        assert len(setup_call) == 1
        details = setup_call[0][1]["details"]
        assert details["plugin"] == "stage"
        assert details["identity"] == "test"


class TestTick:
    """Test the main work cycle."""

    @pytest.mark.asyncio
    async def test_tick_is_noop(self, stage_context):
        """Tick is a no-op for on-demand plugin."""
        plugin = StagePlugin(stage_context)
        await plugin.setup()
        await plugin.tick()
        assert plugin._tick_count == 1
        stage_context.llm_pipeline.chat.assert_not_called()


class TestRunScenario:
    """Test scenario execution."""

    @pytest.mark.asyncio
    async def test_run_simple_scenario(self, stage_context):
        """Runs a scenario and evaluates constraints."""
        plugin = StagePlugin(stage_context)
        await plugin.setup()

        scenario = Scenario(
            name="Test Scenario",
            identity="test",
            steps=[
                ScenarioStep(
                    input="Tell me about love",
                    constraints=[
                        Constraint(
                            type="keyword_present",
                            keywords=["love", "attachment", "connect"],
                        ),
                        Constraint(type="min_length", value=10),
                    ],
                ),
            ],
        )

        # Patch load_identity at the module level
        with patch("overblick.identities.load_identity") as mock_load:
            mock_identity = MagicMock()
            mock_identity.name = "test"
            mock_identity.display_name = "Test"
            mock_identity.llm = stage_context.identity.llm
            mock_load.return_value = mock_identity

            with patch("overblick.identities.build_system_prompt", return_value="System prompt"):
                result = await plugin.run_scenario(scenario)

        assert result.scenario_name == "Test Scenario"
        assert result.identity == "test"
        assert result.total_constraints == 2
        assert result.passed_constraints >= 1  # keyword_present should pass

    @pytest.mark.asyncio
    async def test_run_scenario_no_pipeline(self, stage_context):
        """Returns error when no LLM pipeline is available."""
        stage_context.llm_pipeline = None
        plugin = StagePlugin(stage_context)
        await plugin.setup()

        scenario = Scenario(
            name="Test",
            identity="test",
            steps=[ScenarioStep(input="Hello")],
        )
        result = await plugin.run_scenario(scenario)
        assert result.passed is False
        assert result.error == "No LLM pipeline available"

    @pytest.mark.asyncio
    async def test_run_scenario_missing_identity(self, stage_context):
        """Returns error when identity is not found."""
        plugin = StagePlugin(stage_context)
        await plugin.setup()

        scenario = Scenario(
            name="Test",
            identity="nonexistent",
            steps=[ScenarioStep(input="Hello")],
        )

        with patch("overblick.identities.load_identity", side_effect=FileNotFoundError("Not found")):
            result = await plugin.run_scenario(scenario)
        assert result.passed is False
        assert "not found" in result.error.lower()


class TestScenarioLoading:
    """Test YAML scenario loading."""

    def test_load_scenario_from_yaml(self, stage_context, tmp_path):
        """Loads scenario from YAML file."""
        yaml_content = """
name: "Cherry redirect test"
identity: cherry
description: "Test that Cherry redirects off-topic questions"
tags: [personality, redirect]
steps:
  - input: "Explain quantum computing"
    constraints:
      - type: topic_redirect
        description: "Should redirect to relationships"
      - type: max_length
        value: 500
  - input: "What about heartbreak?"
    constraints:
      - type: on_topic
        keywords: [attachment, relationship, heart, love]
"""
        yaml_file = tmp_path / "test_scenario.yaml"
        yaml_file.write_text(yaml_content)

        plugin = StagePlugin(stage_context)
        scenario = plugin.load_scenario_from_yaml(yaml_file)

        assert scenario.name == "Cherry redirect test"
        assert scenario.identity == "cherry"
        assert len(scenario.steps) == 2
        assert scenario.steps[0].constraints[0].type == "topic_redirect"
        assert scenario.steps[1].constraints[0].keywords == ["attachment", "relationship", "heart", "love"]
        assert scenario.tags == ["personality", "redirect"]


class TestModels:
    """Test Stage data models."""

    def test_scenario_result_pass_rate(self):
        result = ScenarioResult(
            scenario_name="Test",
            identity="cherry",
            total_constraints=10,
            passed_constraints=7,
            failed_constraints=3,
        )
        assert result.pass_rate == pytest.approx(0.7)

    def test_scenario_result_no_constraints(self):
        result = ScenarioResult(
            scenario_name="Test",
            identity="cherry",
            total_constraints=0,
        )
        assert result.pass_rate == 1.0

    def test_step_result_failed_constraints(self):
        from overblick.plugins.stage.models import ConstraintResult
        step = StepResult(
            step_index=0,
            input_text="Test",
            constraint_results=[
                ConstraintResult(constraint_type="a", passed=True),
                ConstraintResult(constraint_type="b", passed=False),
                ConstraintResult(constraint_type="c", passed=False),
            ],
        )
        assert len(step.failed_constraints) == 2


class TestStateManagement:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, stage_context):
        plugin = StagePlugin(stage_context)
        await plugin.setup()
        plugin._results.append(ScenarioResult(
            scenario_name="Test",
            identity="cherry",
            passed=True,
            total_constraints=5,
            passed_constraints=5,
        ))
        plugin._save_state()

        plugin2 = StagePlugin(stage_context)
        await plugin2.setup()
        assert len(plugin2._results) == 1

    @pytest.mark.asyncio
    async def test_handles_corrupt_state(self, stage_context):
        state_file = stage_context.data_dir / "stage_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("broken json")

        plugin = StagePlugin(stage_context)
        await plugin.setup()
        assert len(plugin._results) == 0


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown_saves_state(self, stage_context):
        plugin = StagePlugin(stage_context)
        await plugin.setup()
        plugin._results.append(ScenarioResult(
            scenario_name="Test",
            identity="test",
            passed=True,
        ))
        await plugin.teardown()

        state_file = stage_context.data_dir / "stage_state.json"
        assert state_file.exists()
