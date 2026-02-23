"""
ActionPlanner — LLM-driven plan generation for the agentic core.

Takes observations + goals and produces a prioritized ActionPlan.
Domain-agnostic: validates action types against a plugin-provided
set of valid action strings.
"""

import json
import logging
from typing import Optional

from overblick.core.agentic.models import ActionPlan, PlannedAction
from overblick.core.agentic.prompts import planning_prompt
from overblick.core.agentic.protocols import PlanningPromptConfig

logger = logging.getLogger(__name__)


class ActionPlanner:
    """
    LLM-driven action planner.

    Takes formatted observations and goals, sends them to the LLM,
    and parses the returned JSON plan. Validates action types against
    a plugin-provided set of valid strings.
    """

    def __init__(
        self,
        llm_pipeline,
        system_prompt: str = "",
        prompt_config: Optional[PlanningPromptConfig] = None,
        valid_actions: Optional[set[str]] = None,
        audit_action: str = "agent_planning",
        complexity: str = "ultra",
    ):
        self._llm_pipeline = llm_pipeline
        self._system_prompt = system_prompt
        self._prompt_config = prompt_config or PlanningPromptConfig()
        self._valid_actions = valid_actions
        self._audit_action = audit_action
        self._complexity = complexity

    async def plan(
        self,
        observations: str,
        goals: str,
        recent_actions: str = "",
        learnings: str = "",
        extra_context: str = "",
        max_actions: int = 5,
    ) -> ActionPlan:
        """
        Generate an action plan from the current world state.

        Returns:
            ActionPlan with prioritized actions
        """
        if not self._llm_pipeline:
            logger.warning("Agent planner: no LLM pipeline available")
            return ActionPlan()

        messages = planning_prompt(
            system_prompt=self._system_prompt,
            config=self._prompt_config,
            observations=observations,
            goals=goals,
            recent_actions=recent_actions,
            learnings=learnings,
            extra_context=extra_context,
            max_actions=max_actions,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action=self._audit_action,
                skip_preflight=True,
                complexity=self._complexity,
                priority="low",
            )

            if not result or result.blocked or not result.content:
                logger.warning("Agent planner: LLM returned no plan")
                return ActionPlan()

            plan = self._parse_plan(result.content.strip(), max_actions)
            logger.info(
                "Agent planner: generated plan with %d actions (reasoning: %s)",
                len(plan.actions),
                plan.reasoning[:100] if plan.reasoning else "none",
            )
            return plan

        except Exception as e:
            logger.error("Agent planner: planning failed: %s", e, exc_info=True)
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

            # Validate against plugin's valid actions if provided
            if self._valid_actions and action_type_str not in self._valid_actions:
                logger.debug("Agent planner: skipping unknown action type: %s", action_type_str)
                continue

            try:
                action = PlannedAction(
                    action_type=action_type_str,
                    target=raw_action.get("target", ""),
                    target_number=int(raw_action.get("target_number", 0)),
                    repo=raw_action.get("repo", ""),
                    priority=int(raw_action.get("priority", 50)),
                    reasoning=raw_action.get("reasoning", ""),
                    params=raw_action.get("params", {}),
                )
                actions.append(action)
            except (ValueError, TypeError) as e:
                logger.debug("Agent planner: failed to parse action: %s", e)
                continue

        # Sort by priority (highest first)
        actions.sort(key=lambda a: a.priority, reverse=True)

        return ActionPlan(actions=actions, reasoning=reasoning)

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

        logger.warning("Agent planner: could not parse JSON from response")
        return None
