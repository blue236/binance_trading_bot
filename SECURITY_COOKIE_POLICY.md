# Security Cookie Policy (External Access)

## Scope
Web UI/API sessions when external IP access is enabled.

## Mandatory policy
- Session cookie must be `HttpOnly`.
- Session cookie must be `Secure` on HTTPS.
- Session cookie should use `SameSite=Lax` (or `Strict` for admin-only surfaces).
- Cookie must have explicit expiry (idle timeout 30m, absolute timeout 12h recommended).

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
