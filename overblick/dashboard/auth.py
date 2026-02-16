"""
Authentication — session management, CSRF protection, middleware.

Security properties:
- Password-based login (dashboard_password required)
- Session cookies signed with itsdangerous (tamper-proof)
- CSRF tokens embedded in forms and htmx headers
- Sessions expire after configurable hours
"""

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger(__name__)

# Cookie names
SESSION_COOKIE = "overblick_session"
CSRF_COOKIE = "overblick_csrf"
LOGIN_CSRF_COOKIE = "overblick_login_csrf"

# Paths that don't require authentication
PUBLIC_PATHS = {"/login", "/static", "/health"}


class SessionManager:
    """
    Server-side session management using itsdangerous signed cookies.

    The cookie contains a signed JSON payload with:
    - authenticated: bool
    - csrf_token: str
    - created_at: float
    """

    def __init__(self, secret_key: str, max_age_hours: int = 8):
        self._serializer = URLSafeTimedSerializer(secret_key)
        self._max_age = max_age_hours * 3600

    def create_session(self) -> tuple[str, str]:
        """
        Create a new authenticated session.

        Returns:
            (session_cookie_value, csrf_token)
        """
        csrf_token = secrets.token_hex(32)
        payload = {
            "authenticated": True,
            "csrf_token": csrf_token,
            "created_at": time.time(),
        }
        cookie_value = self._serializer.dumps(payload)
        return cookie_value, csrf_token

    def validate_session(self, cookie_value: str) -> Optional[dict]:
        """
        Validate a session cookie.

        Returns:
            Session payload dict if valid, None otherwise.
        """
        try:
            payload = self._serializer.loads(cookie_value, max_age=self._max_age)
            if payload.get("authenticated"):
                return payload
        except (BadSignature, SignatureExpired):
            pass
        return None

    def validate_csrf(self, session_data: dict, token: str) -> bool:
        """Validate CSRF token against session."""
        expected = session_data.get("csrf_token", "")
        if not expected or not token:
            return False
        return hmac.compare_digest(expected, token)

    @staticmethod
    def generate_login_csrf() -> str:
        """Generate a CSRF token for the login form (pre-auth, double-submit cookie)."""
        return secrets.token_hex(32)

    @staticmethod
    def validate_login_csrf(cookie_token: str, form_token: str) -> bool:
        """Validate login CSRF via double-submit cookie comparison."""
        if not cookie_token or not form_token:
            return False
        return hmac.compare_digest(cookie_token, form_token)


def get_session(request: Request) -> Optional[dict]:
    """Extract and validate session from request cookies."""
    session_mgr: SessionManager = request.app.state.session_manager
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    return session_mgr.validate_session(cookie)


def check_csrf(request: Request, session_data: dict) -> bool:
    """Validate CSRF token from htmx header."""
    session_mgr: SessionManager = request.app.state.session_manager
    token = request.headers.get("X-CSRF-Token", "")
    if not token:
        return False
    return session_mgr.validate_csrf(session_data, token)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces authentication on all non-public routes.

    Redirects unauthenticated users to /login.
    Validates CSRF on all non-GET requests.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Allow public paths
        if any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        # Check session
        session_data = get_session(request)
        if not session_data:
            if request.headers.get("HX-Request"):
                # htmx request — return 401 with redirect header
                response = Response(status_code=401)
                response.headers["HX-Redirect"] = "/login"
                return response
            return RedirectResponse("/login", status_code=302)

        # Store session data on request state for route handlers
        request.state.session = session_data

        # CSRF check on mutating requests — always required
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token = request.headers.get("X-CSRF-Token", "")
            if not token:
                logger.warning("Missing CSRF token for %s %s", request.method, path)
                return Response("CSRF token required", status_code=403)
            session_mgr: SessionManager = request.app.state.session_manager
            if not session_mgr.validate_csrf(session_data, token):
                logger.warning("CSRF validation failed for %s %s", request.method, path)
                return Response("CSRF validation failed", status_code=403)

        return await call_next(request)
