"""
Tests for ActionPlanner — LLM-driven plan generation.
"""

import json

import pytest
from unittest.mock import AsyncMock

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.github.models import ActionType
from overblick.plugins.github.planner import ActionPlanner


class TestActionPlanner:
    """Test ActionPlanner plan generation and parsing."""

    @pytest.fixture
    def mock_pipeline(self):
        return AsyncMock()

    def test_parse_valid_plan(self):
        """Planner parses a valid JSON plan."""
        planner = ActionPlanner(llm_pipeline=None, system_prompt="test")

        raw = json.dumps({
            "reasoning": "Dependabot PR with passing CI should be merged",
            "actions": [
                {
                    "action_type": "merge_pr",
                    "target": "PR #42",
                    "target_number": 42,
                    "repo": "owner/repo",
                    "priority": 90,
                    "reasoning": "Safe patch bump",
                },
                {
                    "action_type": "notify_owner",
                    "target": "CI failure on PR #10",
                    "target_number": 10,
                    "repo": "owner/repo",
                    "priority": 70,
                    "reasoning": "Owner should know",
                },
            ],
        })

        plan = planner._parse_plan(raw, max_actions=5)

        assert len(plan.actions) == 2
        assert plan.reasoning == "Dependabot PR with passing CI should be merged"
        assert plan.actions[0].action_type == ActionType.MERGE_PR
        assert plan.actions[0].target_number == 42
        # Actions should be sorted by priority (descending)
        assert plan.actions[0].priority >= plan.actions[1].priority

    def test_parse_plan_in_markdown_fence(self):
        """Planner extracts JSON from markdown code block."""
        planner = ActionPlanner(llm_pipeline=None, system_prompt="test")

        raw = '```json\n{"reasoning": "test", "actions": [{"action_type": "skip", "target": "", "target_number": 0, "repo": "", "priority": 50, "reasoning": "nothing to do"}]}\n```'

        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == ActionType.SKIP

    def test_parse_invalid_json(self):
        """Planner returns empty plan for unparseable output."""
        planner = ActionPlanner(llm_pipeline=None, system_prompt="test")

        plan = planner._parse_plan("this is not json at all", max_actions=5)
        assert len(plan.actions) == 0

    def test_parse_unknown_action_type(self):
        """Planner skips unknown action types."""
        planner = ActionPlanner(llm_pipeline=None, system_prompt="test")

        raw = json.dumps({
            "reasoning": "test",
            "actions": [
                {"action_type": "unknown_action", "target": "", "target_number": 0, "repo": "", "priority": 50},
                {"action_type": "skip", "target": "", "target_number": 0, "repo": "", "priority": 50, "reasoning": "ok"},
            ],
        })

        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == ActionType.SKIP

    def test_parse_respects_max_actions(self):
        """Planner caps actions at max_actions."""
        planner = ActionPlanner(llm_pipeline=None, system_prompt="test")

        actions = [
            {"action_type": "skip", "target": f"#{i}", "target_number": i, "repo": "", "priority": 50, "reasoning": ""}
            for i in range(10)
        ]
        raw = json.dumps({"reasoning": "test", "actions": actions})

        plan = planner._parse_plan(raw, max_actions=3)
        assert len(plan.actions) == 3

    @pytest.mark.asyncio
    async def test_plan_with_llm(self, mock_pipeline):
        """Full plan generation with mocked LLM."""
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content=json.dumps({
                "reasoning": "All good",
                "actions": [
                    {
                        "action_type": "skip",
                        "target": "",
                        "target_number": 0,
                        "repo": "owner/repo",
                        "priority": 50,
                        "reasoning": "Nothing to do",
                    },
                ],
            }),
        ))

        planner = ActionPlanner(llm_pipeline=mock_pipeline, system_prompt="test")
        plan = await planner.plan(
            observations="No open PRs or issues",
            goals="Keep repo healthy",
        )

        assert len(plan.actions) == 1
        assert plan.reasoning == "All good"
        mock_pipeline.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_plan_no_llm(self):
        """Plan returns empty when no LLM is available."""
        planner = ActionPlanner(llm_pipeline=None, system_prompt="test")
        plan = await planner.plan(observations="", goals="")
        assert len(plan.actions) == 0
