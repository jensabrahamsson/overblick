# Överblick Security Plan — 2026‑03‑03

**Status:** *För beta‑release*  
**Granskad kod:** `main` commit `5f99cea`  
**Granskare:** opencode  
**Prioritet:** Hög – måste åtgärdas före teknisk beta‑release

---

## 1. Sammanfattning av nuvarande säkerhetsstatus

### Sterkt (≥ 80/100)
- **SafeLLMPipeline** är `strict=True` som standard i orchestratorn.
- **Orchestrator** kopplar in alla säkerhetskomponenter och skickar pipeline till plugins.
- **Boundary‑markers** (`wrap_external_content`) används konsekvent i `ResponseGenerator`.
- **Fail‑closed** design – säkerhetskraschar blockar requests.
- **SecretsManager** använder Fernet‑encryption med keyring‑fallback.

### Sårbart (≤ 70/100)
- **`PluginContext` exponerar `llm_client` (raw)** – plugin kan kringgå hela säkerhetskedjan.
- **`ResponseGenerator` har raw‑client‑fallback** – vid felaktig initiering används osäker väg.
- **Moltbook‑plugin använder raw client** i `ChallengeHandler` och `ResponseRouter`.
- **Capability‑systemet blockerar inte** – varningar kan missas av beta‑testare.
- **Skip‑flaggor används i 20+ ställen** – risk för missbruk.

### Säkerhetsbetyg (1–100)
| Komponent | Betyg | Motivering |
|-----------|-------|------------|
| **SafeLLMPipeline** | 88 | strict=True default, tydliga stages, fail‑closed |
| **Orchestrator** | 90 | initierar med full säkerhetskedja |
| **PluginContext** | 62 | exponerar `llm_client` – stora attackytan |
| **ResponseGenerator** | 78 | bra prompt‑hygiene, men raw‑fallback |
| **PreflightChecker** | 85 | 3‑lager, deflection‑system |
| **SecretsManager** | 88 | Fernet, keyring‑fallback |
| **Plugin: Moltbook** | 65 | använder raw client för challenges |
| **Plugin: Email‑Agent** | 85 | enbart pipeline, bra hygiene |
| **Plugin: GitHub‑Agent** | 80 | pipeline, skip‑output‑safety för kod (acceptabelt) |

---

## 2. Prioritetsordning – vad måste åtgärdas före beta

### 🔴 **HÖG PRIO – kritiskt för beta‑säkerhet**
1. **Dölj `PluginContext.llm_client` i production‑mode**  
   *Risk:* Plugin‑författare kan kringgå hela säkerhetskedjan med en rad kod.  
   *Åtgärd:* Gör `llm_client` till en property som kastar exception i safe‑mode.  
   *Fil:* `overblick/core/plugin_base.py:60`

2. **Gör `ResponseGenerator` pipeline‑mandatory**  
   *Risk:* Vid felaktig initiering faller den tillbaka på raw client utan säkerhetskontroller.  
   *Åtgärd:* Kräv `llm_pipeline` eller explicit `allow_raw_fallback=False`‑flagga.  
   *Fil:* `overblick/capabilities/engagement/response_gen.py:36–57`

3. **Uppdatera Moltbook till SafeLLMPipeline**  
   *Risk:* Externa challenge‑frågor och API‑svar matas direkt till LLM utan preflight/output‑safety.  
   *Åtgärd:* Skicka `ctx.llm_pipeline` till `ChallengeHandler` och `ResponseRouter`.  
   *Filer:* `overblick/plugins/moltbook/challenge_handler.py`, `response_router.py`

### 🟡 **MEDEL PRIO – bör åtgärdas i beta‑fasen**
4. **Capability‑system: blockera vid saknad grant**  
   *Risk:* Varningar i loggar missas lätt. Plugin kan köra kapaciteter utan grant.  
   *Åtgärd:* Lägg till strikt‑läge som kastar `PermissionError` vid saknade behörigheter.  
   *Fil:* `overblick/core/plugin_capability_checker.py:60–112`

5. **Dokumentera skip‑flaggor som “core‑only”**  
   *Risk:* `skip_preflight` och `skip_output_safety` kan missbrukas av plugin‑författare.  
   *Åtgärd:* Lägg till tydliga varningar i kod och SECURITY.md.  
   *Filer:* `overblick/core/llm/pipeline.py`, `SECURITY.md`

### 🟢 **LÅG PRIO – förbättringar för production**
6. **Audit‑loggning av alla `llm_client`‑anrop** (om de tillåts)  
   *Åtgärd:* Logga varje raw‑client‑anrop med `WARNING`‑nivå för att spåra bypass‑försök.

7. **Boundary‑marker‑validation i preflight**  
   *Åtgärd:* Låt preflight kolla om marker‑höljet har brutits (t.ex. dubbella marker).

8. **Rate‑limit‑key‑isolation mellan plugin**  
   *Åtgärd:` Särskilj rate‑limit‑nycklar per plugin för att förhindra DoS mellan identiteter.

---

## 3. Implementeringssteg (detaljerad)

### Steg 1: Dölj `PluginContext.llm_client` i safe‑mode
```python
# I PluginContext (plugin_base.py)
@property
def llm_client(self):
    import os
    if os.environ.get("OVERBLICK_RAW_LLM", "0") == "0":
        raise RuntimeError(
            "Raw LLM client access is disabled in safe mode. "
            "Use ctx.llm_pipeline for secure LLM calls. "
            "Set OVERBLICK_RAW_LLM=1 to allow raw access (not recommended)."
        )
    return self._llm_client
```
**Test:** Kör alla unit‑tester med `OVERBLICK_RAW_LLM=1`, sedan med `=0` och se att plugins som använder raw client failar.

### Steg 2: Gör ResponseGenerator pipeline‑mandatory
```python
# I ResponseGenerator.__init__ (response_gen.py)
def __init__(
    self,
    llm_pipeline=None,          # Required in safe mode
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
    *,
    llm_client=None,
    allow_raw_fallback: bool = False,
):
    import os
    self._pipeline = llm_pipeline
    self._llm = llm_client
    
    # Safe‑mode enforcement
    if not self._pipeline:
        if allow_raw_fallback and self._llm:
            logger.warning("ResponseGenerator using raw client (allow_raw_fallback=True)")
        else:
            raise ValueError(
                "SafeLLMPipeline is required in safe mode. "
                "Provide llm_pipeline or set allow_raw_fallback=True."
            )
    
    # ... resten av init
```
**Test:** Uppdatera alla anrop till `ResponseGenerator` i koden (moltbook, email_agent, etc.) att skicka `llm_pipeline=self.ctx.llm_pipeline`.

### Steg 3: Uppdatera Moltbook till SafeLLMPipeline
```python
# I moltbook/plugin.py, ersätt:
# self._challenge_handler = PerContentChallengeHandler(llm_client=self.ctx.llm_client, ...)
# med:
self._challenge_handler = PerContentChallengeHandler(
    llm_pipeline=self.ctx.llm_pipeline,
    # ... andra parametrar
)

# I ChallengeHandler och ResponseRouter, ändra __init__ att acceptera pipeline:
def __init__(self, llm_pipeline=None, *, llm_client=None, ...):
    # Samma logik som ResponseGenerator
```
**Test:** Kör Moltbook‑tester med pipeline, verifiera att challenge‑lösning fortfarande fungerar.

### Steg 4: Capability‑system – strikt‑läge
```python
# I PluginCapabilityChecker.check_plugin()
def check_plugin(self, plugin_name, required_capabilities):
    # ... existing logic to collect missing
    
    import os
    strict = os.environ.get("OVERBLICK_STRICT_CAPABILITIES", "0") == "1"
    if missing and strict:
        raise PermissionError(
            f"Plugin '{plugin_name}' missing capability grants: {missing}. "
            f"Add to identity YAML under 'plugin_capabilities' section."
        )
    # ... existing warning logic
```
**Test:** Lägg till test som verifierar att `PermissionError` kastas i strikt‑läge.

### Steg 5: Dokumentera skip‑flaggor
```python
# I SafeLLMPipeline.chat() docstring, lägg till:
"""
SECURITY WARNING: skip_preflight and skip_output_safety are for CORE/SYSTEM‑GENERATED
content only (e.g., dream system, internal code analysis). Never expose these flags
to plugin‑controlled or external input paths. If you need to skip security stages,
you must validate that the content is 100% system‑generated and contains no user input.
"""
```
**Dokumentation:** Uppdatera `SECURITY.md` med sektion om skip‑flaggor.

---

## 4. Tidslinje

| Vecka | Åtgärd | Beräknad tid | Ansvarig |
|-------|--------|--------------|----------|
| **V1** (beta‑prep) | Steg 1–3 (kritiska) | 2 dagar | Core‑team |
| **V1** | Testning + fix av brytna tester | 1 dag | QA |
| **V2** | Steg 4–5 (förbättringar) | 1 dag | Core‑team |
| **V2** | Dokumentation + SECURITY.md‑uppdatering | 0.5 dag | Dokumentation |
| **V3** | Slutlig säkerhetsgranskning | 1 dag | Säkerhetsteam |

**Total tid:** ~5.5 dagar (inklusive buffert).

---

## 5. Testning & verifiering

### Automatiserade tester
1. **Unit‑tester för nya säkerhetsfunktioner**
   ```bash
   OVERBLICK_SAFE_MODE=0 OVERBLICK_RAW_LLM=0 pytest tests/security/ -v
   OVERBLICK_STRICT_CAPABILITIES=1 pytest tests/core/test_plugin_capability.py -v
   ```

2. **Integrationstester**
   - Kör alla plugin‑tester med `OVERBLICK_RAW_LLM=0`
   - Verifiera att Moltbook‑challenge‑lösning fungerar med pipeline
   - Testa capability‑warning‑ och error‑flöden

3. **Manuell testning**
   - Starta dashboard med nya miljövariabler
   - Ladda identity med saknade capability‑grants (ska varna/errora)
   - Testa att raw‑client‑access ger tydligt felmeddelande

### Verifieringskriterier
- [ ] Alla 4303 unit‑tester passerar med nya säkerhetsinställningar
- [ ] Inga plugin kan använda `ctx.llm_client` i safe‑mode utan explicit miljövariabel
- [ ] `ResponseGenerator` kräver pipeline eller explicit fallback‑flagga
- [ ] Moltbook fungerar med pipeline för challenge‑lösning
- [ ] Capability‑system varnar logiskt, errorar i strikt‑läge
- [ ] SECURITY.md innehåller tydliga instruktioner om skip‑flaggor

---

## 6. Dokumentation som måste uppdateras

### AGENTS.md
- Lägg till sektion om `OVERBLICK_RAW_LLM` och `OVERBLICK_STRICT_CAPABILITIES`
- Uppdatera “Latest Security Updates” med nya safe‑mode‑funktioner

### SECURITY.md
- Lägg till “Skip Flags Security Guidelines”
- Dokumentera capability‑systemets strikt‑läge
- Tydliggör “safe‑by‑default”‑principen

### README.md (Quick Start)
- Lägg till miljövariabel‑förklaringar i konfigurationsdelen
- Uppdatera “Security Architecture” med nya skyddslager

### API‑dokumentation (om separat)
- Dokumentera `PluginContext`‑ändringar för plugin‑utvecklare
- Ge exempel på hur man migrerar från raw client till pipeline

---

## 7. Riskanalys – vad händer om vi inte gör detta?

### Scenario A: Beta‑testare skriver plugin med `ctx.llm_client`
- **Risk:** Fullständig kringgång av preflight, output‑safety, rate‑limiting
- **Konsekvens:** Prompt‑injection, jailbreak, persona‑hijack möjligt
- **Sannolikhet:** Medium (tekniska beta‑testare kan testa gränser)

### Scenario B: `ResponseGenerator` initieras felaktigt
- **Risk:** Hela engagement‑systemet körs utan säkerhetskontroller
- **Konsekvens:** Alla forum‑inlägg, kommentarer, heartbeats är osäkra
- **Sannolikhet:** Låg (men katastrofal om det händer)

### Scenario C: Moltbook‑challenge får skadligt innehåll
- **Risk:** Externt API‑svar med injektion matas direkt till LLM
- **Konsekvens:** Möjlighet att manipulera challenge‑svar‑systemet
- **Sannolikhet:** Medium (externa API:er är ej pålitliga)

**Sammanfattning:** Åtgärderna ovan reducerar dessa risker från **Medium‑Hög** till **Låg**.

---

## 8. Nästa steg – efter beta‑release

1. **Automatisk “pipeline‑only”‑policy för nya plugins**  
   Skapa dekorator `@pipeline_only` som validerar att plugin inte använder raw client.

2. **Boundary‑marker‑validation i preflight**  
   Låt preflight känna igen och blockera brutna marker‑höljen.

3. **Rate‑limit‑key‑isolation**  
   Separata buckets per plugin för att förhindra cross‑plugin DoS.

4. **Säkerhets‑telemetri (opt‑in)**  
   Samla anonymiserade statistik om blockeringar, false‑positives, etc.

---

**Godkännande:**  
[ ] Säkerhetsteam  
[ ] Produktägare  
[ ] Teknisk ledning

**Version:** 1.0  
**Senast uppdaterad:** 2026‑03‑03  
**Nästa granskning:** 2026‑04‑01 (eller efter beta‑release)