"""
Överblick Web Dashboard — server-rendered monitoring and onboarding.

Security-first design:
- Bound to 127.0.0.1 only (hardcoded)
- Jinja2 with autoescape (XSS prevention)
- CSRF tokens on all forms
- Session cookies via itsdangerous
- Read-only agent monitoring (no start/stop)
- Rate-limited endpoints
"""
