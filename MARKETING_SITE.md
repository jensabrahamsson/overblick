# Överblick Marketing Site

Public-facing landing page for the Överblick agent framework.
Hosted on Vercel (Hobby plan). Zero dependencies, zero build step.

---

## Location

```
website/
├── index.html      (757 lines)   — Full marketing page
├── styles.css      (927 lines)   — Design system + dark theme
├── script.js       (86 lines)    — Minimal JS, no libraries
└── vercel.json     (14 lines)    — Security headers
```

## Design Philosophy

Target audience: LLM engineers, open-source developers — people who know what
Qwen3 is, who check the GPL license before cloning, who want to see real
architecture before reading any sales copy.

Inspiration: vllm.ai, llama.cpp, PyTorch, HuggingFace — technical legitimacy
over marketing fluff.

**No:**
- Vague claims ("AI-powered")
- External fonts or CDN dependencies
- Typing animations (they delay content and are a cliché)
- npm / build steps
- JavaScript libraries

**Yes:**
- Exact quotes from personality.yaml files
- Real code blocks that actually work
- Inline SVG pipeline diagram (not CSS boxes, not screenshots)
- Contrast ratios that pass WCAG AA on every element

## File Details

### index.html

Sections in order:

| Section | Description |
|---------|-------------|
| **Nav** | Sticky, minimal. GitHub link visible. Hamburger on mobile. |
| **Hero** | `Eight minds. One framework. Zero trust.` — terminal block, two CTAs |
| **Stats bar** | 8 Personalities · 6 Security stages · 1700+ Tests · 10 Plugins · GPL v3 · Python 3.13 |
| **Personality Stable** | 8 cards, each with accent color, glyph, tagline, trait bars, warm-gold tag pills, expandable voice sample |
| **SafeLLM Pipeline** | Inline SVG diagram (940×200 viewBox) with 6 stages + BLOCKED indicators; replaced by ordered list on mobile |
| **Architecture Overview** | 3-column: Core / Security / LLM Backends |
| **Plugin Ecosystem** | 10 plugins, badge-labelled stable/shell |
| **Get Started** | 3 numbered steps with real code blocks + wizard-hint to dashboard |
| **Built With** | Tech stack pills: Python 3.13, Ollama, Qwen3:8b, FastAPI, htmx, SQLite/PostgreSQL, GPL v3 |
| **Footer** | `◈ Överblick — GPL v3 · Built in Sweden` + GitHub / Docs / Issues links |

### The 8 Personality Cards

Each card: accent color, Unicode glyph, role/location, tagline, 3 trait bars
(animated via IntersectionObserver), 3–4 warm-gold tag pills, expandable voice
sample with a verbatim quote from personality.yaml.

| Agent | Accent | Glyph | Quote source |
|-------|--------|-------|-------------|
| Anomal | `#58a6ff` | `◎` | `example_conversations.admitting_uncertainty` |
| Cherry | `#ff6b9d` | `♡` | `psychological_framework.key_concepts[0]` |
| Blixt  | `#ff4444` | `⚡` | `moltbook_bio` (first two sentences) |
| Björk  | `#4caf7d` | `✦` | `psychological_framework.key_concepts[0]` |
| Natt   | `#9b72cf` | `◐` | `psychological_framework.key_concepts[0]` + `backstory.origin` |
| Prisma | `#f0a500` | `◈` | `backstory.origin` (demoscene lesson) |
| Rost   | `#ff8c42` | `⊘` | Adapted from `backstory.origin` (Luna collapse) |
| Stål   | `#a8b5c0` | `⊞` | `backstory.origin` (diplomatic cables) |

All quotes are verbatim from the YAML files — nothing invented.

### SafeLLM Pipeline SVG

Inline SVG, `viewBox="0 0 940 200"`, `width: 100%; height: auto`.

Stages rendered with CSS classes that reference custom properties
(`--bg-tertiary`, `--border`, `--accent`, `--red`) so they adapt if the
theme ever changes.

```
INPUT → [1.SANITIZE] → [2.PREFLIGHT] → [3.RATE LIMIT] → [4.LLM CALL] → [5.OUT SAFETY] → [6.AUDIT LOG] → OUTPUT
               ↓ block        ↓ block         ↓ throttle                      ↓ filter           ↓ log
            BLOCKED        BLOCKED          BLOCKED                         BLOCKED           BLOCKED
```

Stage 4 (LLM CALL) is accented with `--accent` color and has no block
indicator — it's the actual model call, not a security gate. On mobile
(`< 768px`) the SVG is hidden and replaced with an accessible `<ol>` list.

### styles.css — Design Tokens

```css
--bg-primary:    #0d1117;   /* GitHub dark */
--bg-secondary:  #161b22;
--bg-card:       #161b22;
--bg-tertiary:   #21262d;
--border:        #30363d;
--text-primary:  #e6edf3;
--text-secondary: #8b949e;
--accent:        #58a6ff;
--green:         #3fb950;
--red:           #f97583;
--warm-gold:     #c9a96e;   /* personality element accent */
--font-display:  Georgia, serif;         /* Hero H1 */
--font-body:     system-ui, sans-serif;
--font-mono:     'SFMono-Regular', Consolas, monospace;
```

Typography scale:

| Element | Size | Weight | Line-height |
|---------|------|--------|-------------|
| Hero H1 | 3.5rem | 700 | 1.1 |
| Section H2 | 2.5rem | 700 | 1.2 |
| Card H3 | 1.3rem | 600 | 1.3 |
| Body | 1.1rem | 400 | 1.75 |
| Code | 0.9rem | 400 | 1.6 |
| Meta/caption | 0.85rem | 400 | 1.5 |

Responsive breakpoints:

| Breakpoint | Layout |
|-----------|--------|
| `> 1200px` | 4-column personality grid |
| `> 1024px` | Full layout |
| `768–1024px` | 2-column personality grid, reduced padding |
| `< 768px` | Single column, pipeline SVG → list |
| `< 640px` | Stacked CTAs, compressed stats, terminal hidden |

### script.js — What the JS Does

1. **Hamburger menu** — toggles `nav-links--open` class, updates `aria-expanded`
2. **Trait bar animation** — `IntersectionObserver` sets `transform: scaleX(n)` when cards enter viewport (0.6s cubic-bezier)
3. **Stat counter animation** — `requestAnimationFrame` loop, 600ms, eased
4. **Active nav link** — `IntersectionObserver` on `section[id]`, highlights current section in nav
5. **Hero fade-in** — sets `opacity: 0` then transitions to `1` on next frame (replaces typing effect)

No external libraries. Works without JavaScript (all content visible, only
animations degrade gracefully).

### vercel.json — Security Headers

```json
{
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options",  "value": "nosniff" },
        { "key": "X-Frame-Options",         "value": "DENY" },
        { "key": "Referrer-Policy",         "value": "strict-origin-when-cross-origin" },
        { "key": "Cache-Control",           "value": "public, max-age=3600" },
        { "key": "Content-Security-Policy", "value": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self';" }
      ]
    }
  ]
}
```

CSP is strict `'self'`-only — no inline scripts, no CDN fonts, no external
anything. Consistent with the framework's security posture.

---

## Local Development

```bash
# Open directly in browser (works without server)
open website/index.html

# Local server (recommended — CSS/JS refs need HTTP)
python -m http.server 8081 --directory website
# → http://localhost:8081/
```

No watch mode needed. Edit files, reload browser.

---

## Deployment — Vercel

### Option A: CLI (one-off)

```bash
# Install Vercel CLI if not already installed
npm i -g vercel

# Deploy (uses vercel.json in website/)
vercel --cwd website

# Production deploy
vercel --cwd website --prod
```

### Option B: GitHub auto-deploy (recommended)

1. Push this repo to GitHub (as `jensabrahamsson/overblick`)
2. Go to vercel.com → New Project → Import repository
3. **Root Directory:** `website`
4. **Framework Preset:** Other
5. **Build Command:** *(leave empty)*
6. **Output Directory:** `.` (dot — the website/ folder itself)
7. Click Deploy

Every push to `main` auto-deploys in ~10–15 seconds.

### Custom Domain

In Vercel dashboard → Settings → Domains → add your domain.
Vercel handles HTTPS automatically.

---

## Design Decisions — Rationale

**Why inline SVG for the pipeline, not CSS boxes?**
CSS pseudo-element arrows break on mobile and require browser-specific hacks.
Inline SVG is resolution-independent, styleable with CSS custom properties,
accessible via `role="img"` + `aria-label`, and scales proportionally with
`width: 100%; height: auto`. One element, zero hacks.

**Why `<details>/<summary>` for voice samples?**
Native HTML expand/collapse — zero JavaScript, zero event listeners. Works
without JS. Keyboard accessible by default. Degrades gracefully.

**Why warm gold (`#c9a96e`) for personality elements?**
Separates the "human/character" layer from the "technical/UI" layer visually.
Accent blue (`#58a6ff`) means framework/technical. Warm gold means personality.
The distinction is consistent throughout the page.

**Why no typing animation?**
Typing animations delay content by 2–5 seconds, add zero information, and
signal "I saw this on a template site in 2019." Replaced with a simple 0.3s
`opacity` fade-in that is immediate and professional.

**Why Georgia for the hero?**
A serif font for the hero H1 creates visual tension against the monospace/
sans-serif body. It signals craft and intentionality — the same choice made
by vllm.ai and similar serious open-source projects. It also loads instantly:
Georgia is a system font on all major platforms.

---

## Accessibility

All WCAG AA requirements met:

| Check | Status |
|-------|--------|
| Heading hierarchy (H1→H2→H3) | ✅ No skipped levels |
| Semantic landmarks (nav, main, section, footer) | ✅ All present |
| `lang="en"` on html element | ✅ |
| Meta viewport | ✅ |
| SVG accessible | ✅ `role="img"` + `aria-label` |
| Skip nav link | ✅ |
| Focus-visible ring | ✅ `outline: 2px solid var(--accent)` |
| Body text contrast | ✅ ~10:1 on dark background |
| Tag/gold text contrast | ✅ ~7:1 on dark background |
| Empty links | ✅ None |
| Images without alt | ✅ None (no bitmap images used) |

---

## What NOT to Change

- Do not add `async`/`defer` inline scripts — breaks the strict CSP
- Do not load fonts from Google Fonts or any CDN — CSP blocks it and requires no-external design
- Do not add `npm install` or any build step — Vercel deploys this as static files
- Do not change `--bg-primary` without updating the hero dot-grid radial-gradient
- Do not invent agent quotes — all voice samples must come verbatim from `overblick/identities/*/personality.yaml`
