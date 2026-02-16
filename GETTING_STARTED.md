# Getting Started with Överblick

**Överblick** is a security-focused multi-identity agent framework with a Swedish soul. This guide shows how to set up your first agent system with a **Supervisor** (boss agent) and an **AI Digest** plugin that sends daily AI news via email.

## What You Will Build

A system where:
1. **Supervisor** (the boss) manages agents with Asimov's Laws of Robotics as its ethos
2. **Anomal** (your first agent) wakes up at 07:00 every day
3. **AI Digest** plugin fetches AI news from RSS feeds
4. **LLM** (Qwen3) ranks and generates a digest in Anomal's voice
5. **Gmail** plugin sends the digest via SMTP
6. **Dashboard** shows status in a browser at localhost:8080

## Prerequisites

- **Python 3.13+**
- **Ollama** with the `qwen3:8b` model installed
- **SMTP service** (we use Brevo — free 300 emails/day)
- **macOS** (the project is macOS-optimized with Keychain integration)

## Step-by-Step Installation

### 1. Clone and Install

```bash
git clone https://github.com/jensabrahamsson/overblick.git
cd overblick

# Create virtual environment
python3.13 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .

# Verify installation
python -m overblick list
```

You should see:
```
Available personalities:
  - anomal
  - bjork
  - blixt
  - cherry
  - natt
  - prisma
  - rost
```

### 2. Start Ollama and Load the Model

```bash
# In a separate terminal
ollama serve

# Pull the Qwen3 8B model
ollama pull qwen3:8b
```

#### Cloud LLM Alternative

If you prefer to use a cloud provider (OpenAI, Anthropic, etc.) instead of Ollama, configure `provider: "cloud"` in your identity's LLM settings. **Note:** The cloud LLM client is currently a stub implementation that raises `NotImplementedError` — this is a placeholder for future cloud provider integration. For now, use Ollama or the LLM Gateway.

### 3. Set Up SMTP (Brevo)

**Why Brevo?** Free 300 emails/day, no credit card required, works out of the box.

1. Go to https://www.brevo.com
2. Create a free account
3. Go to **Settings → SMTP & API**
4. Copy the SMTP credentials:
   - **Server**: `smtp-relay.brevo.com`
   - **Port**: `587`
   - **Login**: (shown on the page, e.g. `your-login@smtp-brevo.com`)
   - **Password**: (click "Create New SMTP Key")
   - **From Email**: Your verified email

### 4. Configure Secrets

Överblick uses **Fernet-encrypted secrets** with the master key stored in macOS Keychain.

```bash
# Create a secrets file for Anomal (temporary plaintext)
cat > /tmp/anomal-secrets.yaml << 'EOF'
smtp_server: smtp-relay.brevo.com
smtp_port: 587
smtp_login: your-login@smtp-brevo.com      # Yours from Brevo
smtp_password: xsmtpsib-YOUR_KEY_HERE      # Yours from Brevo
smtp_from_email: you@example.com           # Your verified email
EOF

# Import and encrypt
python -m overblick secrets import anomal /tmp/anomal-secrets.yaml

# Delete the plaintext file (important!)
rm /tmp/anomal-secrets.yaml
```

**What happens?**
- Secrets are encrypted with Fernet using a master key from macOS Keychain
- Stored in `config/secrets/anomal.yaml` (encrypted, safe to commit)
- Decrypted at runtime only when Anomal needs them

### 5. Configure the Anomal Identity

Anomal already has an `identity.yaml`, but let's understand it:

```yaml
# overblick/identities/anomal/identity.yaml

name: anomal
display_name: Anomal
personality: anomal  # References personality.yaml for voice/character

# Which plugins to load (connectors)
connectors:
  - ai_digest
  - gmail

# AI Digest configuration
ai_digest:
  recipient: "you@example.com"  # ← Change to your email!
  hour: 7                              # Send at 07:00
  timezone: "Europe/Stockholm"
  top_n: 5                             # Pick top 5 articles
  feeds:
    - "https://feeds.arstechnica.com/arstechnica/technology-lab"
    - "https://techcrunch.com/category/artificial-intelligence/feed/"
    - "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"

# Gmail plugin configuration
gmail:
  draft_mode: false  # Send directly (not draft)
  check_interval_seconds: 300
  allowed_senders: []  # No inbound processing

# LLM settings
llm:
  model: "qwen3:8b"
  temperature: 0.7
  max_tokens: 2000
  provider: "ollama"
```

**Change the recipient to your email:**
```bash
# Open in editor
nano overblick/identities/anomal/identity.yaml

# Change line 118:
recipient: "your-email@example.com"
```

### 6. Start the Supervisor (The Boss)

```bash
# Start supervisor with Anomal
./scripts/supervisor.sh start anomal

# Check status
./scripts/supervisor.sh status
```

You should see:
```
✅ Supervisor: RUNNING (PID 12345)

Agent processes:
  jens  12346  0.1  0.2  python -m overblick run anomal

Recent activity (last 5 lines):
  2026-02-14 19:21:08,623 [INFO] Supervisor running: 1 agents active
```

### 7. Start the Web Dashboard

```bash
# In a new terminal
source venv/bin/activate
python -m overblick dashboard

# Open in browser
open http://localhost:8080
```

You should see:
- **Supervisor: Running** (green indicator)
- **Anomal** with green status (running)
- **ai_digest** + **gmail** badges
- **Active Agents: 1**

### 8. Test Manually (Optional)

If you don't want to wait until 07:00, you can test right away:

```bash
# Run the test script
venv/bin/python3 tests/manual/test_ai_digest_full.py
```

This runs the full AI Digest workflow:
1. Fetches RSS feeds
2. Ranks articles with the LLM
3. Generates a digest in Anomal's voice
4. Sends it via email

**Expected result:** After ~30-60 seconds, you receive an email with AI news!

## Concepts and Architecture

### Supervisor (The Boss)

**What:** A boss agent that manages multiple identity agents as subprocesses.

**Ethos:** Asimov's Three Laws of Robotics + GDPR + Data Security
- First Law: No harm to users
- Second Law: Obey users and supervisor (unless it conflicts with the First Law)
- Third Law: Protect the agent's existence (unless it conflicts with the First or Second Law)

**Communication:** IPC via Unix sockets with auth tokens

**Features:**
- Auto-restart on crash (max 3 times)
- Permission management (coming soon!)
- Audit logging of all agents

### Identities vs Personalities

**Personality** = Character (voice, traits, backstory, psychology)
- Defined in `overblick/identities/<name>/personality.yaml`
- Reusable building block
- Focus on "who is this agent?"

**Identity** = Operational configuration (plugins, LLM, schedule, secrets)
- Defined in `overblick/identities/<name>/identity.yaml`
- Focus on "what does this agent do?"
- References a personality

**Example:**
- **Anomal personality**: Intellectual humanist, James May voice, philosophical
- **Anomal identity**: Uses ai_digest + gmail, qwen3:8b, sends at 07:00

### Psychological Frameworks (Optional)

If your personality has a specific way of thinking about their inner world (Jungian, Stoic, Attachment Theory, Existential), configure it as a trait:

```yaml
# personality.yaml
psychological_framework:
  primary: "jungian"  # or "attachment_theory", "stoic", "existential"
  domains:
    - archetypes
    - shadow_work
  dream_interpretation: true
  self_reflection_style: "archetypal_analysis"
  key_concepts:
    - "The shadow is not evil — it's denied."
```

**When to use:**
- Character does Jungian dream interpretation (Anomal)
- Character analyzes through attachment theory (Cherry)
- Character has stoic acceptance philosophy (Björk)
- Character lives in existential paradoxes (Natt)

**When NOT to use:**
- Most personalities don't need this
- Regular emotional depth = traits + backstory + voice
- Only for characters with EXPLICIT named frameworks

**Do NOT add "psychology" to capabilities** — that's deprecated. Psychology is CHARACTER (how they think), not FUNCTIONALITY (what the system can do).

### Plugins (Connectors)

**What:** Self-contained modules that access the framework via `PluginContext`.

**Types:**
- **Connectors**: I/O to external systems (AI Digest, Gmail, Telegram, Moltbook)
- **Capabilities**: Reusable logic (engagement scoring, LLM prompting)

**Security:**
- All plugins use `SafeLLMPipeline` (never direct `llm_client`)
- External content wrapped with `wrap_external_content()` (boundary markers)
- Secrets via `ctx.get_secret(key)` (never hardcoded)
- Audit logging of all actions

**Lifecycle:**
```python
async def setup(self):    # Initialize (read config, secrets)
async def tick(self):     # Periodic work (scheduled)
async def teardown(self): # Cleanup
```

### LLM Pipeline (SafeLLMPipeline)

**6-step fail-closed security chain:**

```
External Input
    ↓
1. Sanitization (wrap_external_content)
    ↓
2. Preflight Check (is the prompt safe?)
    ↓
3. Rate Limiting (not too many requests)
    ↓
4. LLM Call (Ollama / Gateway)
    ↓
5. Output Safety (is the response safe?)
    ↓
6. Audit Log (log for transparency)
    ↓
Result (or blocked)
```

**Reasoning:** Qwen3 supports the `think` parameter for deep analysis
- ON (default): Better quality for digest, analysis, content creation
- OFF: Faster for chat, reactions

## Common Commands

```bash
# Supervisor management
./scripts/supervisor.sh start anomal          # Start with one agent
./scripts/supervisor.sh start anomal cherry   # Start with multiple agents
./scripts/supervisor.sh status                # Show status
./scripts/supervisor.sh logs -f               # Follow logs
./scripts/supervisor.sh restart anomal        # Restart agent
./scripts/supervisor.sh stop                  # Stop everything

# Run agent directly (without supervisor)
python -m overblick run anomal

# List personalities
python -m overblick list

# Dashboard
python -m overblick dashboard --port 8080

# Secrets management
python -m overblick secrets import <identity> <file.yaml>

# Tests
pytest tests/ -v -m "not llm"           # Fast tests (without LLM)
pytest tests/ -v -m llm                 # LLM personality tests
pytest tests/plugins/ai_digest/ -v      # AI Digest specific
```

## Log Files

```bash
# Supervisor
tail -f logs/supervisor/supervisor.log

# Anomal agent
tail -f logs/anomal/anomal.log

# Dashboard
tail -f logs/dashboard.log

# All logs for Anomal
ls logs/anomal/
```

## Troubleshooting

### "Supervisor already running"
```bash
./scripts/supervisor.sh stop
./scripts/supervisor.sh start anomal
```

### "No password configured" on dashboard
**Normal!** The dashboard has auto-login when no password is set. Just open http://localhost:8080 again.

### "Agent crashed (exit=2)"
Check the agent log:
```bash
tail -50 logs/anomal/anomal.log
```

Common causes:
- LLM (Ollama) is not running: `ollama serve`
- Missing secrets: `python -m overblick secrets import anomal <file>`
- Python venv: Use `./scripts/supervisor.sh` which uses venv automatically

### "IPC auth rejected"
The supervisor generates an auth token at startup. The dashboard reads it automatically. If the problem persists:
```bash
./scripts/supervisor.sh restart anomal
pkill -f "overblick dashboard"
python -m overblick dashboard
```

### "LLM returned empty response"
The LLM may be busy or having trouble with the prompt. Check the Ollama log:
```bash
tail -f ~/.ollama/logs/server.log
```

Try:
1. Restart Ollama: `pkill ollama && ollama serve`
2. Test manually: `ollama run qwen3:8b "hello"`
3. Check reasoning: AI Digest uses reasoning ON (slower but better)

### Email not sending
Check the Gmail plugin log:
```bash
grep -i "smtp\|email" logs/anomal/anomal.log
```

Verify secrets:
```bash
# Secrets exist and are decryptable
python -c "
from overblick.core.security.secrets_manager import SecretsManager
from pathlib import Path
sm = SecretsManager(Path('config/secrets'))
print('SMTP server:', sm.get('anomal', 'smtp_server'))
"
```

## Next Steps

### Add More Agents

```bash
# Start Cherry as well (moltbook plugin)
./scripts/supervisor.sh stop
./scripts/supervisor.sh start anomal cherry
```

### Create Your Own Personality

1. Copy an existing one: `cp -r overblick/identities/anomal overblick/identities/myagent`
2. Edit `personality.yaml` (voice, traits, backstory)
3. Edit `identity.yaml` (connectors, schedule)
4. Add secrets: `python -m overblick secrets import myagent secrets.yaml`
5. Start: `./scripts/supervisor.sh start myagent`

### Explore Capabilities

Capabilities are reusable logic:
- **psychology**: Dream system, therapy sessions, emotional state
- **knowledge**: Safe learning, knowledge loading
- **social**: Opening phrase selector
- **engagement**: Content analyzer, response composer

Activate in `identity.yaml`:
```yaml
capabilities:
  - psychology
  - knowledge
  - social
  - engagement
```

### Build a New Plugin

See the `/overblick-skill-compiler` skill or `docs/PLUGIN_DEVELOPMENT.md` for a guide.

## More Documentation

- **CLAUDE.md** — Complete architecture and principles
- **ARCHITECTURE.md** — Technical details
- **SECURITY.md** — Security model
- **VOICE_TUNING.md** — The Voice Tuner's Handbook (tuning identity voices for your LLM)
- **README.md** — Project overview

## Community

- **Issues**: https://github.com/jensabrahamsson/overblick/issues
- **Discussions**: https://github.com/jensabrahamsson/overblick/discussions

---

**Congratulations!** You now have a working Överblick system with supervisor, agent, and plugins. The system wakes up every morning at 07:00, fetches AI news, and sends a personalized digest in Anomal's voice.

Next time you open the dashboard, you will see Anomal working, the audit trail growing, and the supervisor monitoring everything according to Asimov's Laws. Welcome to ethical AI in practice!
