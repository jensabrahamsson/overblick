"""
Generic prompt templates for the agentic core platform.

Provides configurable planning and reflection prompt builders.
Plugins inject domain-specific content via PlanningPromptConfig.
"""


from overblick.core.agentic.protocols import PlanningPromptConfig


def planning_prompt(
    system_prompt: str,
    config: PlanningPromptConfig,
    observations: str,
    goals: str,
    recent_actions: str = "",
    learnings: str = "",
    extra_context: str = "",
    max_actions: int = 5,
) -> list[dict[str, str]]:
    """
    Build the planning prompt for the agentic loop.

    The LLM receives the complete world state, active goals, and recent
    history, then produces a prioritized action plan as JSON.

    Args:
        system_prompt: Identity system prompt
        config: Domain-specific prompt configuration
        observations: Formatted observation text
        goals: Formatted goals text
        recent_actions: Recent action history
        learnings: Agent learnings
        extra_context: Plugin-specific extra context (owner commands, etc.)
        max_actions: Maximum number of actions to plan
    """
    system = (
        f"{system_prompt}\n\n"
        f"=== AGENT ROLE ===\n{config.agent_role}\n\n"
    )

    if config.available_actions:
        system += f"Available action types:\n{config.available_actions}\n\n"

    if config.safety_rules:
        system += f"SAFETY RULES:\n{config.safety_rules}\n\n"

    system += (
        f"Plan at most {max_actions} actions, ordered by priority (highest first).\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "reasoning": "Your overall analysis of the current state",\n'
        '  "actions": [\n'
        "    {\n"
        '      "action_type": "action_name",\n'
        '      "target": "description of target",\n'
        '      "target_number": 0,\n'
        '      "repo": "owner/repo",\n'
        '      "priority": 90,\n'
        '      "reasoning": "Why this action is needed"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    if config.output_format_hint:
        system += f"\n\n{config.output_format_hint}"

    # Build user message
    parts = []
    if extra_context:
        parts.append(f"=== PRIORITY CONTEXT ===\n{extra_context}")

    parts.append(f"=== CURRENT STATE ===\n{observations}")
    parts.append(f"=== ACTIVE GOALS ===\n{goals}")

    if recent_actions:
        parts.append(f"=== RECENT ACTIONS (for context) ===\n{recent_actions}")
    if learnings:
        parts.append(f"=== LEARNINGS ===\n{learnings}")

    parts.append("Plan your actions now.")

    user = "\n\n".join(parts)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def reflection_prompt(
    system_prompt: str,
    tick_summary: str,
    action_outcomes: str,
    learning_categories: str = "",
) -> list[dict[str, str]]:
    """
    Build the reflection prompt for learning extraction.

    The LLM extracts learnings from the tick's outcomes.

    Args:
        system_prompt: Identity system prompt
        tick_summary: Summary of the tick cycle
        action_outcomes: Formatted action outcomes
        learning_categories: Domain-specific learning categories
    """
    categories = learning_categories or "general"

    system = (
        f"{system_prompt}\n\n"
        "Reflect on this tick cycle. Extract any useful learnings.\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "learnings": [\n'
        "    {\n"
        f'      "category": "{categories}",\n'
        '      "insight": "What you learned",\n'
        '      "confidence": 0.0-1.0\n'
        "    }\n"
        "  ],\n"
        '  "tick_summary": "Brief summary of what happened"\n'
        "}"
    )

    user = (
        f"=== TICK SUMMARY ===\n{tick_summary}\n\n"
        f"=== ACTION OUTCOMES ===\n{action_outcomes}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
