"""
StagePlugin — Behavioral Scenario Engine.

YAML-driven behavioral test scenarios for identities. Define inputs,
expected behavioral constraints (not exact outputs), and run scenarios
to report compliance. Makes identity testing systematic and CI-ready.

Architecture: On-demand (CLI command + dashboard UI). Parse YAML scenario
-> for each step: inject input -> run through SafeLLMPipeline -> evaluate
output against constraints -> aggregate results -> generate report.

Security: All LLM calls go through SafeLLMPipeline.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from overblick.core.plugin_base import PluginBase, PluginContext

from .evaluator import evaluate_constraint
from .models import (
    Constraint,
    ConstraintResult,
    Scenario,
    ScenarioResult,
    ScenarioStep,
    StepResult,
)

logger = logging.getLogger(__name__)

_MAX_RESULTS_STORED = 200


class StagePlugin(PluginBase):
    """
    Behavioral scenario engine plugin.

    Lifecycle:
        setup()    — Load config, discover scenario files
        tick()     — No-op (on-demand only)
        teardown() — Persist results

    Primary interface: run_scenario() and run_all_scenarios()
    """

    name = "stage"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._scenario_dirs: list[Path] = []
        self._results: list[ScenarioResult] = []
        self._state_file: Optional[Any] = None
        self._tick_count: int = 0

    async def setup(self) -> None:
        """Initialize plugin — load config, discover scenarios."""
        identity = self.ctx.identity
        logger.info("Setting up StagePlugin for identity: %s", identity.name)

        raw_config = identity.raw_config
        stage_config = raw_config.get("stage", {})

        # Discover scenario directories
        configured_dirs = stage_config.get("scenario_dirs", [])
        if configured_dirs:
            self._scenario_dirs = [Path(d) for d in configured_dirs]
        else:
            # Default: look for scenarios/ in project root
            project_root = Path(__file__).parent.parent.parent.parent
            default_dir = project_root / "scenarios"
            if default_dir.exists():
                self._scenario_dirs = [default_dir]

        # State persistence
        self._state_file = self.ctx.data_dir / "stage_state.json"
        self._load_state()

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "scenario_dirs": [str(d) for d in self._scenario_dirs],
            },
        )
        logger.info(
            "StagePlugin setup complete (%d scenario dirs)",
            len(self._scenario_dirs),
        )

    async def tick(self) -> None:
        """No-op — Stage runs on demand, not on a schedule."""
        self._tick_count += 1

    async def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """
        Run a single behavioral scenario.

        Args:
            scenario: The scenario to execute.

        Returns:
            ScenarioResult with pass/fail for each step and constraint.
        """
        start_time = time.time()
        pipeline = self.ctx.llm_pipeline

        if not pipeline:
            return ScenarioResult(
                scenario_name=scenario.name,
                identity=scenario.identity,
                passed=False,
                error="No LLM pipeline available",
            )

        # Load identity and build system prompt
        try:
            identity = self.ctx.load_identity(scenario.identity)
            system_prompt = self.ctx.build_system_prompt(
                identity, platform="Stage Test"
            )
        except FileNotFoundError:
            return ScenarioResult(
                scenario_name=scenario.name,
                identity=scenario.identity,
                passed=False,
                error=f"Identity '{scenario.identity}' not found",
            )

        step_results: list[StepResult] = []
        total_constraints = 0
        passed_constraints = 0
        all_passed = True

        # Build conversation messages (multi-turn)
        messages = [{"role": "system", "content": system_prompt}]

        for i, step in enumerate(scenario.steps):
            messages.append({"role": "user", "content": step.input})

            try:
                result = await pipeline.chat(
                    messages=messages,
                    temperature=identity.llm.temperature,
                    max_tokens=identity.llm.max_tokens,
                    audit_action="stage_scenario_step",
                    audit_details={
                        "scenario": scenario.name,
                        "identity": scenario.identity,
                        "step": i,
                    },
                )

                output = result.content or ""
                messages.append({"role": "assistant", "content": output})

                # Evaluate constraints
                constraint_results: list[ConstraintResult] = []
                step_passed = True

                for constraint in step.constraints:
                    cr = evaluate_constraint(constraint, output)
                    constraint_results.append(cr)
                    total_constraints += 1
                    if cr.passed:
                        passed_constraints += 1
                    else:
                        step_passed = False

                if not step_passed:
                    all_passed = False

                step_results.append(
                    StepResult(
                        step_index=i,
                        input_text=step.input,
                        output_text=output,
                        constraint_results=constraint_results,
                        passed=step_passed,
                    )
                )

            except Exception as e:
                step_results.append(
                    StepResult(
                        step_index=i,
                        input_text=step.input,
                        passed=False,
                        error=str(e),
                    )
                )
                all_passed = False

        duration_ms = (time.time() - start_time) * 1000

        scenario_result = ScenarioResult(
            scenario_name=scenario.name,
            identity=scenario.identity,
            step_results=step_results,
            passed=all_passed,
            total_constraints=total_constraints,
            passed_constraints=passed_constraints,
            failed_constraints=total_constraints - passed_constraints,
            duration_ms=round(duration_ms, 1),
        )

        self._results.append(scenario_result)

        # Trim results
        if len(self._results) > _MAX_RESULTS_STORED:
            self._results = self._results[-_MAX_RESULTS_STORED:]

        self._save_state()

        self.ctx.audit_log.log(
            action="stage_scenario_complete",
            details={
                "scenario": scenario.name,
                "identity": scenario.identity,
                "passed": all_passed,
                "total_constraints": total_constraints,
                "passed_constraints": passed_constraints,
                "duration_ms": duration_ms,
            },
        )

        return scenario_result

    async def run_all_scenarios(
        self, directory: Optional[Path] = None
    ) -> list[ScenarioResult]:
        """
        Run all scenarios found in a directory.

        Args:
            directory: Directory containing YAML scenario files.
                If None, uses configured directories.

        Returns:
            List of ScenarioResults.
        """
        dirs = [directory] if directory else self._scenario_dirs
        scenarios = self._discover_scenarios(dirs)

        results: list[ScenarioResult] = []
        for scenario in scenarios:
            result = await self.run_scenario(scenario)
            results.append(result)

        if self.ctx.event_bus:
            await self.ctx.event_bus.emit(
                "stage.run_complete",
                {
                    "scenarios": len(results),
                    "passed": sum(1 for r in results if r.passed),
                    "failed": sum(1 for r in results if not r.passed),
                },
            )

        return results

    def load_scenario_from_yaml(self, path: Path) -> Scenario:
        """Load a scenario from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        steps = []
        for step_data in data.get("steps", []):
            constraints = []
            for c_data in step_data.get("constraints", []):
                constraints.append(Constraint.model_validate(c_data))
            steps.append(
                ScenarioStep(
                    input=step_data["input"],
                    constraints=constraints,
                    description=step_data.get("description", ""),
                )
            )

        return Scenario(
            name=data.get("name", path.stem),
            identity=data.get("identity", ""),
            description=data.get("description", ""),
            steps=steps,
            tags=data.get("tags", []),
        )

    def _discover_scenarios(self, dirs: list[Path]) -> list[Scenario]:
        """Discover and load all scenario YAML files from directories."""
        scenarios: list[Scenario] = []
        for directory in dirs:
            if not directory or not directory.exists():
                continue
            for yaml_file in sorted(directory.glob("*.yaml")):
                try:
                    scenario = self.load_scenario_from_yaml(yaml_file)
                    if scenario.identity:
                        scenarios.append(scenario)
                except Exception as e:
                    logger.error(
                        "StagePlugin: failed to load %s: %s", yaml_file, e
                    )
        return scenarios

    def get_results(self, limit: int = 50) -> list[ScenarioResult]:
        """Get recent scenario results (newest first)."""
        return list(reversed(self._results[-limit:]))

    def get_results_for_identity(self, name: str) -> list[ScenarioResult]:
        """Get results for a specific identity."""
        return [r for r in self._results if r.identity == name]

    def _load_state(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                for result_data in data.get("results", []):
                    self._results.append(
                        ScenarioResult.model_validate(result_data)
                    )
            except Exception as e:
                logger.warning("StagePlugin: failed to load state: %s", e)

    def _save_state(self) -> None:
        if self._state_file:
            try:
                data = {
                    "results": [
                        r.model_dump()
                        for r in self._results[-_MAX_RESULTS_STORED:]
                    ],
                }
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning("StagePlugin: failed to save state: %s", e)

    async def teardown(self) -> None:
        self._save_state()
        logger.info("StagePlugin teardown complete")
