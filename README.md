# Blick

Security-focused multi-identity agent framework.

## Quick Start

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
python -m blick run anomal
```

## Architecture

```
blick/
  core/         - Framework core (orchestrator, identity, plugins, security, LLM)
  plugins/      - Plugin modules (moltbook, ...)
  identities/   - Per-identity YAML configs and prompts
config/         - Global config + encrypted secrets
data/           - Per-identity runtime data (gitignored)
logs/           - Per-identity log files (gitignored)
```
