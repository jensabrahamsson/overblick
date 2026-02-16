# LLM-Specific Scenario Tests

Scenario files are organized by LLM model. Each model gets its own directory
with tuned assertion thresholds and expectations.

## Directory Structure

```
scenarios/
    qwen3_8b/              # Tuned for Qwen3:8b (Ollama)
        anomal.yaml        # Single-turn scenarios
        blixt.yaml
        bjork.yaml
        cherry.yaml
        natt.yaml
        prisma.yaml
        rost.yaml
        conversations/     # Multi-turn conversation scripts
            blixt_conversations.yaml
            cherry_conversations.yaml
            natt_conversations.yaml
            rost_conversations.yaml
        forum_posts/       # Forum post response tests
            blixt_posts.yaml
            cherry_posts.yaml
            natt_posts.yaml
            rost_posts.yaml
    mistral_7b/            # Future: Tuned for Mistral 7B
    llama3_8b/             # Future: Tuned for Llama 3 8B
```

## Selecting a Model

Set the `OVERBLICK_TEST_MODEL` environment variable:

```bash
# Default (Qwen3:8b)
pytest tests/personalities/ -v -s -m llm

# Explicit model selection
OVERBLICK_TEST_MODEL=qwen3_8b pytest tests/personalities/ -v -s -m llm

# Future: different model
OVERBLICK_TEST_MODEL=mistral_7b pytest tests/personalities/ -v -s -m llm
```

## Adding Support for a New LLM

1. **Create the scenario directory:**
   ```bash
   cp -r tests/personalities/scenarios/qwen3_8b tests/personalities/scenarios/your_model
   ```

2. **Create LLM hints for each personality:**
   ```bash
   # For each identity in overblick/identities/*/llm_hints/
   cp overblick/identities/blixt/llm_hints/qwen3_8b.yaml \
      overblick/identities/blixt/llm_hints/your_model.yaml
   ```

3. **Run tests and iterate:**
   ```bash
   OVERBLICK_TEST_MODEL=your_model pytest tests/personalities/test_single_turn_scenarios.py -v -s -m llm
   ```

4. **Adjust scenarios** — common changes:
   - Broaden `must_contain_any.keywords` if the model uses different vocabulary
   - Adjust `min_matches` thresholds downward for smaller models
   - Relax `max_length` constraints for verbose models
   - Add model-specific `tone_keywords` for voice verification

5. **Adjust LLM hints** — reinforce what the model struggles with:
   - `voice_reinforcement`: Critical character rules
   - `extra_examples`: Additional few-shot examples for voice consistency
   - `avoid`: Common mistakes the model makes with this personality
   - `style_notes`: High-level guidance for the model

## How It Works

The system has two layers of LLM-specific tuning:

1. **LLM Hints** (`overblick/identities/<name>/llm_hints/<model>.yaml`)
   - Injected into the system prompt by `build_system_prompt(model_slug="...")`
   - Contains extra voice reinforcement, examples, and avoidance lists
   - Ships with the repo — ready for use by anyone with the same LLM

2. **Scenario Assertions** (`tests/personalities/scenarios/<model>/`)
   - Define what to expect from each personality on each model
   - Tuned thresholds for keyword matching, length, tone
   - Different models may need different expectations for the same character
