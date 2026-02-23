"""
ReflectionPipeline — LLM-driven learning extraction.

After each tick, the agent reflects on outcomes and extracts
learnings that improve future decisions.
"""

import json
import logging

from overblick.core.agentic.database import AgenticDB
from overblick.core.agentic.models import ActionOutcome, AgentLearning
from overblick.core.agentic.prompts import reflection_prompt

logger = logging.getLogger(__name__)


class ReflectionPipeline:
    """
    Extracts learnings from tick outcomes via LLM.

    Parses the LLM's JSON response into AgentLearning records
    and stores them in the database.
    """

    def __init__(
        self,
        db: AgenticDB,
        llm_pipeline=None,
        system_prompt: str = "",
        learning_categories: str = "",
        audit_action: str = "agent_reflection",
    ):
        self._db = db
        self._llm_pipeline = llm_pipeline
        self._system_prompt = system_prompt
        self._learning_categories = learning_categories
        self._audit_action = audit_action

    async def reflect(
        self,
        tick_number: int,
        planning_reasoning: str,
        outcomes: list[ActionOutcome],
    ) -> None:
        """
        Extract learnings from the tick's outcomes via LLM.

        Skips reflection if no LLM is available or no actions were taken.
        """
        if not outcomes or not self._llm_pipeline:
            return

        # Format outcomes for reflection
        outcomes_text = "\n".join(
            f"- {o.action.action_type} on {o.action.target}: "
            f"{'SUCCESS' if o.success else 'FAILED'} — "
            f"{o.result if o.success else o.error}"
            for o in outcomes
        )

        tick_summary = (
            f"Tick #{tick_number}\n"
            f"Planning reasoning: {planning_reasoning}\n"
            f"Actions executed: {len(outcomes)}\n"
            f"Succeeded: {sum(1 for o in outcomes if o.success)}"
        )

        messages = reflection_prompt(
            system_prompt=self._system_prompt,
            tick_summary=tick_summary,
            action_outcomes=outcomes_text,
            learning_categories=self._learning_categories,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action=self._audit_action,
                skip_preflight=True,
                complexity="low",
                priority="low",
            )

            if result and result.content and not result.blocked:
                await self._store_learnings(tick_number, result.content.strip())

        except Exception as e:
            logger.debug("Reflection failed (non-critical): %s", e)

    async def _store_learnings(self, tick_number: int, raw: str) -> None:
        """Parse and store learnings from reflection LLM response."""
        data = self._extract_json(raw)
        if not data:
            return

        learnings = data.get("learnings", [])
        for learning_data in learnings:
            if not isinstance(learning_data, dict):
                continue
            learning = AgentLearning(
                category=learning_data.get("category", "general"),
                insight=learning_data.get("insight", ""),
                confidence=float(learning_data.get("confidence", 0.5)),
                source="reflection",
                source_tick=tick_number,
            )
            if learning.insight:
                await self._db.add_learning(learning)

    @staticmethod
    def _extract_json(raw: str) -> dict | None:
        """Extract JSON object from LLM response."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

        return None
