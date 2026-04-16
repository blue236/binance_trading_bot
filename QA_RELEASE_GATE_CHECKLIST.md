# QA Release Gate Checklist

## Telegram approval regression
- [ ] BUY signal -> approval request sent
- [ ] APPROVE token -> order executed
- [ ] DENY token -> order blocked
- [ ] timeout -> default deny
- [ ] approval events logged in trades + audit

## Telegram runtime command regression
- [ ] /setrisk range validation
- [ ] /setmaxpos range validation
- [ ] /setcooldown range validation
- [ ] /mode safe|normal|aggressive validation
- [ ] confirm token required and expires
- [ ] non-requester cannot confirm/cancel
- [ ] audit log: issued/applied/denied/expired

## Web UI/mobile regression
- [ ] tabs/buttons/log panels usable on <=768px
- [ ] config edit/save works on mobile viewport
- [ ] log download works on mobile/desktop

## Security baseline regression
- [ ] unauthenticated routes blocked (target config)
- [ ] cookie policy documented and applied
- [ ] HTTP->HTTPS redirect verified
- [ ] `BTB_WEB_PASSWORD` is set before starting the web UI; server must refuse to start if `BTB_WEB_AUTH_ENABLED=1` and password is unset
- [ ] Session tokens contain timestamp+nonce+HMAC; plain random tokens no longer accepted
- [ ] Session expires after `BTB_WEB_SESSION_TTL_HOURS` (verify by advancing system clock or lowering TTL to 1 h and confirming re-login is required)
- [ ] `BTB_CREDENTIALS_PASSPHRASE` is set when `.credentials.enc.json` exists; bot must fail loudly rather than starting with empty credentials
- [ ] Config save does not write API keys to disk (blank them in the saved YAML and confirm with `cat config.yaml | grep api_key`)
- [ ] `validate_config()` rejects negative `daily_loss_stop_pct` and non-positive `per_trade_risk_pct` at startup
