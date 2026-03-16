"""
Tests for ActionPlanner — LLM-driven plan generation.
"""

import json
from unittest.mock import AsyncMock

import pytest

from overblick.core.agentic.planner import ActionPlanner
from overblick.core.llm.pipeline import PipelineResult


class TestActionPlanner:
    """Test ActionPlanner plan generation and parsing."""

    def test_parse_valid_plan(self):
        """Planner parses a valid JSON plan."""
        planner = ActionPlanner(
            llm_pipeline=None,
            valid_actions={"merge_pr", "notify_owner", "skip"},
        )

        raw = json.dumps(
            {
                "reasoning": "Dependabot PR with passing CI",
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
            }
        )

        plan = planner._parse_plan(raw, max_actions=5)

        assert len(plan.actions) == 2
        assert plan.reasoning == "Dependabot PR with passing CI"
        assert plan.actions[0].action_type == "merge_pr"
        assert plan.actions[0].target_number == 42
        # Sorted by priority descending
        assert plan.actions[0].priority >= plan.actions[1].priority

    def test_parse_plan_in_markdown_fence(self):
        """Planner extracts JSON from markdown code block."""
        planner = ActionPlanner(llm_pipeline=None)

        raw = (
            "```json\n"
            '{"reasoning": "test", "actions": ['
            '{"action_type": "skip", "target": "", "target_number": 0, '
            '"repo": "", "priority": 50, "reasoning": "nothing to do"}'
            "]}\n```"
        )

        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == "skip"

    def test_parse_invalid_json(self):
        """Planner returns empty plan for unparseable output."""
        planner = ActionPlanner(llm_pipeline=None)
        plan = planner._parse_plan("this is not json at all", max_actions=5)
        assert len(plan.actions) == 0

    def test_parse_unknown_action_type_filtered(self):
        """Planner skips unknown action types when valid_actions is set."""
        planner = ActionPlanner(
            llm_pipeline=None,
            valid_actions={"skip"},
        )

        raw = json.dumps(
            {
                "reasoning": "test",
                "actions": [
                    {
                        "action_type": "unknown_action",
                        "target": "",
                        "target_number": 0,
                        "repo": "",
                        "priority": 50,
                    },
                    {
                        "action_type": "skip",
                        "target": "",
                        "target_number": 0,
                        "repo": "",
                        "priority": 50,
                        "reasoning": "ok",
                    },
                ],
            }
        )

        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == "skip"

    def test_parse_no_validation_when_valid_actions_none(self):
        """When valid_actions is None, all action types are accepted."""
        planner = ActionPlanner(llm_pipeline=None, valid_actions=None)

        raw = json.dumps(
            {
                "reasoning": "test",
                "actions": [
                    {"action_type": "anything_goes", "target": "", "target_number": 0, "repo": ""},
                ],
            }
        )

        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == "anything_goes"

    def test_parse_respects_max_actions(self):
        """Planner caps actions at max_actions."""
        planner = ActionPlanner(llm_pipeline=None)

        actions = [
            {
                "action_type": "skip",
                "target": f"#{i}",
                "target_number": i,
                "repo": "",
                "priority": 50,
            }
            for i in range(10)
        ]
        raw = json.dumps({"reasoning": "test", "actions": actions})

        plan = planner._parse_plan(raw, max_actions=3)
        assert len(plan.actions) == 3

    @pytest.mark.asyncio
    async def test_plan_with_llm(self):
        """Full plan generation with mocked LLM."""
        mock_pipeline = AsyncMock()
        mock_pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(
                content=json.dumps(
                    {
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
                    }
                ),
            )
        )

        planner = ActionPlanner(llm_pipeline=mock_pipeline)
        plan = await planner.plan(
            observations="No open PRs or issues",
            goals="Keep repo healthy",
        )

        assert len(plan.actions) == 1
        assert plan.reasoning == "All good"
        mock_pipeline._chat_with_overrides.assert_called_once()

    @pytest.mark.asyncio
    async def test_plan_no_llm(self):
        """Plan returns empty when no LLM is available."""
        planner = ActionPlanner(llm_pipeline=None)
        plan = await planner.plan(observations="", goals="")
        assert len(plan.actions) == 0

    @pytest.mark.asyncio
    async def test_plan_llm_blocked(self):
        """Plan returns empty when LLM result is blocked."""
        mock_pipeline = AsyncMock()
        mock_pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(
                content=None,
                blocked=True,
                block_reason="test",
            )
        )

        planner = ActionPlanner(llm_pipeline=mock_pipeline)
        plan = await planner.plan(observations="test", goals="test")
        assert len(plan.actions) == 0

    def test_extract_json_direct(self):
        """Extract JSON from a direct JSON string."""
        data = ActionPlanner._extract_json('{"key": "value"}')
        assert data == {"key": "value"}

    def test_extract_json_with_prefix(self):
        """Extract JSON from text with surrounding content."""
        data = ActionPlanner._extract_json('Here is the result: {"key": "value"} done')
        assert data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_plan_returns_empty_when_llm_raises(self):
        """Plan returns empty ActionPlan when LLM raises an exception."""
        mock_pipeline = AsyncMock()
        mock_pipeline._chat_with_overrides = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        planner = ActionPlanner(llm_pipeline=mock_pipeline)
        plan = await planner.plan(observations="obs", goals="goals")
        assert len(plan.actions) == 0

    def test_parse_plan_non_list_actions(self):
        """Returns plan with reasoning but no actions when actions is not a list."""
        planner = ActionPlanner(llm_pipeline=None)
        raw = json.dumps({"reasoning": "some reason", "actions": "not a list"})
        plan = planner._parse_plan(raw, max_actions=5)
        assert plan.reasoning == "some reason"
        assert len(plan.actions) == 0

    def test_parse_plan_non_dict_action_item(self):
        """Non-dict items in the actions list are skipped."""
        planner = ActionPlanner(llm_pipeline=None)
        raw = json.dumps({
            "reasoning": "mixed",
            "actions": [
                "not a dict",
                {"action_type": "skip", "target": "", "target_number": 0, "repo": "", "priority": 50},
            ],
        })
        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == "skip"

    def test_parse_plan_value_error_in_action(self):
        """Actions with invalid numeric fields are skipped (ValueError/TypeError)."""
        planner = ActionPlanner(llm_pipeline=None)
        raw = json.dumps({
            "reasoning": "bad numbers",
            "actions": [
                {
                    "action_type": "skip",
                    "target": "t",
                    "target_number": "not_a_number",
                    "repo": "",
                    "priority": 50,
                },
            ],
        })
        plan = planner._parse_plan(raw, max_actions=5)
        assert len(plan.actions) == 0

    def test_extract_json_invalid_json_in_markdown_fence(self):
        """Invalid JSON inside markdown fence falls through."""
        raw = "```json\n{invalid json}\n```"
        data = ActionPlanner._extract_json(raw)
        # Falls through to raw JSON extraction which also fails on the braces
        # but finds { and } and tries to parse
        assert data is None

    def test_extract_json_invalid_json_in_raw_braces(self):
        """Invalid JSON between { and } returns None."""
        raw = "prefix {not: valid: json} suffix"
        data = ActionPlanner._extract_json(raw)
        assert data is None

    def test_extract_json_valid_json_in_non_json_fence(self):
        """Valid JSON inside plain ``` fence is extracted."""
        raw = '```\n{"key": "val"}\n```'
        data = ActionPlanner._extract_json(raw)
        assert data == {"key": "val"}
