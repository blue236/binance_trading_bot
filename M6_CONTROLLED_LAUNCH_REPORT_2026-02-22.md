# M6 Controlled Launch Report (2026-02-22)

- Script: `scripts/m6_controlled_launch_gate.sh`
- Log: `logs/m6_controlled_launch_2026-02-22.log`
- Executed at: 2026-02-22T19:24:11+01:00

## Scope
1. M5 readiness precondition
2. Launch policy + rollback policy validation
3. Operator command availability check
4. Incident-review report completeness

## Execution Log
```text
[m6-gate] started: 2026-02-22T19:24:09+01:00
[m6-gate] root: /home/blue236/.openclaw/workspace/binance_trading_bot
[m5-gate] started: 2026-02-22T19:24:09+01:00
[m5-gate] root: /home/blue236/.openclaw/workspace/binance_trading_bot
[release-gate] started: 2026-02-22T19:24:09+01:00
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
Reply /confirm 85d12d to apply (expires in 119s) or /cancel to discard.
[PASS] TG /confirm restart executes restart path :: ♻️ Restarting bot process...
TOTAL: pass=9, fail=0
RESULT: PASS
[release-gate] python checks: PASS
[release-gate] running optional risk gate (--with-risk)
[risk-gate] started: 2026-02-22T19:24:10+01:00
[risk-gate] root: /home/blue236/.openclaw/workspace/binance_trading_bot
[PASS] Mandatory pre-trade risk gate function exists :: fn_idx=41661
[PASS] Entry loop evaluates risk gate before entry :: entry_idx=48786, call_idx=48848
[PASS] Risk-gate reject audit event exists :: reject_idx=42553
[PASS] Owner-only command denies non-owner /setrisk :: Owner-only command.
[PASS] Non-owner /setrisk does not create pending change :: None
TOTAL: pass=5, fail=0
RESULT: PASS
[risk-gate] checks: PASS
[risk-gate] completed: PASS
[release-gate] completed: PASS
[PASS] artifact exists: M2_ARCH_CONVERGENCE_REPORT_2026-02-22.md
[PASS] artifact exists: M3_RISK_ENFORCEMENT_REPORT_2026-02-22.md
[PASS] artifact exists: M4_RELIABILITY_OBSERVABILITY_REPORT_2026-02-22.md
[PASS] artifact exists: STRATEGY_MARKER_BACKEND_REPORT_2026-02-22.md
[PASS] artifact exists: RISK_GATE_VERIFY_REPORT_2026-02-22.md
[PASS] artifact exists: NETWORK_RELIABILITY_REPORT_2026-02-22.md
[INFO] bot.log loop error count: 1
[PASS] loop error count within readiness threshold (<=5)
[PASS] unified backtest endpoint present
[PASS] system health endpoint present
[m5-gate] completed checks
[m5-gate] final: PASS
[PASS] launch policy exists
[PASS] operator command present: /pause
[PASS] operator command present: /resume
[PASS] operator command present: /stop
[PASS] operator command present: /start
[PASS] operator command present: /restart
[PASS] report exists: M3_RISK_ENFORCEMENT_REPORT_2026-02-22.md
[PASS] report exists: M4_RELIABILITY_OBSERVABILITY_REPORT_2026-02-22.md
[PASS] report exists: RISK_GATE_VERIFY_REPORT_2026-02-22.md
[PASS] report exists: NETWORK_RELIABILITY_REPORT_2026-02-22.md
[PASS] report exists: M5_TRADING_READINESS_REPORT_2026-02-22.md
[PASS] rollback policy references gate re-validation
[m6-gate] completed checks
```

## Go/No-Go
- Result: **PASS**
- Launch recommendation: **GO (controlled ramp only, policy-bound)**
