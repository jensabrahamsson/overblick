"""
Tests for AgenticPluginBase — integration tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from overblick.core.agentic.models import (
    ActionOutcome,
    AgentGoal,
)
from overblick.core.agentic.plugin_base import AgenticPluginBase
from overblick.core.agentic.protocols import ActionHandler, Observer, PlanningPromptConfig
from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.plugin_base import PluginContext


class SampleObserver:
    """Sample observer for testing."""

    async def observe(self):
        return {"items": ["a", "b"]}

    def format_for_planner(self, observation):
        return f"Observed {len(observation.get('items', []))} items"


class SampleHandler:
    """Sample action handler for testing."""

    async def handle(self, action, observation):
        return ActionOutcome(action=action, success=True, result="Sample done")


class SampleAgenticPlugin(AgenticPluginBase):
    """Concrete test plugin that implements the abstract methods."""

    name = "sample_agent"

    async def create_observer(self) -> Observer:
        return SampleObserver()

    def get_action_handlers(self) -> dict[str, ActionHandler]:
        return {
            "sample_action": SampleHandler(),
            "skip": SampleHandler(),
        }

    def get_planning_prompt_config(self) -> PlanningPromptConfig:
        return PlanningPromptConfig(
            agent_role="You are a sample test agent.",
            available_actions="- sample_action: Do a sample thing\n- skip: Do nothing",
            safety_rules="No dangerous operations.",
        )

    def get_default_goals(self) -> list[AgentGoal]:
        return [
            AgentGoal(name="sample_goal", description="A sample goal", priority=80),
        ]

    def get_system_prompt(self) -> str:
        return "You are a sample agent for testing."

    async def setup(self) -> None:
        """Setup with real SQLite backend."""
        db_path = self.ctx.data_dir / "sample.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        await self.setup_agentic_db(backend)
        await self.setup_agentic_loop(max_actions_per_tick=3)

    async def tick(self) -> None:
        """Run agentic tick."""
        await self.agentic_tick()


class TestAgenticPluginBase:
    """Test AgenticPluginBase lifecycle."""

    @pytest.fixture
    def plugin_ctx(self, tmp_path):
        """Create a PluginContext for testing."""
        ctx = PluginContext(
            identity_name="test_agent",
            data_dir=tmp_path / "data" / "test_agent",
            log_dir=tmp_path / "logs" / "test_agent",
            llm_pipeline=None,  # No LLM for unit tests
        )
        return ctx

    @pytest.mark.asyncio
    async def test_setup_creates_db_and_loop(self, plugin_ctx):
        """Plugin setup creates agentic DB and loop."""
        plugin = SampleAgenticPlugin(plugin_ctx)
        await plugin.setup()

        assert plugin.agentic_db is not None
        assert plugin.goal_tracker is not None
        assert len(plugin.goal_tracker.active_goals) == 1
        assert plugin.goal_tracker.active_goals[0].name == "sample_goal"

    @pytest.mark.asyncio
    async def test_tick_runs_without_llm(self, plugin_ctx):
        """Tick runs without errors even without LLM (no actions planned)."""
        plugin = SampleAgenticPlugin(plugin_ctx)
        await plugin.setup()

        # Should not raise — planner returns empty plan without LLM
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_valid_action_types_from_handlers(self, plugin_ctx):
        """get_valid_action_types returns keys from handlers."""
        plugin = SampleAgenticPlugin(plugin_ctx)
        valid = plugin.get_valid_action_types()
        assert valid == {"sample_action", "skip"}

    @pytest.mark.asyncio
    async def test_setup_agentic_db_without_loop_warns(self, plugin_ctx):
        """agentic_tick without loop setup logs warning."""
        plugin = SampleAgenticPlugin(plugin_ctx)

        # Only setup DB, not loop
        db_path = plugin_ctx.data_dir / "sample.db"
        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        await plugin.setup_agentic_db(backend)

        result = await plugin.agentic_tick()
        assert result is None

    @pytest.mark.asyncio
    async def test_setup_loop_without_db_raises(self, plugin_ctx):
        """setup_agentic_loop without prior setup_agentic_db raises."""
        plugin = SampleAgenticPlugin(plugin_ctx)

        with pytest.raises(RuntimeError, match="setup_agentic_db"):
            await plugin.setup_agentic_loop()

    @pytest.mark.asyncio
    async def test_inherits_plugin_base(self, plugin_ctx):
        """AgenticPluginBase is a proper subclass of PluginBase."""
        from overblick.core.plugin_base import PluginBase

        plugin = SampleAgenticPlugin(plugin_ctx)
        assert isinstance(plugin, PluginBase)
        assert plugin.name == "sample_agent"

    @pytest.mark.asyncio
    async def test_default_complexity_is_high(self, plugin_ctx):
        """Default complexity should be 'high', not 'ultra'."""
        import inspect

        sig = inspect.signature(AgenticPluginBase.setup_agentic_loop)
        assert sig.parameters["complexity"].default == "high"

    def test_get_default_goals_returns_empty_list(self, plugin_ctx):
        """Base class get_default_goals returns empty list."""

        class MinimalPlugin(AgenticPluginBase):
            name = "minimal"

            async def create_observer(self) -> Observer:
                return SampleObserver()

            def get_action_handlers(self) -> dict[str, ActionHandler]:
                return {}

            def get_planning_prompt_config(self) -> PlanningPromptConfig:
                return PlanningPromptConfig()

            async def setup(self) -> None:
                pass

            async def tick(self) -> None:
                pass

        plugin = MinimalPlugin(plugin_ctx)
        assert plugin.get_default_goals() == []

    def test_get_system_prompt_fallback_on_file_not_found(self, plugin_ctx):
        """get_system_prompt falls back when identity file is not found."""

        class FallbackPlugin(AgenticPluginBase):
            name = "fallback_agent"

            async def create_observer(self) -> Observer:
                return SampleObserver()

            def get_action_handlers(self) -> dict[str, ActionHandler]:
                return {}

            def get_planning_prompt_config(self) -> PlanningPromptConfig:
                return PlanningPromptConfig()

            async def setup(self) -> None:
                pass

            async def tick(self) -> None:
                pass

        plugin = FallbackPlugin(plugin_ctx)
        # Patch the underlying load_identity function to raise FileNotFoundError
        with patch(
            "overblick.identities.load_identity",
            side_effect=FileNotFoundError("no identity"),
        ):
            prompt = plugin.get_system_prompt()
        assert "autonomous agent" in prompt
        assert "fallback_agent" in prompt

    def test_get_system_prompt_success_path(self, plugin_ctx):
        """get_system_prompt calls load_identity and build_system_prompt."""

        class PromptPlugin(AgenticPluginBase):
            name = "prompt_agent"

            async def create_observer(self) -> Observer:
                return SampleObserver()

            def get_action_handlers(self) -> dict[str, ActionHandler]:
                return {}

            def get_planning_prompt_config(self) -> PlanningPromptConfig:
                return PlanningPromptConfig()

            async def setup(self) -> None:
                pass

            async def tick(self) -> None:
                pass

        plugin = PromptPlugin(plugin_ctx)
        mock_identity = MagicMock()
        with patch(
            "overblick.identities.load_identity",
            return_value=mock_identity,
        ), patch.object(
            type(plugin.ctx),
            "build_system_prompt",
            return_value="Built prompt",
        ):
            prompt = plugin.get_system_prompt()
        assert prompt == "Built prompt"
