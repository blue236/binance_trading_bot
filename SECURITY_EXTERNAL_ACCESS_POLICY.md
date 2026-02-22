# External Access Security Baseline (BTB)

## Scope
- Web UI external IP access policy
- Mandatory authentication requirement
- Cookie/session transport policy for HTTP/HTTPS

## Mandatory rules
1. Anonymous access is forbidden for all `/api/*` and operational UI pages.
2. Login is required before any trading-control action.
3. External exposure is allowed only over HTTPS.

## Route policy checklist
- [ ] `/login` only public route
- [ ] `/` requires authenticated session
- [ ] `/api/*` requires auth (401/403 on failure)
- [ ] admin routes require RBAC admin role

## Cookie/session policy
- Session cookie: `HttpOnly=true`, `Secure=true`, `SameSite=Lax` (or Strict for admin-only)
- Session TTL: idle timeout 30 min, absolute timeout 12 h
- CSRF token required for state-changing endpoints (POST/PUT/DELETE)

## HTTP vs HTTPS behavior
- HTTP requests must be redirected to HTTPS (301/308)
- No session cookie issuance over HTTP
- HSTS enabled (`max-age>=31536000`, includeSubDomains)

## Brute-force protection
- Login endpoint rate-limit by IP and account
- Temporary lock after repeated failures (e.g., 5 failures / 15 min)
- Audit logging for auth failures

## Audit requirements
Log all events with timestamp, user, IP, action, result:
- login success/fail
- logout
- permission denied
- config/risk changes
- restart/stop/start control actions

## Verification (DoD)
- [ ] Unauthenticated API/UI access blocked
- [ ] Cookie flags confirmed in browser devtools
- [ ] HTTP->HTTPS redirect verified
- [ ] CSRF test case passes
- [ ] Rate-limit and lockout test passes
- [ ] Audit log evidence captured
