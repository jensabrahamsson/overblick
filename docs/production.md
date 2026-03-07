# Production Hardening Guide

This document provides guidelines for deploying Överblick in a secure production environment.

## 1. Operating System
**Recommendation: Linux (Ubuntu 22.04+ or Debian 12+)**
- Use a dedicated user account for Överblick (e.g., `overblick`).
- Never run as `root`.
- Ensure POSIX permissions are enforced on the `data/` and `config/` directories (`chmod 700`).

## 2. Network Security
- **Dashboard:** Always bind to `127.0.0.1` (default). NEVER expose the dashboard port (default 5005) directly to the internet.
- **Reverse Proxy:** Use Nginx or Caddy to proxy requests to the dashboard.
- **HTTPS:** Termination should happen at the reverse proxy level with a valid TLS certificate (e.g., Let's Encrypt).
- **Firewall:** Close all ports except SSH (22) and HTTPS (443).

### Example Nginx Configuration
```nginx
server {
    listen 443 ssl;
    server_name overblick.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5005;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Enable WebSocket support for htmx/streaming (if used)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 3. Application Security
- **Safe Mode:** Ensure `OVERBLICK_SAFE_MODE=1` is set in your environment.
- **Cryptography:** The `cryptography` library is MANDATORY in production. The system will refuse to start without it if Safe Mode is enabled.
- **Secrets Management:** Use the system keyring (macOS/Linux) or ensure `.master_key` is stored in a highly restricted directory.
- **Dashboard Auth:** Always enable dashboard authentication in `config/overblick.yaml`.

## 4. Maintenance & Monitoring
- **Logs:** Monitor `logs/supervisor.log` for process crashes.
- **Audit Trail:** Regularly review the audit log via the Dashboard to identify any unusual agent behavior or security deflections.
- **Retention:** The system automatically trims audit logs and engagement data after 90 days. Ensure you have enough disk space for this period.
- **Backups:** Regularly back up the `data/` directory, which contains SQLite databases for all identities.

## 5. Security Fail-Closed Design
Överblick is designed to **fail closed**. If a security component (e.g., rate limiter, preflight check, output safety) is misconfigured or unavailable, the LLM pipeline will raise a `ConfigError` and block the request. Do not attempt to bypass these errors by disabling Safe Mode in production.
