"""
ActionPlanner — LLM-driven plan generation for the GitHub agent.

Takes observations + goals and produces a prioritized ActionPlan.
Uses complexity="ultra" to route through Devstral (128K context)
for deep reasoning about repository state.
"""

import json
import logging
from typing import Optional

from overblick.plugins.github.models import ActionPlan, ActionType, PlannedAction
from overblick.plugins.github.prompts import planning_prompt

logger = logging.getLogger(__name__)

# Valid action types for validation
_VALID_ACTIONS = {a.value for a in ActionType}


class ActionPlanner:
    """
    LLM-driven action planner for the GitHub agent.

    Takes formatted observations and goals, sends them to the LLM
    with complexity=ultra, and parses the returned JSON plan.
    """

    def __init__(self, llm_pipeline, system_prompt: str = ""):
        self._llm_pipeline = llm_pipeline
        self._system_prompt = system_prompt

    async def plan(
        self,
        observations: str,
        goals: str,
        recent_actions: str = "",
        learnings: str = "",
        owner_commands: str = "",
        max_actions: int = 5,
    ) -> ActionPlan:
        """
        Generate an action plan from the current world state.

        Args:
            observations: Formatted observation text
            goals: Formatted goals text
            recent_actions: Recent action history
            learnings: Agent learnings
            owner_commands: Commands from owner (Telegram)
            max_actions: Maximum number of actions to plan

        Returns:
            ActionPlan with prioritized actions
        """
        if not self._llm_pipeline:
            logger.warning("GitHub planner: no LLM pipeline available")
            return ActionPlan()

        messages = planning_prompt(
            system_prompt=self._system_prompt,
            observations=observations,
            goals=goals,
            recent_actions=recent_actions,
            learnings=learnings,
            owner_commands=owner_commands,
            max_actions=max_actions,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="github_agent_planning",
                skip_preflight=True,
                complexity="ultra",
                priority="low",
            )

            if not result or result.blocked or not result.content:
                logger.warning("GitHub planner: LLM returned no plan")
                return ActionPlan()

            plan = self._parse_plan(result.content.strip(), max_actions)
            logger.info(
                "GitHub planner: generated plan with %d actions (reasoning: %s)",
                len(plan.actions),
                plan.reasoning[:100] if plan.reasoning else "none",
            )
            return plan

        except Exception as e:
            logger.error("GitHub planner: planning failed: %s", e, exc_info=True)
            return ActionPlan()

    def _parse_plan(self, raw: str, max_actions: int) -> ActionPlan:
        """Parse LLM output into an ActionPlan."""
        data = self._extract_json(raw)
        if not data:
            return ActionPlan()

        reasoning = data.get("reasoning", "")
        raw_actions = data.get("actions", [])
        if not isinstance(raw_actions, list):
            return ActionPlan(reasoning=reasoning)

        actions: list[PlannedAction] = []
        for raw_action in raw_actions[:max_actions]:
            if not isinstance(raw_action, dict):
                continue

            action_type_str = raw_action.get("action_type", "skip")
            if action_type_str not in _VALID_ACTIONS:
                logger.debug("GitHub planner: skipping unknown action type: %s", action_type_str)
                continue

            try:
                action = PlannedAction(
                    action_type=ActionType(action_type_str),
                    target=raw_action.get("target", ""),
                    target_number=int(raw_action.get("target_number", 0)),
                    repo=raw_action.get("repo", ""),
                    priority=int(raw_action.get("priority", 50)),
                    reasoning=raw_action.get("reasoning", ""),
                    params=raw_action.get("params", {}),
                )
                actions.append(action)
            except (ValueError, TypeError) as e:
                logger.debug("GitHub planner: failed to parse action: %s", e)
                continue

        # Sort by priority (highest first)
        actions.sort(key=lambda a: a.priority, reverse=True)

        return ActionPlan(
            actions=actions,
            reasoning=reasoning,
        )

    @staticmethod
    def _extract_json(raw: str) -> Optional[dict]:
        """Extract JSON object from LLM response (handles markdown fences)."""
        # Try direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in markdown code block
        for fence in ("```json", "```"):
            start = raw.find(fence)
            if start >= 0:
                start += len(fence)
                end = raw.find("```", start)
                if end > start:
                    try:
                        return json.loads(raw[start:end].strip())
                    except json.JSONDecodeError:
                        pass

        # Try to find raw JSON object
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

        logger.warning("GitHub planner: could not parse JSON from response")
        return None
