# Verification Report — Auto Gate (2026-02-22)

- Script: `scripts/release_gate.sh`
- Log: `logs/release_gate_2026-02-22.log`
- Executed at: 2026-02-22T18:51:24+01:00

## Scope
1. Auth gate regression
2. Telegram extended command regression (/summary, /health, /restart confirm flow)

## Execution Log (excerpt)
```text
[release-gate] started: 2026-02-22T18:51:22+01:00
[release-gate] root: /home/blue236/.openclaw/workspace/binance_trading_bot

=== RELEASE GATE SUMMARY ===
[PASS] Auth unauth GET / redirects to /login :: status=302, location=/login
[PASS] Auth unauth GET /api/health returns 401 :: status=401
[PASS] Auth login success sets secure session cookie :: status=302
[PASS] Auth authenticated GET /api/health returns 200 :: status=200
[PASS] TG /summary response :: 📌 Summary
runtime_mode: normal
approval: ON
risk_pct: 0.5
max_pos: 2
cooldown_h: 8
open_positions: 1
equity: 1000.00 USD
[PASS] TG /health response :: 🩺 Health
loop_alive: yes
owner_configured: yes
pending_change: False
positions: 1
paused: False
[PASS] TG /restart non-owner denied :: Owner-only command.
[PASS] TG /restart owner issues confirm token :: Pending change: restart -> now
Reply /confirm 92ae34 to apply (expires in 119s) or /cancel to discard.
[PASS] TG /confirm restart executes restart path :: ♻️ Restarting bot process...
TOTAL: pass=9, fail=0
RESULT: PASS
[release-gate] python checks: PASS
```

## Gate Decision
- Result: **PASS**

## Remaining Risks
- HTTPS redirect/HSTS는 앱 외부 프록시 환경에서 별도 검증 필요
- 모킹 기반 Telegram 검증으로, 실 Telegram API 연동 E2E는 별도 수행 필요
