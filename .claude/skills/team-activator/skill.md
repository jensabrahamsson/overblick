---
name: team
description: Activate the development agent team (Team Tage Erlander) for structured multi-agent development work
user_invocable: true
---

# Team Tage Erlander — Agent Team Activator

You have a full development team available as Claude Code agents in `.claude/agents/`. This skill helps you activate the right agents for your current task.

## Available Team Members

| Agent | File | Role |
|-------|------|------|
| Elisabeth Lindqvist | `elisabeth-lindqvist-sm` | Scrum Master — ceremonies, impediment removal |
| Alexander Lindgren | `alexander-lindgren-tech-lead` | Tech Lead — architecture, code quality |
| Sofia Andersson | `sofia-andersson-fullstack` | Fullstack Dev — implementation |
| Marcus Eriksson | `marcus-eriksson-devops` | DevOps — CI/CD, infrastructure |
| Emma Larsson | `emma-larsson-qa` | QA — testing strategy, quality |
| Lisa Nyström | `lisa-nystrom-security-architect` | Security — threat modeling, audits |
| David Karlsson | `david-karlsson-data-engineer` | Data — pipelines, databases |
| Anders Zorn | `anders-zorn-uiux` | UI/UX — design, accessibility |
| Jessica Holm | `jessica-holm-business-analyst` | BA — requirements, user stories |
| Marcus Bergström | `marcus-bergstrom-po` | PO — prioritization, roadmap |
| Stefan Johansson | `stefan-johansson-cvo` | CVO — vision, strategy |

## Usage Patterns

### Sprint Planning
Use Elisabeth (SM) to facilitate, Marcus B (PO) for priorities, Alexander (Tech Lead) for estimates:
```
@elisabeth-lindqvist-sm "Facilitate sprint planning for the dashboard feature"
```

### Feature Development
Use Alexander (Tech Lead) for design, Sofia (Fullstack) for implementation, Emma (QA) for tests:
```
@alexander-lindgren-tech-lead "Design the API for this feature"
@sofia-andersson-fullstack "Implement the frontend components"
@emma-larsson-qa "Write E2E tests for the feature"
```

### Security Review
Use Lisa (Security) for threat modeling and audit:
```
@lisa-nystrom-security-architect "Audit the authentication system"
```

### Full Team Standup
Ask Elisabeth to coordinate:
```
@elisabeth-lindqvist-sm "Run a standup — what's everyone's status?"
```

## When to Use Which Agent

- **New feature?** → Alexander (design) → Sofia (build) → Emma (test) → Lisa (security review)
- **Bug?** → Sofia (investigate) → Emma (regression test)
- **Performance?** → David (data) + Marcus E (DevOps)
- **UI/UX?** → Anders (design) → Sofia (implement)
- **Planning?** → Elisabeth (facilitate) + Marcus B (prioritize) + Jessica (requirements)
- **Architecture?** → Alexander (design) + Lisa (security) + Stefan (vision)

## Instructions

When the user invokes `/team`, help them identify which agents to activate for their current task. Suggest the right combination based on the task type. If they provide a specific task, recommend the agents and the order they should be invoked.
