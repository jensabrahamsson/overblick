# Software Engineering Capability

> **Status: STUB** — Not yet implemented. Placeholder for future code generation features.

## Overview

The Software Engineering capability will enable agents to generate code patches, create pull requests, propose fixes for failing CI, and suggest refactoring improvements.

**Plugin vs Capability vs Identity**: A *capability* is a reusable skill shared across plugins. A *plugin* connects an identity to a platform or service. An *identity* is a character with voice, traits, and backstory. The Software Engineering capability is a **composable block** that any plugin can request via `ctx.get_capability("software_engineering")`.

## Current State

The capability reports itself as `configured = False` and performs no operations. It exists as a placeholder to define the interface that future implementations will fulfill.

```python
cap = ctx.get_capability("software_engineering")
assert cap.configured is False  # Not yet implemented
```

## Planned Features

- **Code Patch Generation**: LLM-powered code fixes using specialized prompts
- **Branch & PR Creation**: Automated git branch creation and pull request submission
- **CI Fix Proposals**: Analyze failing CI logs and suggest fixes
- **Refactoring Suggestions**: Identify and propose code quality improvements

## Architecture

When implemented, the capability will:

1. Use the `SafeLLMPipeline` with code-specialized prompts
2. Interact with the GitHub Agent plugin for PR creation
3. Use the Dev Agent plugin's workspace management for safe code execution
4. Report results through the audit log

## Files

| File | Purpose |
|------|---------|
| `software_engineering.py` | Stub capability class |
| `__init__.py` | Package init |

## Testing

```bash
pytest tests/capabilities/ -v -k engineering
```
