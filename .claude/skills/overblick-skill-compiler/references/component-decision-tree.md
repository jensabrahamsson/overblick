# Component Decision Tree

Use this flowchart to determine which Överblick components to generate.

## Decision Flowchart

```
START: What does the feature need?
│
├─ Q1: Does it interact with an external service/API?
│  ├─ YES → Needs a PLUGIN (at minimum)
│  │  ├─ Q1a: Does it also have reusable analysis/processing logic?
│  │  │  └─ YES → Also needs a CAPABILITY
│  │  └─ Q1b: Does it need a new character persona?
│  │     └─ YES → Also needs a PERSONALITY
│  └─ NO → Continue to Q2
│
├─ Q2: Is the behavior reusable across multiple plugins?
│  ├─ YES → Needs a CAPABILITY
│  │  └─ Q2a: Does it need a new character persona?
│  │     └─ YES → Also needs a PERSONALITY
│  └─ NO → Continue to Q3
│
├─ Q3: Is this a new character/persona definition?
│  ├─ YES → Needs a PERSONALITY
│  │  └─ Q3a: Does the character need custom behavior beyond prompts?
│  │     └─ YES → Also needs a CAPABILITY or PLUGIN
│  └─ NO → The feature may not fit the framework, or it's a modification to existing code
│
└─ END
```

## Capability Sub-decisions

If creating a capability:

```
Q4: Does the capability need periodic work?
├─ YES → Override tick()
└─ NO → Leave tick() as default no-op

Q5: Does it react to events?
├─ YES → Override on_event(), document which events
└─ NO → Leave on_event() as default no-op

Q6: Does it contribute context to LLM prompts?
├─ YES → Override get_prompt_context()
└─ NO → Leave get_prompt_context() returning ""

Q7: Does it use the LLM?
├─ YES → Use ctx.llm_pipeline (NEVER ctx.llm_client directly)
└─ NO → No pipeline needed

Q8: Which bundle should it belong to?
├─ Existing bundle matches → Add to existing bundle
└─ No match → Create new bundle directory
```

### Existing Bundles

| Bundle | Contains | Purpose |
|--------|----------|---------|
| `system` | system_clock | Core capabilities injected into all agents |
| `psychology` | dream, therapy, emotional | Internal mental state (**DEPRECATED** — use personality.yaml) |
| `knowledge` | learning, loader | Knowledge acquisition and storage |
| `social` | openings | Social interaction patterns |
| `engagement` | analyzer, composer | Engagement scoring and composition |
| `conversation` | conversation_tracker | Conversation state management |
| `content` | summarizer | Content processing |
| `speech` | stt, tts | Speech-to-text and text-to-speech |
| `vision` | vision | Image/video analysis |
| `communication` | boss_request, email, gmail, telegram_notifier | Communication channels and notifications |
| `consulting` | personality_consultant | Cross-identity consulting |
| `monitoring` | host_inspection | System health monitoring |

## Component Matrix

| Feature Description | Plugin | Capability | Personality | Rationale |
|---|:---:|:---:|:---:|---|
| Discord bot integration | X | | | External API (Discord) |
| Sentiment analyzer | | X | | Reusable across plugins |
| New snarky character "Spike" | | | X | Character definition only |
| Twitter bot with mood tracking | X | X | | External API + reusable emotional state |
| Slack bot with new persona | X | | X | External API + new character |
| Translation capability | | X | | Reusable text processing |
| Full new agent identity | X | X | X | All three: API + behavior + character |
| RSS feed monitor | X | | | External service polling |
| Image description service | | X | | Reusable across plugins |
| Custom knowledge indexer | | X | | Reusable knowledge processing |
| Email triage agent | X | X | X | External API (Gmail) + classification capability + personality |

## Quick Decision Rules

1. **If it has an API key** → Plugin
2. **If two plugins could use it** → Capability
3. **If it's about WHO the agent is** → Personality
4. **If in doubt between Plugin and Capability** → Make it a Capability and have plugins consume it (more reusable)
5. **Composite features** are the norm, not the exception — most real features need 2+ components
