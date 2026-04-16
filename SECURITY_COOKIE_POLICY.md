# Security Cookie Policy (External Access)

## Scope
Web UI/API sessions when external IP access is enabled.

## Mandatory policy
- Session cookie must be `HttpOnly`.
- Session cookie must be `Secure` on HTTPS.
- Session cookie should use `SameSite=Lax` (or `Strict` for admin-only surfaces).
- Cookie must have explicit expiry (idle timeout 30m, absolute timeout 12h recommended).

## Session token format (current implementation)

Tokens are structured as `{username}:{timestamp}:{nonce}:{HMAC-SHA256}` where:

- `timestamp` is a Unix epoch integer (seconds).
- `nonce` is 8 random hex bytes (`secrets.token_hex(8)`).
- `HMAC-SHA256` signs `"{username}:{timestamp}:{nonce}:ok"` with `BTB_WEB_SESSION_SECRET`.

Token validation enforces:
- Cryptographic MAC verification (timing-safe `hmac.compare_digest`).
- Username match against the configured `BTB_WEB_USERNAME`.
- Age check: token rejected if older than `BTB_WEB_SESSION_TTL_HOURS` (default 8 h) or if timestamp is in the future.

This replaces the previous opaque random token which had no expiry or integrity protection.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BTB_WEB_SESSION_SECRET` | auto-generated | HMAC signing key for session tokens |
| `BTB_WEB_SESSION_TTL_HOURS` | `8` | Session lifetime in hours |
| `BTB_WEB_AUTH_ENABLED` | `1` | Set to `0` to disable auth entirely |
| `BTB_WEB_USERNAME` | `admin` | Login username |
| `BTB_WEB_PASSWORD` | — | **Required** when auth is enabled; startup fails if unset |

## HTTP vs HTTPS behavior
- HTTP: do not issue authenticated session cookies for production external access.
- HTTPS: only channel allowed for authenticated session cookie.
- Enforce `HTTP -> HTTPS` redirect at reverse proxy.

## Related hardening
- HSTS enabled (`max-age>=31536000; includeSubDomains` when domain is stable)
- CSRF protection enabled for state-changing POST/PUT/DELETE
- Login rate-limit / brute-force protection enabled
- Audit logs for login success/failure and privileged changes

## Verification checklist
- [ ] Cookie flags observed in browser devtools (`HttpOnly`, `Secure`, `SameSite`)
- [ ] No authenticated request possible via plain HTTP
- [ ] Redirect works for all routes
- [ ] Session expires by idle and absolute timeout policy
