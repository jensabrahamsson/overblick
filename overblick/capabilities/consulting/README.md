# Consulting Capabilities

## PersonalityConsultantCapability

Consult any personality in the stable for advice — without requiring that personality to be running as a separate agent.

### How It Works

1. **Load** the consultant's personality YAML via `load_personality()`
2. **Build** their system prompt via `build_system_prompt(personality, platform="Internal Consultation")`
3. **Call** the LLM through the caller's `SafeLLMPipeline` with the consultant's system prompt
4. **Return** the advice to the calling plugin

No IPC, no separate processes — just a prompt-swap pattern.

### Usage

```python
# Get the capability
consultant = ctx.get_capability("personality_consultant")

# Ask Cherry for tone advice
advice = await consultant.consult(
    query="Should this reply be warm or professional?",
    context=f"Email from: {sender}\nSubject: {subject}\nBody: {body}",
    consultant_name="cherry",
)
```

### Configuration

In personality YAML under `operational.personality_consultant`:

```yaml
operational:
  capabilities:
    - personality_consultant
  personality_consultant:
    default_consultant: "cherry"   # Which personality to consult by default
    temperature: 0.7               # LLM temperature for consultations
    max_tokens: 800                # Max response length
```

### Design Decisions

- **Generic**: Any agent can consult any personality — not coupled to specific agents
- **Lazy loading**: Personalities are cached on first use, no upfront cost
- **Graceful degradation**: Returns `None` if personality not found, LLM unavailable, or response blocked
- **Low priority**: Consultations run at `priority="low"` to avoid blocking interactive tasks
- **Skip preflight**: Internal system-generated content doesn't need injection checks

### Bundle

```python
CAPABILITY_BUNDLES = {
    "consulting": ["personality_consultant"],
}
```

### Current Consumers

- **Stål (email agent)**: Consults Cherry for tone advice before generating email replies
