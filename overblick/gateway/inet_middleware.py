"""
Security middleware stack for the Internet Gateway.

Middleware order (outermost first):
1. RequestSizeLimitMiddleware — reject oversized bodies
2. IPBanMiddleware — reject banned IPs
3. IPAllowlistMiddleware — reject unlisted IPs (if configured)
4. GlobalRateLimitMiddleware — global token bucket

Per-key rate limiting is handled in the route handler (after auth).
"""

import ipaddress
import logging
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def _error_response(status: int, message: str, error_type: str) -> JSONResponse:
    """Return an OpenAI-compatible error response."""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "code": str(status)}},
    )


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies exceeding the configured limit."""

    def __init__(self, app, max_bytes: int = 65_536):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            logger.warning(
                "Request too large: %s bytes from %s",
                content_length, request.client.host if request.client else "unknown",
            )
            return _error_response(413, "Request body too large", "invalid_request_error")

        return await call_next(request)


class ViolationTracker:
    """Track violations per IP for auto-ban decisions.

    In-memory sliding window — fast lookups, bounded memory via cleanup.
    """

    def __init__(self, window_seconds: int = 300, threshold: int = 10, ban_duration: int = 3600):
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.ban_duration = ban_duration
        # ip -> list of violation timestamps
        self._violations: dict[str, list[float]] = defaultdict(list)
        # ip -> ban expiry timestamp
        self._bans: dict[str, float] = {}

    def record_violation(self, ip: str) -> bool:
        """Record a violation and return True if IP should be banned."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old violations
        violations = self._violations[ip]
        self._violations[ip] = [t for t in violations if t > cutoff]
        self._violations[ip].append(now)

        if len(self._violations[ip]) >= self.threshold:
            self._bans[ip] = now + self.ban_duration
            logger.warning(
                "Auto-banned IP %s: %d violations in %ds (ban duration: %ds)",
                ip, len(self._violations[ip]), self.window_seconds, self.ban_duration,
            )
            return True

        return False

    def is_banned(self, ip: str) -> bool:
        """Check if an IP is currently banned."""
        expires = self._bans.get(ip)
        if expires is None:
            return False
        if time.time() > expires:
            del self._bans[ip]
            self._violations.pop(ip, None)
            return False
        return True

    def ban_remaining(self, ip: str) -> int:
        """Get remaining ban time in seconds (0 if not banned)."""
        expires = self._bans.get(ip)
        if expires is None:
            return 0
        remaining = int(expires - time.time())
        return max(0, remaining)


class IPBanMiddleware(BaseHTTPMiddleware):
    """Reject requests from banned IPs."""

    def __init__(self, app, tracker: ViolationTracker):
        super().__init__(app)
        self.tracker = tracker

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = request.client.host if request.client else "unknown"

        if self.tracker.is_banned(client_ip):
            remaining = self.tracker.ban_remaining(client_ip)
            logger.warning("Rejected banned IP: %s (%ds remaining)", client_ip, remaining)
            response = _error_response(403, "Access denied", "access_denied")
            response.headers["Retry-After"] = str(remaining)
            return response

        return await call_next(request)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject requests from IPs not on the allowlist (if configured)."""

    def __init__(self, app, allowlist: list[str]):
        super().__init__(app)
        # Parse CIDR networks
        self.networks = [ipaddress.ip_network(cidr, strict=False) for cidr in allowlist]

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.networks:
            # No allowlist configured — allow all
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        try:
            addr = ipaddress.ip_address(client_ip)
            if any(addr in net for net in self.networks):
                return await call_next(request)
        except ValueError:
            pass

        logger.warning("IP not in allowlist: %s", client_ip)
        return _error_response(403, "Access denied", "access_denied")


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Global rate limiting using the framework's token bucket."""

    def __init__(self, app, rpm: int = 60):
        super().__init__(app)
        from overblick.core.security.rate_limiter import RateLimiter

        # Token bucket: rpm tokens, refill at rpm/60 per second
        self.limiter = RateLimiter(max_tokens=float(rpm), refill_rate=rpm / 60.0)
        self.rpm = rpm

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for health checks
        if request.url.path == "/health":
            return await call_next(request)

        if not self.limiter.allow("global"):
            retry_after = self.limiter.retry_after("global")
            logger.warning(
                "Global rate limit exceeded from %s",
                request.client.host if request.client else "unknown",
            )
            response = _error_response(429, "Rate limit exceeded", "rate_limit_error")
            response.headers["Retry-After"] = str(int(retry_after) + 1)
            response.headers["X-RateLimit-Limit"] = str(self.rpm)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rpm)
        return response
