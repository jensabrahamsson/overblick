# Getting Started with Överblick

**Överblick** är ett säkerhetsfokuserat multi-identity agent framework med svensk själ. Denna guide visar hur du sätter upp ditt första agent-system med en **Supervisor** (chef) och en **AI Digest** plugin som skickar dagliga AI-nyheter via email.

## 🎯 Vad du kommer bygga

Ett system där:
1. **Supervisor** (chefen) hanterar agenter med Asimovs robotlagar som ethos
2. **Anomal** (din första agent) vaknar kl 07:00 varje dag
3. **AI Digest** plugin hämtar AI-nyheter från RSS-feeds
4. **LLM** (Qwen3) rankar och genererar ett digest i Anomals röst
5. **Gmail** plugin skickar digestet via SMTP
6. **Dashboard** visar status i webbläsare på localhost:8080

## 📋 Förutsättningar

- **Python 3.13+**
- **Ollama** med `qwen3:8b` modellen installerad
- **SMTP-tjänst** (vi använder Brevo - gratis 300 emails/dag)
- **macOS** (projektet är macOS-optimerat med Keychain-integration)

## 🚀 Steg-för-steg Installation

### 1. Klona och installera

```bash
git clone https://github.com/jensabrahamsson/overblick.git
cd overblick

# Skapa virtual environment
python3.13 -m venv venv
source venv/bin/activate

# Installera beroenden
pip install -e .

# Verifiera installation
python -m overblick list
```

Du borde se:
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

### 2. Starta Ollama och ladda modellen

```bash
# I en separat terminal
ollama serve

# Ladda Qwen3 8B modellen
ollama pull qwen3:8b
```

### 3. Sätt upp SMTP (Brevo)

**Varför Brevo?** Gratis 300 emails/dag, ingen kreditkort krävs, fungerar direkt.

1. Gå till https://www.brevo.com
2. Skapa gratis konto
3. Gå till **Settings → SMTP & API**
4. Kopiera SMTP-credentials:
   - **Server**: `smtp-relay.brevo.com`
   - **Port**: `587`
   - **Login**: (visas på sidan, typ `your-login@smtp-brevo.com`)
   - **Password**: (klicka "Create New SMTP Key")
   - **From Email**: Din verifierade email

### 4. Konfigurera secrets

Överblick använder **Fernet-krypterade secrets** med master key i macOS Keychain.

```bash
# Skapa secrets-fil för Anomal (temporär plaintext)
cat > /tmp/anomal-secrets.yaml << 'EOF'
smtp_server: smtp-relay.brevo.com
smtp_port: 587
smtp_login: your-login@smtp-brevo.com      # Ditt från Brevo
smtp_password: xsmtpsib-YOUR_KEY_HERE...        # Ditt från Brevo
smtp_from_email: you@example.com    # Din verifierade email
EOF

# Importera och kryptera
python -m overblick secrets import anomal /tmp/anomal-secrets.yaml

# Radera plaintext (viktigt!)
rm /tmp/anomal-secrets.yaml
```

**Vad händer?**
- Secrets krypteras med Fernet och master key från macOS Keychain
- Sparas i `config/secrets/anomal.yaml` (krypterad, säker att committa)
- Dekrypteras runtime bara när Anomal behöver dem

### 5. Konfigurera Anomal identity

Anomal har redan en `identity.yaml`, men låt oss förstå den:

```yaml
# overblick/personalities/anomal/identity.yaml

name: anomal
display_name: Anomal
personality: anomal  # Refererar till personality.yaml för röst/karaktär

# Vilka plugins ska laddas (connectors)
connectors:
  - ai_digest
  - gmail

# AI Digest konfiguration
ai_digest:
  recipient: "you@example.com"  # ← Ändra till din email!
  hour: 7                              # Skicka kl 07:00
  timezone: "Europe/Stockholm"
  top_n: 5                             # Välj top 5 artiklar
  feeds:
    - "https://feeds.arstechnica.com/arstechnica/technology-lab"
    - "https://techcrunch.com/category/artificial-intelligence/feed/"
    - "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"

# Gmail plugin konfiguration
gmail:
  draft_mode: false  # Skicka direkt (ej draft)
  check_interval_seconds: 300
  allowed_senders: []  # Ingen inbound processing

# LLM settings
llm:
  model: "qwen3:8b"
  temperature: 0.7
  max_tokens: 2000
  provider: "ollama"
```

**Ändra recipient till din email:**
```bash
# Öppna i editor
nano overblick/personalities/anomal/identity.yaml

# Ändra rad 118:
recipient: "din-email@example.com"
```

### 6. Starta Supervisor (Chefen)

```bash
# Starta supervisor med Anomal
./scripts/supervisor.sh start anomal

# Kontrollera status
./scripts/supervisor.sh status
```

Du borde se:
```
✅ Supervisor: RUNNING (PID 12345)

Agent processes:
  jens  12346  0.1  0.2  python -m overblick run anomal

Recent activity (last 5 lines):
  2026-02-14 19:21:08,623 [INFO] Supervisor running: 1 agents active
```

### 7. Starta Web Dashboard

```bash
# I en ny terminal
source venv/bin/activate
python -m overblick dashboard

# Öppna i browser
open http://localhost:8080
```

Du borde se:
- 🟢 **Supervisor: Running**
- 🟢 **Anomal** med grön prick (running)
- **ai_digest** + **gmail** badges
- **Active Agents: 1**

### 8. Testa manuellt (valfritt)

Om du inte vill vänta till 07:00 kan du testa direkt:

```bash
# Kör test-skriptet
venv/bin/python3 tests/manual/test_ai_digest_full.py
```

Detta kör hela AI Digest workflow:
1. Hämtar RSS feeds
2. Rankar artiklar med LLM
3. Genererar digest i Anomals röst
4. Skickar via email

**Förväntat resultat:** Efter ~30-60 sekunder får du ett email med AI-nyheter!

## 🎓 Koncept och Arkitektur

### Supervisor (Chefen)

**Vad:** En boss agent som hanterar flera identity-agenter som subprocesses.

**Ethos:** Asimovs Tre Robotlagar + GDPR + Datasäkerhet
- Första lagen: Ingen skada på användare
- Andra lagen: Lyda användare och supervisor (om ej konflikt)
- Tredje lagen: Skydda agentens existens (om ej konflikt)

**Kommunikation:** IPC via Unix sockets med auth tokens

**Features:**
- Auto-restart vid krasch (max 3 gånger)
- Permission management (kommande!)
- Audit logging av alla agenter

### Identities vs Personalities

**Personality** = Karaktär (voice, traits, backstory, psychology)
- Definierad i `overblick/personalities/<name>/personality.yaml`
- Reusable building block
- Fokus på "vem är denna agent?"

**Identity** = Operativ konfiguration (plugins, LLM, schedule, secrets)
- Definierad i `overblick/personalities/<name>/identity.yaml`
- Fokus på "vad gör denna agent?"
- Refererar till en personality

**Exempel:**
- **Anomal personality**: Intellektuell humanist, James May-röst, filosofisk
- **Anomal identity**: Använder ai_digest + gmail, qwen3:8b, skickar kl 07:00

### Plugins (Connectors)

**Vad:** Self-contained moduler som får access till framework via `PluginContext`.

**Typer:**
- **Connectors**: I/O till externa system (AI Digest, Gmail, Telegram, Moltbook)
- **Capabilities**: Återanvändbar logik (engagement scoring, LLM prompting)

**Security:**
- Alla plugins använder `SafeLLMPipeline` (aldrig direkt `llm_client`)
- External content wrapped med `wrap_external_content()` (boundary markers)
- Secrets via `ctx.get_secret(key)` (never hardcoded)
- Audit logging av alla actions

**Livscykel:**
```python
async def setup(self):    # Initialize (läs config, secrets)
async def tick(self):     # Periodisk arbete (schedulerad)
async def teardown(self): # Cleanup
```

### LLM Pipeline (SafeLLMPipeline)

**6-stegs fail-closed security chain:**

```
External Input
    ↓
1. Sanitization (wrap_external_content)
    ↓
2. Preflight Check (är prompten säker?)
    ↓
3. Rate Limiting (inte för många requests)
    ↓
4. LLM Call (Ollama / Gateway)
    ↓
5. Output Safety (är svaret säkert?)
    ↓
6. Audit Log (logga för transparency)
    ↓
Result (or blocked)
```

**Reasoning:** Qwen3 stödjer `think` parameter för djup analys
- ON (default): Bättre kvalitet för digest, analys, content creation
- OFF: Snabbare för chat, reactions

## 🔧 Vanliga Kommandon

```bash
# Supervisor management
./scripts/supervisor.sh start anomal          # Starta med en agent
./scripts/supervisor.sh start anomal cherry   # Starta med flera
./scripts/supervisor.sh status                # Visa status
./scripts/supervisor.sh logs -f               # Följ loggar
./scripts/supervisor.sh restart anomal        # Starta om
./scripts/supervisor.sh stop                  # Stoppa allt

# Kör agent direkt (utan supervisor)
python -m overblick run anomal

# Lista personligheter
python -m overblick list

# Dashboard
python -m overblick dashboard --port 8080

# Secrets management
python -m overblick secrets import <identity> <file.yaml>

# Tester
pytest tests/ -v -m "not llm"           # Snabba tester (utan LLM)
pytest tests/ -v -m llm                 # LLM personality tests
pytest tests/plugins/ai_digest/ -v      # AI Digest specifika
```

## 📊 Loggfiler

```bash
# Supervisor
tail -f logs/supervisor/supervisor.log

# Anomal agent
tail -f logs/anomal/anomal.log

# Dashboard
tail -f logs/dashboard.log

# Alla loggar för Anomal
ls logs/anomal/
```

## 🐛 Troubleshooting

### "Supervisor already running"
```bash
./scripts/supervisor.sh stop
./scripts/supervisor.sh start anomal
```

### "No password configured" på dashboard
**Normal!** Dashboard har auto-login när ingen password är satt. Öppna bara http://localhost:8080 igen.

### "Agent crashed (exit=2)"
Kolla agent-loggen:
```bash
tail -50 logs/anomal/anomal.log
```

Vanliga orsaker:
- LLM (Ollama) körs inte: `ollama serve`
- Saknade secrets: `python -m overblick secrets import anomal <file>`
- Python venv: Använd `./scripts/supervisor.sh` som använder venv automatiskt

### "IPC auth rejected"
Supervisorn genererar ett auth token vid start. Dashboard läser det automatiskt. Om problemet kvarstår:
```bash
./scripts/supervisor.sh restart anomal
pkill -f "overblick dashboard"
python -m overblick dashboard
```

### "LLM returned empty response"
LLM kan vara upptagen eller ha problem med prompten. Kolla Ollama-loggen:
```bash
tail -f ~/.ollama/logs/server.log
```

Försök:
1. Starta om Ollama: `pkill ollama && ollama serve`
2. Testa manuellt: `ollama run qwen3:8b "hello"`
3. Kontrollera reasoning: AI Digest använder reasoning ON (långsammare men bättre)

### Email skickas inte
Kolla Gmail plugin-loggen:
```bash
grep -i "smtp\|email" logs/anomal/anomal.log
```

Verifiera secrets:
```bash
# Secrets finns och är dekrypterbara
python -c "
from overblick.core.security.secrets_manager import SecretsManager
from pathlib import Path
sm = SecretsManager(Path('config/secrets'))
print('SMTP server:', sm.get('anomal', 'smtp_server'))
"
```

## 🎯 Nästa steg

### Lägg till fler agenter

```bash
# Starta Cherry också (moltbook plugin)
./scripts/supervisor.sh stop
./scripts/supervisor.sh start anomal cherry
```

### Skapa din egen personality

1. Kopiera en befintlig: `cp -r overblick/personalities/anomal overblick/personalities/myagent`
2. Redigera `personality.yaml` (voice, traits, backstory)
3. Redigera `identity.yaml` (connectors, schedule)
4. Lägg till secrets: `python -m overblick secrets import myagent secrets.yaml`
5. Starta: `./scripts/supervisor.sh start myagent`

### Utforska capabilities

Capabilities är återanvändbar logik:
- **psychology**: Dream system, therapy sessions, emotional state
- **knowledge**: Safe learning, knowledge loading
- **social**: Opening phrase selector
- **engagement**: Content analyzer, response composer

Aktivera i `identity.yaml`:
```yaml
capabilities:
  - psychology
  - knowledge
  - social
  - engagement
```

### Bygg ett nytt plugin

Se `/overblick-skill-compiler` skill eller `docs/PLUGIN_DEVELOPMENT.md` för guide.

## 📚 Mer dokumentation

- **CLAUDE.md** - Komplett arkitektur och principles
- **ARCHITECTURE.md** - Tekniska detaljer
- **SECURITY.md** - Säkerhetsmodell
- **README.md** - Projektöversikt

## 🤝 Community

- **Issues**: https://github.com/jensabrahamsson/overblick/issues
- **Discussions**: https://github.com/jensabrahamsson/overblick/discussions

---

**Grattis!** 🎉 Du har nu ett fungerande Överblick-system med supervisor, agent, och plugins. Systemet vaknar varje morgon kl 07:00, hämtar AI-nyheter, och skickar ett personligt digest i Anomals röst.

Nästa gång du öppnar dashboarder ser du Anomal arbeta, audit trail växa, och supervisor övervaka allt enligt Asimovs lagar. Välkommen till etisk AI i praktiken! 🤖
