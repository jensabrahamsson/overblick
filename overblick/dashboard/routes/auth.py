"""
Authentication routes — login and logout.

Supports bcrypt password hash (dashboard.password_hash in YAML).
"""

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import LOGIN_CSRF_COOKIE, SESSION_COOKIE, SessionManager, get_session
from ..security import RateLimiter


def _should_use_secure_cookie(request: Request) -> bool:
    """Determine if cookies should be marked Secure.

    Returns True if:
    - Request scheme is https, OR
    - X-Forwarded-Proto header is https (reverse proxy), OR
    - Network access mode is enabled (assumes HTTPS should be used).
    """
    config = request.app.state.config
    if request.url.scheme == "https":
        return True
    if request.headers.get("X-Forwarded-Proto") == "https":
        return True
    # Network access mode typically requires HTTPS for security
    if config.network_access:
        return True
    return False


logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_password(password: str, config) -> bool:
    """Verify password against bcrypt hash."""
    if not config.password_hash:
        return False

    try:
        import bcrypt

        return bcrypt.checkpw(
            password.encode("utf-8"),
            config.password_hash.encode("utf-8"),
        )
    except Exception:
        logger.warning("bcrypt verification failed, denying access")
        return False


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    config = request.app.state.config
    templates = request.app.state.templates

    # If no password configured, auto-login (always create fresh session)
    if not config.auth_enabled:
        session_mgr: SessionManager = request.app.state.session_manager
        cookie_value, _csrf_token = session_mgr.create_session()
        response = RedirectResponse("/", status_code=302)
        secure = _should_use_secure_cookie(request)
        response.set_cookie(
            SESSION_COOKIE,
            cookie_value,
            httponly=True,
            secure=secure,
            samesite="strict",
            max_age=config.effective_session_hours * 3600,
        )
        logger.info("Auto-login: no password configured, creating session")
        return response

    # If already authenticated with valid session, redirect to dashboard
    if get_session(request):
        return RedirectResponse("/", status_code=302)

    # Generate double-submit CSRF token for the login form
    login_csrf = SessionManager.generate_login_csrf()
    response = templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "csrf_token": login_csrf,
        },
    )
    secure = _should_use_secure_cookie(request)
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        login_csrf,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=600,  # 10 minutes
    )
    return response


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    """Process login form submission."""
    config = request.app.state.config
    templates = request.app.state.templates
    rate_limiter: RateLimiter = request.app.state.rate_limiter

    # Helper: render login with fresh CSRF (every error response needs this)
    def _login_error(error_msg: str, status_code: int = 403):
        fresh_csrf = SessionManager.generate_login_csrf()
        resp = templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": error_msg,
                "csrf_token": fresh_csrf,
            },
            status_code=status_code,
        )
        secure = _should_use_secure_cookie(request)
        resp.set_cookie(
            LOGIN_CSRF_COOKIE,
            fresh_csrf,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=600,
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{client_ip}"
    if not rate_limiter.check(rate_key, config.login_rate_limit, config.login_rate_window):
        logger.warning("Login rate limit exceeded for %s", client_ip)
        return _login_error("Too many login attempts. Please wait before trying again.", 429)

    # Parse and validate form
    form = await request.form()
    password = form.get("password", "")
    csrf_token = form.get("csrf_token", "")

    # Validate CSRF token via double-submit cookie pattern
    cookie_csrf = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not SessionManager.validate_login_csrf(cookie_csrf, csrf_token):
        logger.warning("Login CSRF validation failed from %s", client_ip)
        return _login_error("Invalid form submission. Please try again.")

    # Validate password (bcrypt hash or legacy plaintext)
    if not _verify_password(password, config):
        logger.warning("Failed login attempt from %s", client_ip)
        return _login_error("Invalid password.", 401)

    # Create session
    session_mgr: SessionManager = request.app.state.session_manager
    cookie_value, _session_csrf = session_mgr.create_session()

    response = RedirectResponse("/", status_code=302)
    secure = _should_use_secure_cookie(request)
    response.set_cookie(
        SESSION_COOKIE,
        cookie_value,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=config.effective_session_hours * 3600,
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
