"""
Authentication routes — login and logout.
"""

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import SESSION_COOKIE, SessionManager, get_session
from ..security import LoginForm, RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    config = request.app.state.config
    templates = request.app.state.templates

    # If no password configured, auto-login (always create fresh session)
    if not config.password:
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

    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
    })


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

    # Parse form
    form = await request.form()
    password = form.get("password", "")
    csrf_token = form.get("csrf_token", "")

    # Validate password (constant-time comparison to prevent timing attacks)
    if not hmac.compare_digest(password, config.password):
        logger.warning("Failed login attempt from %s", client_ip)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid password.",
        }, status_code=401)

    # Create session
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

    logger.info("Successful login from %s", client_ip)
    return response


@router.get("/logout")
async def logout(request: Request):
    """Log out and redirect to login."""
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
