# Binance Trading Bot — One-Page Team Roadmap

## Vision (6 weeks)
Ship a secure, testable, production-ready trading platform with:
1. Unified web control plane
2. Hard risk gates and auditability
3. Reliable execution + observability
4. Staged go-live (paper → shadow → limited live)

## Team & ownership
- **Team Lead (Steve Jobs):** priority, scope, release decisions
- **Software Architect:** architecture, risk policy, technical sign-off
- **Software Developer:** implementation, refactoring, CI integration
- **Tester (existing sub-agent):** release-gate QA, regression, failure simulation

## Milestones & exit criteria

### M1 — Foundation Hardening (Week 1)
**Deliverables**
- Deterministic API error handling (user errors no longer 500)
- Validation layer for config/backtest/domain constraints
- Security UX consistency for encrypted credentials
- CI baseline: smoke + negative tests
- Runbook v1 (startup, secrets, rollback)

**Exit criteria**
- P0 tests green
- No critical/high security defects

### M2 — Architecture Convergence (Week 2)
**Deliverables**
- Shared interfaces for Strategy / Risk / Execution / Data
- Legacy flow wrapped behind service boundaries
- Config schema versioning and migration checks

**Exit criteria**
- No duplicated critical business logic
- Backward compatibility verified

### M3 — Risk Engine Enforcement (Week 3)
**Deliverables**
- Mandatory pre-trade hard checks (exposure, drawdown, cooldown)
- Kill switch + safe-mode behavior
- Full audit chain (signal → risk decision → order result)

**Exit criteria**
- Forced-trigger risk tests pass
- No risk bypass path

### M4 — Reliability & Observability (Week 4)
**Deliverables**
- Structured logging and correlation IDs
- Health checks with dependency coverage
- Retry/reconnect/idempotency hardening
- Alert rules (latency, rejects, drift, stale feed)

**Exit criteria**
- Recovery scenarios pass
- Alert quality validated

### M5 — Trading Readiness (Week 5)
**Deliverables**
- Extended paper trading run
- Shadow run (decisioning only)
- Regression/performance report

**Exit criteria**
- Stable paper run window completed
- Reconciliation drift within threshold

### M6 — Controlled Launch (Week 6)
**Deliverables**
- Limited-capital deployment
- Incremental capital ramp policy
- Incident response and rollback drill

**Exit criteria**
- Zero unresolved blocker defects
- Go/No-Go signed by Architect + Tester + Team Lead

## New requirement (added)
- **Telegram approval workflow for live orders**
  - When bot detects BUY/SELL action, send approval request to Telegram
  - Execute only after explicit APPROVE command within timeout
  - Default deny on timeout
  - Log approval decision in trade events

## Weekly operating rhythm
- Daily 15-min standup
- Architect design review 2x/week
- End-of-week tester release-gate review
- Merge policy: small PRs, CI green, checklist attached

## KPI snapshot
- Reliability: API error rate, reject rate, reconciliation drift
- Safety: risk trigger correctness, secret leakage = 0
- Delivery: PR lead time, regression pass rate
- Readiness: paper trading stability, drawdown compliance
