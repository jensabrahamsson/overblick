---
name: overblick-skill-compiler
description: Compile a specification into working Överblick plugins, capabilities, and personalities with full code, tests, and registry wiring
triggers:
  - compile skill
  - build skill
  - build from spec
  - skill compiler
  - generate plugin from description
  - scaffold from spec
  - generate components
  - create from spec
---

# Överblick Skill Compiler

You are a **code compiler** for the Överblick agent framework. Given a specification (SKILL.md file or free-form description), you produce **complete, production-grade** Överblick components — plugins, capabilities, and/or personalities — with full implementation, tests, and registry wiring.

**You do NOT produce scaffolding.** You produce working code. The only TODOs allowed are for external-API-specific logic that cannot be known without credentials or documentation (e.g., the exact HTTP endpoint for a third-party service).

## Input Modes

### Mode 1: SKILL.md Spec File

If the user provides a SKILL.md file (or path to one), parse it according to the schema in `references/skill-spec-format.md`.

Extract from the YAML frontmatter:
- `name` — component name (lowercase, no spaces)
- `type` — plugin, capability, personality, or composite (auto-detected if omitted)
- `bundle` — capability bundle (for capabilities only)
- `needs_llm` — whether the component uses LLM
- `needs_secrets` — list of required secret keys
- `needs_events` — events this component listens to
- `emits_events` — events this component produces
- `needs_tick` — whether tick() does work
- `external_api` — external API URL/description
- `capabilities_used` — capabilities this plugin consumes

Extract from the markdown body:
- **Purpose** — what does the component do
- **Behavior** — detailed behavioral specification
- **Configuration** — identity.yaml config keys
- **External API** — API details, endpoints, auth
- **Events** — event contracts
- **Security** — special security considerations
- **Examples** — example inputs/outputs

### Mode 2: Free Description

If the user provides a free-text description instead of a spec file:

1. Apply the decision tree from `references/component-decision-tree.md` to determine what components are needed
2. Ask **at most 3 targeted questions** to fill gaps. Good questions:
   - "Does this need to call an external API?" (determines plugin vs capability)
   - "Should this be reusable across plugins?" (determines capability)
   - "What secrets/credentials does this need?"
3. Do NOT ask about things you can infer from the description

## Component Decision

Apply the decision tree from `references/component-decision-tree.md`:

| Signal | Component |
|--------|-----------|
| External service/API interaction | **Plugin** |
| Reusable behavior across plugins | **Capability** |
| New persona/character | **Personality** |
| External API + reusable analysis | **Plugin + Capability** |
| New character with custom behavior | **Personality + Plugin** or **Personality + Capability** |

Many features need multiple components. A Discord bot needs a Plugin (API) + possibly a Capability (shared logic) + possibly a Personality (character). Generate ALL needed components.

## Generation Workflow

Follow these 6 steps in order. Do not skip steps.

### Step 1: Analyze

Determine:
- Which components to generate (plugin, capability, personality, or combination)
- Dependencies between components (capabilities before plugins that use them)
- Which existing capabilities/plugins to reference
- Required secrets, events, and configuration keys

### Step 2: Generate Code

Generate code using the templates in `references/`. Follow dependency order:

1. **Personality YAML** (if needed) — `references/personality-template.md`
2. **Capability source** + bundle `__init__.py` (if needed) — `references/capability-template.md`
3. **Capability registry update** (`overblick/capabilities/__init__.py`) — `references/registry-wiring.md`
4. **Plugin source** + `__init__.py` (if needed) — `references/plugin-template.md`
5. **Plugin registry update** (`overblick/core/plugin_registry.py`) — `references/registry-wiring.md`

Every generated file must:
- Have a module docstring explaining purpose and security properties
- Use English-only (zero Swedish in code, comments, logs, variables)
- Have type hints on all public interfaces
- Use Pydantic v2 BaseModel for config classes
- Use async/await for all lifecycle methods

### Step 3: Generate Tests

Generate comprehensive tests using `references/test-templates.md`:

6. **Capability tests** — `tests/capabilities/test_<name>.py`
7. **Plugin conftest.py** — `tests/plugins/<name>/conftest.py`
8. **Plugin tests** — `tests/plugins/<name>/test_<name>.py`
9. **Plugin `__init__.py`** — `tests/plugins/<name>/__init__.py` (empty)

Every test file must:
- Use `pytest.mark.asyncio` for async tests
- Use `AsyncMock` for async interfaces, `MagicMock` for sync
- Test setup, tick, teardown, and key business logic
- Include a security test class verifying pipeline usage and boundary markers

### Step 4: Wire Registries

Update the registries using exact patterns from `references/registry-wiring.md`:

- **Plugin**: Add to `_DEFAULT_PLUGINS` dict in `overblick/core/plugin_registry.py` (alphabetical order)
- **Capability**: Add import, registry entry, bundle entry, and `__all__` entry in `overblick/capabilities/__init__.py`

### Step 5: Verify

Run tests:
```bash
# Component-specific tests
./venv/bin/python3 -m pytest tests/plugins/<name>/ tests/capabilities/test_<name>.py -v -x

# Full test suite (excluding LLM tests)
./venv/bin/python3 -m pytest tests/ -v -m "not llm" -x
```

### Step 6: Fix and Re-run

If any tests fail:
1. Read the error carefully
2. Fix the root cause (not symptoms)
3. Re-run the failing tests
4. Once passing, re-run the full suite

## Security Rules (Non-Negotiable)

These rules are baked into every template and must appear in every generated component:

1. **`wrap_external_content()`** on ALL data from external sources (API responses, user messages, webhook payloads). Import from `overblick.core.security.input_sanitizer`.

2. **`ctx.llm_pipeline`** for ALL LLM calls. NEVER use `ctx.llm_client` directly in new code. The pipeline enforces the full security chain (sanitize -> preflight -> rate limit -> LLM -> output safety -> audit).

3. **Handle `result.blocked`** from every pipeline.chat() call. Log the block reason and return gracefully. Never ignore a blocked result.

4. **`ctx.get_secret(key)`** for ALL secrets. Never hardcode credentials. Raise `RuntimeError` in setup() if a required secret is missing.

5. **`ctx.audit_log.log()`** for setup and every significant action (API calls, posts, decisions).

6. **Quiet hours check** before any LLM work in tick():
   ```python
   if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
       return
   ```

7. **Never log secret values.** Log key names only.

## Code Quality Standards

- **English only** — Zero Swedish in code, comments, logs, error messages, variable names
- **Type hints** on all public methods and class attributes
- **Async/await** for all lifecycle methods (setup, tick, teardown, on_event)
- **Pydantic v2 BaseModel** for any config/data classes (not dataclass)
- **pytest.mark.asyncio** on all async test methods
- **AsyncMock** for async interfaces (pipeline, client), **MagicMock** for sync (audit_log, engagement_db)
- **No over-engineering** — Generate what the spec requires, nothing more
- **Module docstrings** on every file explaining purpose and security
- **Logging** via `logger = logging.getLogger(__name__)` (never print)

## File Generation Order Summary

| Order | File | Condition |
|-------|------|-----------|
| 1 | `overblick/identities/<name>/personality.yaml` | If personality needed |
| 2 | `overblick/capabilities/<bundle>/<name>.py` | If capability needed |
| 3 | `overblick/capabilities/<bundle>/__init__.py` | If new bundle |
| 4 | `overblick/capabilities/__init__.py` | If capability needed (update) |
| 5 | `overblick/plugins/<name>/plugin.py` | If plugin needed |
| 6 | `overblick/plugins/<name>/__init__.py` | If plugin needed |
| 7 | `overblick/core/plugin_registry.py` | If plugin needed (update) |
| 8 | `tests/capabilities/test_<name>.py` | If capability needed |
| 9 | `tests/plugins/<name>/__init__.py` | If plugin needed |
| 10 | `tests/plugins/<name>/conftest.py` | If plugin needed |
| 11 | `tests/plugins/<name>/test_<name>.py` | If plugin needed |

## References

- `references/component-decision-tree.md` — When to create plugin/capability/personality
- `references/plugin-template.md` — Full plugin code template with security
- `references/capability-template.md` — Full capability code template
- `references/personality-template.md` — Full personality YAML template
- `references/test-templates.md` — Test + conftest templates for all types
- `references/registry-wiring.md` — Exact registry update patterns
- `references/skill-spec-format.md` — SKILL.md input spec schema
