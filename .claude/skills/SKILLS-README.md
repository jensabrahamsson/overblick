# Överblick Claude Code Skills

Four skills for developing with the Överblick agent framework. Three helper skills provide interactive guidance, and the **skill compiler** automates full component generation from specifications.

## Skills Overview

| Skill | Triggers | Purpose |
|-------|----------|---------|
| **overblick-skill-compiler** | `compile skill`, `build from spec`, `generate plugin from description` | Compile specs into full production-grade plugins, capabilities, and personalities |
| **overblick-plugin-helper** | `create plugin`, `review plugin`, `debug plugin` | Create, review, and debug Överblick connector plugins |
| **overblick-capability-helper** | `create capability`, `review capability`, `add bundle` | Create and compose reusable behavioral capabilities |
| **overblick-personality-helper** | `create personality`, `design character`, `review personality` | Design YAML-driven character personalities |

## How to Use

Mention any trigger phrase in your prompt and Claude Code will activate the skill automatically:

```
"Compile this SKILL.md into a working plugin"
"Build from spec: a Slack integration that monitors channels"
"Generate plugin from description: RSS feed aggregator with summarization"
"Create a new plugin for Discord"
"Review the Telegram plugin for security issues"
"Design a personality for a music-loving bot"
"Add a new capability for sentiment analysis"
```

## Skill Details

### overblick-skill-compiler

The **meta-skill** — a code compiler that produces complete, production-grade Överblick components from specifications. Not scaffolding — working code with full tests and registry wiring.

**What it does:**
- Accepts a SKILL.md spec file OR a free-form description
- Applies the component decision tree (plugin vs capability vs personality)
- Generates full implementation code using framework templates
- Writes comprehensive tests (setup, tick, teardown, security)
- Wires registries (plugin_registry.py + capabilities/__init__.py)
- Runs verification and fixes failures

**What it knows:**
- Component decision tree (when to create what)
- Full plugin, capability, and personality templates with security patterns
- Test templates for both plugins and capabilities
- Exact registry wiring patterns for both registries
- SKILL.md spec format with YAML frontmatter

**References:**
- `references/component-decision-tree.md` — When to create plugin/capability/personality
- `references/plugin-template.md` — Full plugin code template with security
- `references/capability-template.md` — Full capability code template
- `references/personality-template.md` — Full personality YAML template
- `references/test-templates.md` — Test + conftest templates for all types
- `references/registry-wiring.md` — Exact registry update patterns
- `references/skill-spec-format.md` — SKILL.md input spec schema

### overblick-plugin-helper

Guides creation and review of **connector plugins** — self-contained modules that integrate with external services (APIs, messaging platforms, etc.).

**What it knows:**
- PluginBase lifecycle (setup, tick, teardown)
- PluginContext API (all 16 fields)
- PluginRegistry whitelist registration
- SafeLLMPipeline usage patterns
- Security checklist (boundary markers, secrets, permissions, audit)
- Real examples from Telegram, Gmail, and Moltbook plugins

**References:**
- `references/plugin-architecture.md` — Full API documentation
- `references/plugin-checklist.md` — Security and quality checklist
- `references/plugin-examples.md` — Condensed real-world patterns

### overblick-capability-helper

Guides creation of **capabilities** — composable behavioral building blocks that plugins wire together (lego-block architecture).

**What it knows:**
- CapabilityBase API (setup, tick, on_event, teardown, get_prompt_context)
- CapabilityContext fields and from_plugin_context()
- CapabilityRegistry (register, resolve, create, bundles)
- Bundle system for grouping related capabilities
- Dependency ordering patterns

**References:**
- `references/capability-architecture.md` — Full API documentation
- `references/capability-examples.md` — Real capability patterns

### overblick-personality-helper

Guides creation of **personalities** — YAML-driven character definitions that shape how agents communicate.

**What it knows:**
- Full personality YAML schema (20+ sections)
- Personality class API and loading system
- build_system_prompt() generation
- Voice design, trait scales, vocabulary rules
- Real examples from the personality stable (Volt, Birch, etc.)

**References:**
- `references/personality-architecture.md` — Personality class and loading system
- `references/personality-yaml-schema.md` — Complete YAML schema with examples

## Architecture

```
.claude/skills/
├── SKILLS-README.md                        (this file)
├── overblick-skill-compiler/
│   ├── SKILL.md
│   └── references/
│       ├── component-decision-tree.md
│       ├── plugin-template.md
│       ├── capability-template.md
│       ├── personality-template.md
│       ├── test-templates.md
│       ├── registry-wiring.md
│       └── skill-spec-format.md
├── overblick-plugin-helper/
│   ├── SKILL.md
│   └── references/
│       ├── plugin-architecture.md
│       ├── plugin-checklist.md
│       └── plugin-examples.md
├── overblick-capability-helper/
│   ├── SKILL.md
│   └── references/
│       ├── capability-architecture.md
│       └── capability-examples.md
└── overblick-personality-helper/
    ├── SKILL.md
    └── references/
        ├── personality-architecture.md
        └── personality-yaml-schema.md
```

Skills are checked into the repo so anyone cloning gets them automatically.
