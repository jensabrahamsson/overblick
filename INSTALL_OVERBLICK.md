# Installing Överblick

Security-focused multi-identity agent framework.

## Prerequisites

- **Python 3.13+**
- **Ollama** (local LLM) — [install guide](https://ollama.com/download)
- *Optional:* Gmail account with [App Password](https://myaccount.google.com/apppasswords)
- *Optional:* Telegram bot via [@BotFather](https://t.me/BotFather)

## Quick Start

```bash
git clone https://github.com/jensabrahamsson/overblick.git
cd overblick
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dashboard]"

# Pull an LLM model
ollama pull qwen3:8b

# Run the setup wizard
python -m overblick setup
```

## Core Concepts

Överblick uses three key concepts that work together:

| Concept | What it is | Example |
|---------|-----------|---------|
| **Channel** | A communication pathway — how an agent connects to the outside world | Gmail, Telegram, Moltbook forum |
| **Agent** | A specific task runner — what gets done. Each use case creates an agent. | Email Agent, Social Media Agent, Notifications Agent |
| **Personality** | The voice and character that runs an agent — who does it | Anomal (sharp analyst), Cherry (warm storyteller), Stal (precise executor) |

An agent is assigned exactly one personality. The personality determines the agent's writing style, tone, decision-making approach, and expertise areas. You pick the personality for each agent during setup using the character carousel.

Multiple agents can share the same personality (e.g. both Notifications and Research could be run by Anomal), and a personality can be compatible with multiple agent types.

The setup wizard opens in your browser and walks you through:
1. **Welcome** — overview and ambient music
2. **Your identity** — name, email, timezone
3. **AI engine** — Ollama or LLM Gateway configuration
4. **Channels** — Gmail and Telegram (both optional)
5. **Use cases** — what should your agents do?
6. **Assign agents** — pick a personality for each agent via the character carousel
7. **Review & create** — generates all config files and encrypted secrets
8. **Complete** — you're ready to run

## What the Setup Wizard Creates

| Path | Purpose |
|------|---------|
| `config/overblick.yaml` | Global LLM and framework settings |
| `config/secrets/<agent>.yaml` | Fernet-encrypted secrets per agent |
| `data/<agent>/` | Agent data directory |
| `logs/<agent>/` | Agent log directory |

## Running Your Agents

```bash
# Start a single agent
python -m overblick run anomal

# Start the web dashboard (localhost:8080)
python -m overblick dashboard

# Start multiple agents with the supervisor
python -m overblick supervisor anomal cherry stal

# List available personalities
python -m overblick list
```

## Manual Configuration

For advanced users who prefer editing YAML directly:

1. Copy a personality template from `overblick/personalities/`
2. Edit `personality.yaml` with your custom settings
3. Create secrets manually:
   ```bash
   python -m overblick secrets import <agent> path/to/plaintext.yaml
   ```

## Troubleshooting

**Ollama not running:**
```bash
# Start Ollama
ollama serve

# Verify it's running
curl http://localhost:11434/api/tags
```

**Gmail App Password issues:**
- Requires 2-Factor Authentication enabled on your Google account
- Generate at: https://myaccount.google.com/apppasswords
- Use the 16-character app password, not your regular Gmail password

**Telegram bot setup:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token
4. Send a message to your bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find your `chat_id` in the response

**Port conflicts:**
The setup wizard automatically picks a random available port.
The dashboard defaults to port 8080. If that's taken:
```bash
python -m overblick dashboard --port 9090
```
