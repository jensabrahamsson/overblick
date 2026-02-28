# Dashboard

## Overview

FastAPI + Jinja2 + htmx web dashboard for monitoring and managing the Överblick agent framework. Localhost only. No npm — vendored htmx, hand-crafted dark theme CSS.

## Architecture

```
dashboard/
├── app.py              — FastAPI application factory
├── auth.py             — Session-based authentication
├── config.py           — Dashboard configuration
├── security.py         — Security headers, CSRF protection
├── routes/             — Route handlers (one file per domain)
│   ├── dashboard.py    — Main dashboard view
│   ├── agents.py       — Agent status and control
│   ├── identities.py   — Identity stable browser
│   ├── audit.py        — Audit trail viewer
│   ├── observability.py — htmx polling partials
│   ├── settings.py     — Configuration management
│   ├── onboarding.py   — Onboarding chat
│   ├── moltbook.py     — Moltbook status/profiles
│   └── ...             — Plugin-specific routes
├── services/           — Business logic (separate from routes)
│   ├── identity.py     — Identity data loading
│   ├── supervisor.py   — Supervisor IPC client
│   ├── audit.py        — Audit log queries
│   └── ...
├── templates/          — Jinja2 templates
└── static/             — CSS, vendored htmx
```

## Running

```bash
# Start dashboard (default port 8080)
python -m overblick dashboard

# Custom port
python -m overblick dashboard --port 9090
```

## Key Features

- **Real-time monitoring**: htmx-powered polling (5s intervals) for agent status, gateway health, system metrics
- **Identity browser**: View and explore all personalities in the stable
- **Audit trail**: Query and browse structured action logs
- **Settings wizard**: 8-step guided setup at `/settings/`
- **Plugin dashboards**: Per-plugin status views (Moltbook, Kontrast, Spegel, etc.)
- **Authentication**: Session-based login with configurable password

## Design Decisions

- **No npm/bundler**: htmx.min.js is vendored, CSS is hand-written
- **Localhost only**: Dashboard binds to 127.0.0.1 by default
- **Read-mostly**: Dashboard primarily reads state; write operations go through the supervisor
- **Identity name validation**: All identity path parameters validated with `IDENTITY_NAME_RE` regex
