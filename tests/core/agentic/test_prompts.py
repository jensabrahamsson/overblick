"""
Tests for planning and reflection prompt builders.
"""

from overblick.core.agentic.prompts import planning_prompt, reflection_prompt
from overblick.core.agentic.protocols import PlanningPromptConfig


class TestPlanningPrompt:
    """Test planning_prompt builder."""

    def test_should_include_available_actions_when_provided(self):
        """Available actions appear in system message when config has them."""
        config = PlanningPromptConfig(
            agent_role="Test agent",
            available_actions="- merge_pr: Merge a PR\n- skip: Do nothing",
        )
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
        )
        system = messages[0]["content"]
        assert "Available action types:" in system
        assert "merge_pr" in system

    def test_should_include_safety_rules_when_provided(self):
        """Safety rules appear in system message when config has them."""
        config = PlanningPromptConfig(
            agent_role="Test agent",
            safety_rules="Never delete anything.",
        )
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
        )
        system = messages[0]["content"]
        assert "SAFETY RULES:" in system
        assert "Never delete anything." in system

    def test_should_include_output_format_hint_when_provided(self):
        """Output format hint is appended to system message when present."""
        config = PlanningPromptConfig(
            agent_role="Test agent",
            output_format_hint="Extra formatting instructions here.",
        )
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
        )
        system = messages[0]["content"]
        assert "Extra formatting instructions here." in system

    def test_should_include_extra_context_when_provided(self):
        """Extra context appears in user message as PRIORITY CONTEXT."""
        config = PlanningPromptConfig(agent_role="Test agent")
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
            extra_context="Owner command: merge PR #42",
        )
        user = messages[1]["content"]
        assert "PRIORITY CONTEXT" in user
        assert "Owner command: merge PR #42" in user

    def test_should_include_recent_actions_when_provided(self):
        """Recent actions appear in user message."""
        config = PlanningPromptConfig(agent_role="Test agent")
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
            recent_actions="- [OK] merge_pr on PR #1",
        )
        user = messages[1]["content"]
        assert "RECENT ACTIONS" in user
        assert "merge_pr" in user

    def test_should_include_learnings_when_provided(self):
        """Learnings appear in user message."""
        config = PlanningPromptConfig(agent_role="Test agent")
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
            learnings="- [testing] Tests are important",
        )
        user = messages[1]["content"]
        assert "LEARNINGS" in user
        assert "Tests are important" in user

    def test_should_omit_optional_sections_when_empty(self):
        """Optional sections are omitted when not provided."""
        config = PlanningPromptConfig(agent_role="Test agent")
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
        )
        system = messages[0]["content"]
        user = messages[1]["content"]
        assert "Available action types:" not in system
        assert "SAFETY RULES:" not in system
        assert "PRIORITY CONTEXT" not in user
        assert "RECENT ACTIONS" not in user
        assert "LEARNINGS" not in user

    def test_should_include_all_sections_together(self):
        """All optional sections included when all are provided."""
        config = PlanningPromptConfig(
            agent_role="Full agent",
            available_actions="- act: Do thing",
            safety_rules="Be safe",
            output_format_hint="Format hint",
        )
        messages = planning_prompt(
            system_prompt="sys",
            config=config,
            observations="obs",
            goals="goals",
            recent_actions="recent",
            learnings="learnings",
            extra_context="extra",
            max_actions=3,
        )
        system = messages[0]["content"]
        user = messages[1]["content"]
        assert "at most 3 actions" in system
        assert "Format hint" in system
        assert "PRIORITY CONTEXT" in user
        assert "RECENT ACTIONS" in user
        assert "LEARNINGS" in user


class TestReflectionPrompt:
    """Test reflection_prompt builder."""

    def test_should_build_reflection_messages(self):
        """Reflection prompt builds system and user messages."""
        messages = reflection_prompt(
            system_prompt="sys",
            tick_summary="Tick #1",
            action_outcomes="- test: OK",
            learning_categories="testing|general",
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "testing|general" in messages[0]["content"]
        assert "Tick #1" in messages[1]["content"]

    def test_should_use_general_when_no_categories(self):
        """Reflection prompt defaults to 'general' when no categories provided."""
        messages = reflection_prompt(
            system_prompt="sys",
            tick_summary="summary",
            action_outcomes="outcomes",
        )
        assert '"general"' in messages[0]["content"]
