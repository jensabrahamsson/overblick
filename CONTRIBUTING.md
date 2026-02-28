# Contributing to Överblick

Thank you for considering contributing to Överblick! This guide covers the development setup, code standards, and PR process.

## Development Setup

```bash
git clone https://github.com/jensabrahamsson/overblick.git
cd overblick
python3.13 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# All unit + scenario tests (fast — no LLM or browser required)
pytest tests/ -v -m "not llm and not e2e"

# LLM personality tests (requires Ollama with qwen3:8b)
pytest tests/ -v -s -m llm --timeout=300

# Dashboard tests only
pytest tests/dashboard/ -v

# Specific plugin
pytest tests/plugins/telegram/ -v

# With coverage
pytest tests/ --cov=overblick -m "not llm and not e2e"
```

## Code Standards

### Language
All code, comments, log messages, and error messages **must be in English**. No exceptions.

### Style
- Python 3.13+
- Type hints on all public interfaces
- Tests for every module
- Security: all external content wrapped in boundary markers via `wrap_external_content()`

### Commit Messages
- Write in English
- Use conventional format: `FIX:`, `FEAT:`, `CHORE:`, `DOCS:`, `REFACTOR:`, `TEST:`
- Keep the first line under 72 characters

### Security First
Överblick uses a fail-closed security pipeline. When contributing:
- Never bypass the `SafeLLMPipeline` — use `ctx.llm_pipeline`, not raw clients
- Wrap external content with `wrap_external_content()` from `input_sanitizer`
- Access secrets via `ctx.get_secret()` — never hardcode credentials
- Add audit log entries for actions with side effects

## Plugin Development

Create a new plugin using the Claude Code skill:
```
/overblick-plugin-helper create plugin
```

Or follow the manual approach:

1. Create `overblick/plugins/<name>/plugin.py` with a class extending `PluginBase`
2. Implement `setup()`, `tick()`, and optionally `teardown()`
3. Register in `overblick/plugins/<name>/__init__.py`
4. Add tests in `tests/plugins/<name>/`
5. Add a `README.md` in the plugin directory

See [overblick-plugin-helper](.claude/skills/overblick-plugin-helper.md) for the full API reference and checklist.

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Write tests for new functionality
3. Ensure all tests pass: `pytest tests/ -v -m "not llm and not e2e"`
4. Update documentation if adding new features
5. Submit a PR with a clear description of changes

### Good First Contributions
- Create a new personality for the identity stable
- Extend an experimental plugin (Compass, Kontrast, Skuggspel, Spegel, Stage)
- Add test coverage for edge cases
- Improve dashboard UI/UX
- Add chaos tests (`tests/chaos/`)

## License

By contributing, you agree that your contributions will be licensed under the GPL v3 license.
