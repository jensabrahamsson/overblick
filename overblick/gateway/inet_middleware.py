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
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def _extract_client_ip(request, trusted_proxies: list[str]) -> str:
    """Extract client IP from request with X-Forwarded-For validation.

    Args:
        request: Starlette Request object
        trusted_proxies: List of CIDR strings for trusted proxy IPs.

    Returns:
        Client IP as string, or "unknown" if unable to determine.
    """
    if not trusted_proxies:
        # No trusted proxies configured — ignore X-Forwarded-For for security
        return request.client.host if request.client else "unknown"

    # Parse trusted proxy networks (CIDR)
    trusted_networks = []
    for cidr in trusted_proxies:
        try:
            trusted_networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("Invalid trusted_proxy CIDR: %s", cidr)

    # Helper to check if IP is trusted
    def is_trusted(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return any(ip in net for net in trusted_networks)

    # Get remote IP (the immediate connection)
    remote_ip = request.client.host if request.client else None
    if remote_ip is None:
        return "unknown"

    # Parse X-Forwarded-For header (comma-separated, leftmost is original client)
    forwarded_header = request.headers.get("x-forwarded-for", "")
    forwarded_ips = [ip.strip() for ip in forwarded_header.split(",") if ip.strip()]

    # Build chain: forwarded_ips + [remote_ip]
    chain = [*forwarded_ips, remote_ip]

    # Walk from rightmost to leftmost, stopping at first untrusted IP
    for ip in reversed(chain):
        if is_trusted(ip):
            continue
        return ip

    # All IPs in chain are trusted — return the original client (leftmost)
    return chain[0] if chain else "unknown"


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
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                return _error_response(
                    400, "Invalid Content-Length header", "invalid_request_error"
                )
            if length > self.max_bytes:
                logger.warning(
                    "Request too large: %d bytes from %s",
                    length,
                    request.client.host if request.client else "unknown",
                )
                return _error_response(413, "Request body too large", "invalid_request_error")

        return await call_next(request)


class ViolationTracker:
    """Track violations per IP for auto-ban decisions.

    In-memory sliding window — fast lookups, bounded memory via max_tracked_ips.
    When the IP cap is reached, oldest entries are evicted to stay within bounds.
    """

    def __init__(
        self,
        window_seconds: int = 300,
        threshold: int = 10,
        ban_duration: int = 3600,
        max_tracked_ips: int = 50_000,
        ban_store=None,
    ):
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.ban_duration = ban_duration
        self.max_tracked_ips = max_tracked_ips
        self._ban_store = ban_store
        # ip -> list of violation timestamps
        self._violations: dict[str, list[float]] = defaultdict(list)
        # ip -> ban expiry timestamp
        self._bans: dict[str, float] = {}
        if self._ban_store is not None:
            self._bans.update(self._ban_store.load_bans())
            logger.info("Loaded %d persistent bans from store", len(self._bans))

    def record_violation(self, ip: str) -> bool:
        """Record a violation and return True if IP should be banned."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Enforce memory bounds before adding new IP
        if ip not in self._violations and len(self._violations) >= self.max_tracked_ips:
            self._evict_oldest()

        # Clean old violations for this IP
        violations = self._violations[ip]
        self._violations[ip] = [t for t in violations if t > cutoff]
        self._violations[ip].append(now)

        if len(self._violations[ip]) >= self.threshold:
            expires = now + self.ban_duration
            self._bans[ip] = expires
            if self._ban_store is not None:
                self._ban_store.add_ban(ip, expires)
            logger.warning(
                "Auto-banned IP %s: %d violations in %ds (ban duration: %ds)",
                ip,
                len(self._violations[ip]),
                self.window_seconds,
                self.ban_duration,
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
            if self._ban_store is not None:
                self._ban_store.remove_ban(ip)
            return False
        return True

    def ban_remaining(self, ip: str) -> int:
        """Get remaining ban time in seconds (0 if not banned)."""
        expires = self._bans.get(ip)
        if expires is None:
            return 0
        remaining = int(expires - time.time())
        return max(0, remaining)

    def cleanup(self) -> int:
        """Purge expired bans and old violations. Returns number of entries removed."""
        now = time.time()
        cutoff = now - self.window_seconds
        removed = 0

        # Purge expired bans
        expired_bans = [ip for ip, exp in self._bans.items() if now > exp]
        for ip in expired_bans:
            del self._bans[ip]
            self._violations.pop(ip, None)
            removed += 1

        # Purge IPs with no recent violations (and not banned)
        stale_ips = [
            ip
            for ip, ts_list in self._violations.items()
            if ip not in self._bans and all(t <= cutoff for t in ts_list)
        ]
        for ip in stale_ips:
            del self._violations[ip]
            removed += 1

        if self._ban_store is not None:
            removed += self._ban_store.cleanup_expired()

        return removed

    def close(self) -> None:
        """Close persistent ban store if any."""
        if self._ban_store is not None:
            self._ban_store.close()

    def _evict_oldest(self) -> None:
        """Evict the oldest violation entries to stay within max_tracked_ips."""
        # Find IPs with the oldest last-violation timestamp (skip banned IPs)
        candidates = []
        for ip, ts_list in self._violations.items():
            if ip not in self._bans and ts_list:
                candidates.append((ip, max(ts_list)))

        # Sort by most recent violation (ascending) — evict least recently active
        candidates.sort(key=lambda x: x[1])

        # Evict 10% or at least 1 to make room
        evict_count = max(1, len(candidates) // 10)
        for ip, _ in candidates[:evict_count]:
            del self._violations[ip]


class IPBanMiddleware(BaseHTTPMiddleware):
    """Reject requests from banned IPs."""

    def __init__(
        self,
        app,
        tracker: ViolationTracker,
        trusted_proxies: list[str] | None = None,
    ):
        super().__init__(app)
        self.tracker = tracker
        self.trusted_proxies = trusted_proxies or []

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = _extract_client_ip(request, self.trusted_proxies)

        if self.tracker.is_banned(client_ip):
            remaining = self.tracker.ban_remaining(client_ip)
            logger.warning("Rejected banned IP: %s (%ds remaining)", client_ip, remaining)
            response = _error_response(403, "Access denied", "access_denied")
            response.headers["Retry-After"] = str(remaining)
            return response

        return await call_next(request)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject requests from IPs not on the allowlist (if configured)."""

    def __init__(self, app, allowlist: list[str], trusted_proxies: list[str] | None = None):
        super().__init__(app)
        # Parse CIDR networks
        self.networks = [ipaddress.ip_network(cidr, strict=False) for cidr in allowlist]
        self.trusted_proxies = trusted_proxies or []

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.networks:
            # No allowlist configured — allow all
            return await call_next(request)

        client_ip = _extract_client_ip(request, self.trusted_proxies)

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

    _HEALTH_RPM = 300  # Generous separate limit for /health

    def __init__(self, app, rpm: int = 60, trusted_proxies: list[str] | None = None):
        super().__init__(app)
        from overblick.core.security.rate_limiter import RateLimiter

        # Token bucket: rpm tokens, refill at rpm/60 per second
        self.limiter = RateLimiter(max_tokens=float(rpm), refill_rate=rpm / 60.0)
        self._health_limiter = RateLimiter(
            max_tokens=float(self._HEALTH_RPM),
            refill_rate=self._HEALTH_RPM / 60.0,
        )
        self.rpm = rpm
        self.trusted_proxies = trusted_proxies or []

    async def dispatch(self, request: Request, call_next) -> Response:
        # Health checks use a separate, generous rate limit
        if request.url.path == "/health":
            if not self._health_limiter.allow("health"):
                return _error_response(429, "Rate limit exceeded", "rate_limit_error")
            return await call_next(request)

        if not self.limiter.allow("global"):
            retry_after = self.limiter.retry_after("global")
            client_ip = _extract_client_ip(request, self.trusted_proxies)
            logger.warning(
                "Global rate limit exceeded from %s",
                client_ip,
            )
            response = _error_response(429, "Rate limit exceeded", "rate_limit_error")
            response.headers["Retry-After"] = str(int(retry_after) + 1)
            response.headers["X-RateLimit-Limit"] = str(self.rpm)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rpm)
        return response
