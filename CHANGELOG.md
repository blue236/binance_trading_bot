# Changelog

All notable changes to this project are documented here.

## [Unreleased] — 2026-04-16

### Security

- **DEV-01**: Web UI server now fails at startup with a clear `RuntimeError` when `BTB_WEB_AUTH_ENABLED=1` and `BTB_WEB_PASSWORD` is not set. Previously accepted login with a blank password.
- **DEV-02**: Session tokens hardened from a static HMAC to `{username}:{timestamp}:{nonce}:{HMAC-SHA256}`. Tokens expire after `BTB_WEB_SESSION_TTL_HOURS` (default 8 h) and are validated with `hmac.compare_digest` to prevent timing attacks. Old-format tokens are rejected (one-time re-login required).
- **DEV-03**: `credentials.py` — if `.credentials.enc.json` exists but `BTB_CREDENTIALS_PASSPHRASE` is unset, startup now raises `RuntimeError` with an actionable message instead of silently starting with empty API keys.
- **DEV-04**: Config save in webapp now blanks API key and Telegram secret fields in memory **before** writing to disk, and writes via a temp file + `os.replace()` to eliminate the partial-write window.
- **REV-04 (PBKDF2)**: Raised PBKDF2-HMAC-SHA256 iteration count from 390,000 to 600,000 to match the OWASP 2024 Password Storage Cheat Sheet recommendation.
- **REV-04 (logger)**: Plaintext-credentials fallback warning now uses the root logger so it is guaranteed visible at startup before `setup_logger()` has run.

### Fixed

- **DEV-05**: `validate_config()` added to `main.py`. Raises `ValueError` if `risk.daily_loss_stop_pct` is negative (would silently disable the daily loss guard) or `risk.per_trade_risk_pct` is ≤ 0. Emits warnings for values above 10% and 5% respectively.
- **DEV-08**: `aggressive.loop_sleep_seconds` was at the wrong nesting level; deep-merge never reached `cfg["strategy"]`. Moved to `aggressive.strategy.loop_sleep_seconds` in `config.yaml`. Config reference updated.

### Added

- **TEST-01**: Five new edge-case tests for `h1_signals()` in `tests/test_hv5_strategy_adoption.py`:
  - `test_h1_signals_insufficient_bars_returns_none` — guard fires on 10-row DataFrame
  - `test_h1_signals_rsi_overheat_suppresses_t_long` — `rsi_overheat=60` blocks entry on strong uptrend
  - `test_h1_signals_regime_none_suppresses_entry` — `regime="none"` yields `signal=None`
  - `test_h1_signals_params_always_contain_atr_and_close` — verifies unconditional params key assignment
  - `test_h1_signals_pullback_suppressed_without_momentum` — flat prices, `atr_v==0`, no pullback entry
- **TEST-07**: Coverage baseline established at **35%** overall (3,610 statements). Top gaps: `backtester.py` 0%, `main.py` 17%, `webapp/backtest_service.py` 15%, `credentials.py` 24%. Full report in `coverage_report.txt`.
- **Agents**: Four project-local agents created in `~/.claude/agents/`:
  - `btb-developer.md` — senior developer persona with full H_V5 context
  - `btb-reviewer.md` — code reviewer with financial-safety priority hierarchy
  - `btb-tester.md` — QA engineer with pytest/OHLCV fixture patterns
  - `btb-quant.md` — quantitative strategist for parameter optimization
- **PROJECT_REVIEW_AND_TASKS.md**: Engineering review report with 25 tasks across 4 agents, severity-tagged findings (M-01→M-06, W-01→W-08, B-01→B-03, R-01→R-02), and 4-sprint execution plan.

### Reviewed

- **REV-02**: H_V5 signal chain audited for lookahead bias. **WARN (not BLOCK)** — no confirmed lookahead found in `regime_filter()`, `h1_signals()`, or the structural EMA100 exit. Two warm-up margin WARNs: `h1_signals()` uses a 300-bar fetch for EMA-200 (recommend 400); `regime_filter()` has no NaN guard before EMA comparison. See `reviews/REV-02_REV-04_report.md`.
- **REV-04**: Credential module reviewed. Two MEDIUM items identified and fixed in same session (logger and PBKDF2). No CRITICAL or BLOCK items. See `reviews/REV-02_REV-04_report.md`.

### Documentation

- `CLAUDE.md`: Added `BTB_WEB_SESSION_TTL_HOURS` env var, security requirements block, `validate_config()` design constraint, lookahead audit result, test suite section.
- `CONFIG_REFERENCE.md`: Fixed `aggressive.loop_sleep_seconds` → `aggressive.strategy.loop_sleep_seconds`; added `validate_config()` step to runtime load order.
- `SECURITY_COOKIE_POLICY.md`: Added session token format specification, HMAC signing details, and environment variable table.
- `QA_RELEASE_GATE_CHECKLIST.md`: Added 5 security baseline checks covering password guard, session expiry, passphrase requirement, config save masking, and config validation.
- `README.md`: Updated credential security section (passphrase now mandatory); added web UI authentication section with required env vars.
