# Agentic Loop

## Overview

The **agentic** module implements a reusable OBSERVE/THINK/PLAN/ACT/REFLECT reasoning loop for autonomous agent plugins. It provides the foundation for plugins that need to independently assess situations, plan multi-step actions, execute them, and learn from the results.

## Architecture

```
AgenticPluginBase
    └── AgenticLoop
            ├── ActionPlanner    (LLM-driven planning)
            ├── ActionExecutor   (Domain-agnostic dispatcher)
            ├── ReflectionPipeline (Post-action learning)
            └── GoalTracker      (Persistent goal state)
```

## Components

### AgenticPluginBase (`plugin_base.py`)

Extended `PluginBase` with agentic lifecycle. Plugins subclass this to get the full reasoning loop integrated into the standard setup/tick/teardown lifecycle.

### AgenticLoop (`loop.py`)

Orchestrates the 5-phase reasoning cycle:
1. **OBSERVE** — Gather context from the environment
2. **THINK** — Analyze observations via LLM
3. **PLAN** — Generate actionable steps
4. **ACT** — Execute planned actions
5. **REFLECT** — Learn from outcomes

### ActionPlanner (`planner.py`)

Uses SafeLLMPipeline to generate structured action plans from observations. Returns prioritized action steps with parameters.

### ActionExecutor (`executor.py`)

Domain-agnostic action dispatcher. Maps action types to handler functions registered by the plugin. Does not perform file I/O or network calls itself — delegates to registered handlers.

### ReflectionPipeline (`reflection.py`)

Post-action LLM analysis that extracts learnings and stores them via the LearningStore. Failures are non-critical and logged at WARNING level.

### GoalTracker (`goal_tracker.py`)

Persistent goal state management backed by SQLite. Tracks active goals, completed steps, and progress across agent restarts.

## Usage

```python
from overblick.core.agentic import AgenticPluginBase

class MyPlugin(AgenticPluginBase):
    async def observe(self) -> dict:
        return {"data": await self.gather_context()}

    def register_actions(self):
        self.executor.register("my_action", self.handle_my_action)
```

## Plugins Using This Module

- **github** — Autonomous GitHub issue/PR management
- **dev_agent** — Autonomous development task execution
- **log_agent** — Autonomous log analysis and anomaly detection
