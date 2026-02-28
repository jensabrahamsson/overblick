"""
Authentication routes — login and logout.

Supports two password modes:
- Legacy plaintext (OVERBLICK_DASH_PASSWORD env var) — hmac comparison
- bcrypt hash (dashboard.password_hash in YAML) — bcrypt.checkpw()
"""

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import LOGIN_CSRF_COOKIE, SESSION_COOKIE, SessionManager, get_session
from ..security import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_password(password: str, config) -> bool:
    """Verify password against configured hash or plaintext.

    Checks bcrypt hash first (preferred), then falls back to
    plaintext comparison for backward compatibility.
    """
    if config.password_hash:
        try:
            import bcrypt
            return bcrypt.checkpw(
                password.encode("utf-8"),
                config.password_hash.encode("utf-8"),
            )
        except Exception:
            logger.warning("bcrypt verification failed, denying access")
            return False

    if config.password:
        return hmac.compare_digest(password, config.password)

    return False


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    config = request.app.state.config
    templates = request.app.state.templates

    # If no password configured, auto-login (always create fresh session)
    if not config.auth_enabled:
        session_mgr: SessionManager = request.app.state.session_manager
        cookie_value, csrf_token = session_mgr.create_session()
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            cookie_value,
            httponly=True,
            samesite="strict",
            max_age=config.session_hours * 3600,
        )
        logger.info("Auto-login: no password configured, creating session")
        return response

    # If already authenticated with valid session, redirect to dashboard
    if get_session(request):
        return RedirectResponse("/", status_code=302)

    # Generate double-submit CSRF token for the login form
    login_csrf = SessionManager.generate_login_csrf()
    response = templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "csrf_token": login_csrf,
    })
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        login_csrf,
        httponly=True,
        samesite="strict",
        max_age=600,  # 10 minutes
    )
    return response


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    """Process login form submission."""
    config = request.app.state.config
    templates = request.app.state.templates
    rate_limiter: RateLimiter = request.app.state.rate_limiter

    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{client_ip}"
    if not rate_limiter.check(rate_key, config.login_rate_limit, config.login_rate_window):
        logger.warning("Login rate limit exceeded for %s", client_ip)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Too many login attempts. Please wait before trying again.",
        }, status_code=429)

    # Parse and validate form
    form = await request.form()
    password = form.get("password", "")
    csrf_token = form.get("csrf_token", "")

    # Validate CSRF token via double-submit cookie pattern
    cookie_csrf = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not SessionManager.validate_login_csrf(cookie_csrf, csrf_token):
        logger.warning("Login CSRF validation failed from %s", client_ip)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid form submission. Please try again.",
        }, status_code=403)

    # Validate password (bcrypt hash or legacy plaintext)
    if not _verify_password(password, config):
        logger.warning("Failed login attempt from %s", client_ip)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid password.",
        }, status_code=401)

    # Create session
    session_mgr: SessionManager = request.app.state.session_manager
    cookie_value, session_csrf = session_mgr.create_session()

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        cookie_value,
        httponly=True,
        samesite="strict",
        max_age=config.session_hours * 3600,
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE)

    logger.info("Successful login from %s", client_ip)
    return response


@router.get("/logout")
async def logout(request: Request):
    """Log out and redirect to login."""
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
