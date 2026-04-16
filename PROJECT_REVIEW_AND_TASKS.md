# Project Review & Agent Task Board
**Date:** 2026-04-16  
**Branch:** fix/web-ui-issues  
**Reviewer:** Engineering Review (all agents)  
**Status:** Dry-run only — not cleared for live trading

---

## Executive Summary

The binance_trading_bot is a structurally sound Python trading system with two operating modes: a monolithic CLI bot (`main.py`) and a FastAPI web UI (`webapp/`). The H_V5 Breakeven + EMA100 strategy is the production strategy. The codebase is suitable for continued **dry-run** development but requires significant hardening before real-money deployment.

**Overall Grade: B−** | **Risk Level: MEDIUM-HIGH** for live trading

| Category | Grade | Risk |
|---|---|---|
| Code Quality (main.py) | B− | MEDIUM |
| Code Quality (webapp) | B | MEDIUM |
| Code Quality (backtester) | B− | LOW-MEDIUM |
| Test Coverage | C+ | MEDIUM |
| Config Consistency | C | MEDIUM-HIGH |
| Strategy Correctness | B | MEDIUM |
| Security | C+ | MEDIUM-HIGH |
| Architecture | C+ | MEDIUM |

---

## Part 1 — Engineering Review Findings

### 1.1 Code Quality

#### `main.py` (1489 lines)

**Strengths:**
- Explicit lookahead protection in structural exit logic (line 1388–1390 drops current forming candle before EMA comparison)
- Comprehensive network resilience with exponential backoff (`call_with_retry`, `safe_fetch_balance`)
- State machine pattern for position tracking and cooldown enforcement
- Audit logging on all trade events and risk gate rejections

**Issues Found:**

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| M-01 | HIGH | credentials.py:118 | Silent fallback to empty credentials if encrypted store exists but passphrase is missing. Bot starts without keys, no error raised. |
| M-02 | HIGH | main.py:1138 | `daily_pnl_guard()` uses `daily_loss_stop_pct` with no sign check. If config has positive value instead of negative, guard never triggers. |
| M-03 | MEDIUM | main.py:1384 | Structural exit fetches 220 bars but doesn't explicitly verify current candle is closed. Incomplete bar included in EMA calculation before the intended drop. |
| M-04 | MEDIUM | main.py:201 | `NETWORK_HEALTH` is a global mutable dict accessed by multiple functions without locking. Thread-unsafe if exchange calls are ever parallelized. |
| M-05 | MEDIUM | main.py:519 | Pending telegram command token expires after 120s but state update is not atomic. Race condition possible if bot restarts during confirmation window. |
| M-06 | LOW | main.py:978 | `fetch_equity_usdt()` silently drops assets whose ticker fetch fails. Equity underestimated without warning when major asset tickers are unavailable. |

#### `webapp/app.py` (1481 lines)

**Strengths:**
- Clean `AuthMiddleware` class with clear pass/fail logic
- Idempotent AI bot start/stop with PID file state checks
- Secrets masking in API responses before sending to browser
- Base64 fallback for large config payloads

**Issues Found:**

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| W-01 | CRITICAL | app.py:75 | Session secret defaults to literal string `"change-me"`. If operator forgets to set `BTB_WEB_SESSION_SECRET`, all sessions are forgeable. |
| W-02 | CRITICAL | app.py:887 | Server starts and accepts empty-string password if `BTB_WEB_PASSWORD` env var is not set. Login succeeds with blank password. |
| W-03 | HIGH | app.py:96–115 | No CSRF protection on any state-changing POST route. Cross-site request forgery possible for any logged-in session. |
| W-04 | HIGH | app.py:885 | Password passed via HTTP header `x-btb-pass` in plaintext. Interceptable on non-HTTPS connections. |
| W-05 | HIGH | app.py:267 | Config save blanks secrets *after* writing to disk. Brief window where secrets persist in `config.yaml` in plaintext. Should blank before write or use atomic temp-file swap. |
| W-06 | MEDIUM | app.py:161 | `_wait_ai_stopped()` has a 6s hard timeout. If process hangs in cleanup, function returns False but caller proceeds as if bot stopped. |
| W-07 | MEDIUM | app.py:1188 | Partial chart refresh (some symbols succeed, others time out) leaves SQLite in inconsistent state with no rollback. |
| W-08 | LOW | app.py:841 | Telegram poll job runs every 5s with no rate limit on command execution. Rapid `/restart` commands can cause rapid bot restarts. |

#### `backtester.py` (1021 lines)

**Issues Found:**

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| B-01 | HIGH | backtester.py:203 | ML strategy labels use `df["close"].shift(-holding_period_bars)` — explicit lookahead. Model trained on future data; live performance will degrade significantly. |
| B-02 | MEDIUM | backtester.py:394–416 | Position sizing calculates entry with slippage, then applies slippage again on exit. Round-trip costs double-counted. |
| B-03 | LOW | backtester.py:48 | Bars with >171% move removed as errors. On crypto, legitimate flash-crashes followed by recovery are removed, distorting volatility profile. |

#### `scripts/btb_strategy_research.py` (465 lines)

**Strengths:**
- Anti-overfitting scoring (`0.45×val + 0.55×test`) — well-designed
- Multi-symbol evaluation with temporal train/val/test splits
- Explicit `trade_penalty` for sparse OOS trades

**Issues Found:**

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| R-01 | HIGH | research.py | H_V5 production strategy (`h1_signals` + structural exit) is NOT implemented in the research tool. Cannot backtest the actual live strategy. |
| R-02 | MEDIUM | research.py | Research strategies use different parameter names than `config.yaml` (e.g. `trail_atr_mult` vs `atr_trail_mult`). Mapping is manual and error-prone. |

---

### 1.2 Test Coverage

**Current state: ~40% of critical paths covered. No integration tests.**

| Test File | Lines | Coverage Quality |
|-----------|-------|-----------------|
| test_hv5_strategy_adoption.py | 76 | FAIR — breakout and pullback signal cases present, regime checks present, but edge cases missing |
| test_hv5_config_defaults.py | 31 | WEAK — validates config shape only, no range checks |
| test_ai_start_stop_idempotency.py | 113 | FAIR — idempotency tested, race conditions not |
| test_ai_config_reload.py | 80 | WEAK — mocks filesystem, doesn't test timing |
| test_telegram_dispatch.py | 89 | FAIR — command parsing tested, not state mutations |
| test_telegram_summary.py | 98 | FAIR — message format only |
| test_backtest_unified_reliability.py | 71 | WEAK — return code testing only, not backtest accuracy |
| test_chart_api_features.py | 50 | WEAK — endpoint responses, not data freshness |
| test_logging_and_ai_log_stream.py | 84 | WEAK — file creation, not streaming behavior |

**Untested critical paths:**
- `h1_signals()` with insufficient data (< warm-up bars)
- `regime_filter()` with all three modes
- `position_size()` with extreme ATR/risk values
- `daily_pnl_guard()` activation and recovery
- Credential loading fallback chain (encrypted → env → empty)
- Full trade lifecycle: entry → trailing stop update → exit
- State persistence across restarts
- Config `deep_merge` correctness
- Approval timeout and user mismatch handling

---

### 1.3 Config Consistency

| Issue | Severity | Detail |
|-------|----------|--------|
| `min_notional_usdt` vs `min_notional_usdc` | HIGH | Template uses `usdt`, runtime expects `usdc`. Regenerating from template breaks the bot. |
| `CONFIG_REFERENCE.md` stale | MEDIUM | Documents `ema_slow: 1` (an old test value). Actual config has `ema_slow: 200`. |
| Aggressive mode constraint undocumented | MEDIUM | `aggressive` overrides only apply when both `dry_run: true` AND `aggressive_mode: true`. Not stated in the template or docs. |
| `aggressive.loop_sleep_seconds` misplaced | LOW | Currently at `aggressive.loop_sleep_seconds` but runtime reads `strategy.loop_sleep_seconds`. This override is silently ignored. |
| `strategy.mode` values undocumented | LOW | Three valid modes exist but not listed anywhere in docs. |

---

### 1.4 Architecture Concerns

| Concern | Impact |
|---------|--------|
| **Backtester disconnected from live strategy** — `backtester.py` and `scripts/btb_strategy_research.py` implement their own signal logic that doesn't match `h1_signals()` in `main.py`. Backtest results cannot be trusted to reflect live performance. | HIGH |
| **No shared strategy module** — Signal logic is duplicated between research tools and the bot. Bug fixes in one don't propagate to the other. | HIGH |
| **Dual config write paths** — `webapp/app.py` edits `config.yaml` while `main.py` reads it at startup. `state.json` is shared; no file locking. | MEDIUM |
| **Global mutable `NETWORK_HEALTH`** — Thread-unsafe dict mutated by multiple functions without locks. | MEDIUM |
| **EMA warm-up risk** — `regime_filter()` fetches 400 bars for an EMA-200. Only 200 bars of true EMA history available; first 199 values biased. | MEDIUM |

---

## Part 2 — Agent Task Board

Tasks are ordered by priority within each agent's domain. Each task has an ID, agent assignment, priority, and acceptance criteria.

---

### AGENT: `btb-developer`

#### TASK DEV-01 — Fix empty-password server startup
**Priority:** CRITICAL  
**Files:** `webapp/app.py`

The server starts and accepts login with a blank password if `BTB_WEB_PASSWORD` is not set in the environment. This is a security hole in any network-accessible deployment.

**What to do:**
- In the startup lifecycle (app startup event or `_auth_pass()` guard), check if auth is enabled and `BTB_WEB_PASSWORD` is empty or unset.
- If so, raise a `RuntimeError` or call `sys.exit(1)` with a clear message: `"BTB_WEB_PASSWORD must be set when auth is enabled. Set it in .env or as an environment variable."`
- Do not disable auth silently; force the operator to make an explicit choice.

**Acceptance criteria:**
- Starting the server without `BTB_WEB_PASSWORD` prints a clear fatal error and exits.
- Starting with a non-empty password works as before.
- Existing test `test_ai_start_stop_idempotency.py` still passes.

---

#### TASK DEV-02 — Harden session token (add expiry + nonce)
**Priority:** HIGH  
**Files:** `webapp/app.py`

Current session token is a deterministic HMAC of `username:ok`. No expiry, no nonce. An attacker who obtains the session secret can mint valid tokens indefinitely.

**What to do:**
- Add a timestamp (Unix epoch, seconds) and a random 8-byte nonce to the token payload: `f"{username}:{ts}:{nonce}:ok"`.
- On validation, check that the token timestamp is within the session TTL (default 8 hours; make it configurable via `BTB_WEB_SESSION_TTL_HOURS`).
- If expired, redirect to `/login` with an "Session expired" message.
- Keep backward compat: if existing cookie has old format, treat it as invalid (re-login required — this is a one-time migration cost).

**Acceptance criteria:**
- Tokens older than the TTL are rejected.
- Forged tokens without a valid HMAC are rejected.
- Normal login → browse → logout flow is unaffected.

---

#### TASK DEV-03 — Fix credential silent fallback
**Priority:** HIGH  
**Files:** `credentials.py`

If `.credentials.enc.json` exists but `BTB_CREDENTIALS_PASSPHRASE` is not set, the code falls through silently and returns empty credentials. The bot starts with no API keys and no warning.

**What to do:**
- In `load_credentials()`, after detecting the encrypted file exists, check if passphrase is available.
- If encrypted file exists and passphrase is missing, raise `RuntimeError("BTB_CREDENTIALS_PASSPHRASE is required to decrypt .credentials.enc.json")`.
- Add a log warning (not silent) when falling back from encrypted file to env vars.
- Add a log warning when loading from legacy plaintext `credentials.json`.

**Acceptance criteria:**
- `python -c "from credentials import load_or_prompt_credentials; load_or_prompt_credentials()"` with encrypted file + missing passphrase raises `RuntimeError` with a clear message.
- Normal encrypted flow with correct passphrase still works.

---

#### TASK DEV-04 — Fix config persistence race in webapp (atomic save)
**Priority:** HIGH  
**Files:** `webapp/app.py`

`_save_ai_config_text()` writes config to disk, then blanks secrets. There is a brief window where secrets exist in plaintext on disk. Additionally, the `main.py` subprocess could read the config during this window.

**What to do:**
- Blank/mask secrets in the in-memory string **before** writing to disk.
- Write to a temp file (`config.yaml.tmp`), then use `os.replace()` for atomic swap.
- Ensure the `_AI_RELOAD_LOCK` is held for the full duration of write + subprocess restart signal.

**Acceptance criteria:**
- Secrets never appear in the final `config.yaml` on disk.
- If the write fails mid-way, the original `config.yaml` is preserved (no partial file).

---

#### TASK DEV-05 — Fix `daily_pnl_guard()` sign validation
**Priority:** HIGH  
**Files:** `main.py`

`daily_loss_stop_pct` is expected to be a positive number (e.g., `2.0` means 2% daily loss stop). The guard checks `daily_pnl <= -abs(daily_loss_stop_pct)`. If the config has a negative value (e.g., `-2.0`), the double-negative makes the condition always false — the guard never triggers.

**What to do:**
- Add input validation at config load time: assert `risk.daily_loss_stop_pct > 0`.
- Log a warning if the value is unusually high (> 10.0) or unusually low (< 0.5).
- Add the same validation for `per_trade_risk_pct` (must be > 0 and < 100).

**Acceptance criteria:**
- Starting with `daily_loss_stop_pct: -2.0` raises a `ValueError` or logs a critical warning and uses the absolute value.
- Existing risk guard tests pass.

---

#### TASK DEV-06 — Add CSRF token protection to state-changing POST routes
**Priority:** MEDIUM  
**Files:** `webapp/app.py`, `webapp/templates/`

No CSRF protection on POST endpoints. A logged-in user visiting a malicious page could have their session hijacked to send bot commands.

**What to do:**
- Generate a CSRF token per session (derived from session token + a server-side secret, or stored server-side).
- Inject the CSRF token into all HTML forms and the JavaScript fetch calls via a `<meta name="csrf-token">` tag.
- Add CSRF validation middleware that rejects POST/PUT/DELETE requests without a valid token from the same origin.
- Skip CSRF for explicitly public read-only GET endpoints.

**Acceptance criteria:**
- POST to `/api/start_ai_bot` without CSRF token returns 403.
- POST with valid token works normally.
- All existing JS fetch calls include the token.

---

#### TASK DEV-07 — Fix chart partial-refresh inconsistency
**Priority:** MEDIUM  
**Files:** `webapp/app.py`, `webapp/chart_service.py`

When chart refresh runs for multiple symbols and one times out, the SQLite DB ends up partially updated. The last-refresh timestamp in `meta` is not updated but the `ohlcv` table may have new rows for some symbols.

**What to do:**
- Wrap each symbol's refresh in a SQLite transaction.
- Only update `meta` last-refresh timestamp for a symbol after its OHLCV rows are committed.
- Log a clear warning for each symbol that timed out or failed.
- Return a partial-success response to the UI showing which symbols succeeded and which failed.

**Acceptance criteria:**
- If ETH refresh times out but BTC succeeds, BTC meta timestamp updates and ETH meta timestamp does not.
- UI shows "BTC: OK, ETH: timeout" in refresh response.

---

#### TASK DEV-08 — Misplaced aggressive override key: `loop_sleep_seconds`
**Priority:** LOW  
**Files:** `config.yaml`, `CONFIG_REFERENCE.md`

`aggressive.loop_sleep_seconds` is at the wrong nesting level. Runtime reads `cfg["strategy"]["loop_sleep_seconds"]`, so this override is silently ignored.

**What to do:**
- Move the key in `config.yaml` to `aggressive.strategy.loop_sleep_seconds`.
- Update `CONFIG_REFERENCE.md` to document this fix.
- Add a startup warning if `aggressive.loop_sleep_seconds` is present at the old location (backwards compat detection).

**Acceptance criteria:**
- With `aggressive_mode: true` and `dry_run: true`, the bot's loop sleep uses the aggressive value.
- `CONFIG_REFERENCE.md` documents the correct path.

---

### AGENT: `btb-reviewer`

#### TASK REV-01 — Full security audit of auth + session system
**Priority:** CRITICAL  
**Files:** `webapp/app.py` lines 62–115, 875–900

After DEV-01 and DEV-02 are implemented, perform a complete security review of the authentication and session system.

**What to review:**
- Session token generation, validation, and expiry (DEV-02 output)
- Login endpoint: brute force resistance, timing attacks, error messages leaking username validity
- CSRF token implementation (DEV-06 output)
- Cookie flags: `HttpOnly`, `Secure`, `SameSite`
- Header-based password (`x-btb-pass`) — does it still appear after the fix?
- Auth middleware bypass paths: any routes that skip authentication unintentionally

**Expected output:** Review report with CRITICAL/HIGH/MEDIUM/LOW findings and sign-off or block.

---

#### TASK REV-02 — Review H_V5 signal logic for lookahead bias
**Priority:** HIGH  
**Files:** `main.py` lines 788–957

Perform a focused lookahead audit of the complete signal chain.

**What to review:**
- `regime_filter()` for all three modes: Does any computation reference future data?
- `h1_signals()` Donchian comparison uses `.iloc[-2]` (correct), but verify all other indicator references
- Structural exit in `run_trading_loop()` (lines ~1380–1400): Dropped-current-bar logic — is the drop actually happening before EMA calculation, or does it depend on fetch call order?
- EMA warm-up: is `fetch_ohlc(limit=220)` sufficient warm-up for `ema_slow=200`? Check for NaN propagation.

**Red flags to look for:**
- Any `iloc[-1]` after a `.shift(-N)` operation
- Any `rolling()` window that includes the current (potentially incomplete) bar
- Any label or target variable computed from future prices

**Expected output:** Lookahead audit report with PASS/FAIL for each function.

---

#### TASK REV-03 — Review backtester ML lookahead in `backtester.py`
**Priority:** HIGH  
**Files:** `backtester.py` lines 200–323

B-01 identified that `label_future_returns()` uses `df["close"].shift(-holding_period_bars)` — explicit forward-looking labels fed into an XGBoost classifier. The model trains on information it cannot have at prediction time.

**What to review:**
- Confirm the lookahead in labels (line 203)
- Check `build_ml_features()` for any forward-looking features (MACD, rolling means)
- Verify the train/test split doesn't leak test labels into training
- Check whether `data.dropna()` (line 242) removes future-NaN rows correctly or shifts the effective training window

**Expected output:** Confirm or deny lookahead; propose fix approach (expanding window cross-validation, removing `shift(-N)` labels).

---

#### TASK REV-04 — Review credential loading fallback chain
**Priority:** HIGH  
**Files:** `credentials.py`

After DEV-03 is implemented, review the complete credential resolution flow.

**What to review:**
- Order of precedence: encrypted file → env vars → plaintext legacy file
- Silent vs loud failure at each step
- File permissions check: does code verify `.credentials.enc.json` is chmod 600?
- PBKDF2 iteration count (currently 390000): adequate for 2026 standards?
- Memory: credentials stored in plain Python dict after load — no zeroing

**Expected output:** Security review of credentials module with sign-off or block.

---

#### TASK REV-05 — Review position sizing and risk math
**Priority:** MEDIUM  
**Files:** `main.py` — `position_size()`, `order_constraints_ok()`, `daily_pnl_guard()`

Review the risk management math for correctness.

**What to review:**
- `position_size()`: verify the formula `risk_amount / (close - sl)` is correct; check for division-by-zero if `close == sl`
- Does position size validate that resulting order value ≤ available free balance?
- `daily_pnl_guard()` sign convention (DEV-05 context)
- Trailing stop update: does it ever move stop *down* (widening risk) instead of only up?
- Breakeven move: is the breakeven price calculation correct for both long and short directions?

**Expected output:** Math correctness review with PASS/FAIL per function.

---

### AGENT: `btb-tester`

#### TASK TEST-01 — Add edge-case tests for `h1_signals()`
**Priority:** HIGH  
**Files:** `tests/test_hv5_strategy_adoption.py`

The existing tests cover the happy path (breakout triggers T_LONG, wrong regime suppresses). Critical edge cases are missing.

**Tests to add:**

```python
# Insufficient data (below warm-up threshold)
def test_h1_signals_insufficient_bars_returns_none(self):
    # DataFrame with only 10 bars — all indicators return NaN
    # Expected: signal=None, params={}

# NaN in ATR (first bars before warm-up)
def test_h1_signals_nan_atr_returns_none(self):
    # ATR NaN on last bar
    # Expected: signal=None, no KeyError

# RSI overheat suppresses breakout
def test_h1_signals_rsi_overheat_suppresses_t_long(self):
    # Price breaks out but RSI=82 (> rsi_overheat=75)
    # Expected: signal=None

# Pullback entry (not breakout)  
def test_h1_signals_pullback_triggers_t_long(self):
    # Price near pullback EMA, RSI in 40-70 range, momentum ok
    # Expected: signal="T_LONG", params["entry_style"]="PULLBACK"

# Regime=none suppresses all signals
def test_h1_signals_regime_none_suppresses_entry(self):
    # Valid breakout conditions but regime="none"
    # Expected: signal=None
```

**Acceptance criteria:** All 5 new tests pass. Zero `KeyError` or `AttributeError` in any edge case path.

---

#### TASK TEST-02 — Add tests for `regime_filter()`
**Priority:** HIGH  
**Files:** New file `tests/test_regime_filter.py`

`regime_filter()` is completely untested. It determines whether entries are allowed at all.

**Tests to add:**

```python
# V5 mode: trending conditions return "trend"
def test_regime_v5_trending_returns_trend(self):
    # close > EMA200 > EMA50, slope positive, RSI >= 55
    # Expected: ("trend", nan)

# V5 mode: RSI below threshold returns "none"
def test_regime_v5_low_rsi_returns_none(self):
    # close > EMA200, slope positive, RSI=45 (< regime_rsi_min=55)
    # Expected: ("none", nan)

# V5 mode: price below EMA200 returns "none"
def test_regime_v5_below_ema200_returns_none(self):
    # Expected: ("none", nan)

# Legacy mode: ADX above threshold with positive slope returns "trend"
def test_regime_legacy_high_adx_returns_trend(self):
    # ADX=25 > threshold=20, positive EMA200 slope
    # Expected: ("trend", adx_value)

# Legacy mode: ADX below threshold returns "range"
def test_regime_legacy_low_adx_returns_range(self):
    # ADX=15 <= threshold=20
    # Expected: ("range", adx_value)
```

All tests mock `fetch_ohlc()` to avoid exchange calls.

**Acceptance criteria:** All tests pass. `regime_filter()` function behavior fully documented through tests.

---

#### TASK TEST-03 — Add tests for config validation and `deep_merge()`
**Priority:** HIGH  
**Files:** `tests/test_hv5_config_defaults.py` (extend) or new `tests/test_config_validation.py`

**Tests to add:**

```python
# deep_merge correctness
def test_deep_merge_nested_override(self):
    base = {"risk": {"per_trade": 0.5, "daily": 2.0}}
    override = {"risk": {"per_trade": 0.9}}
    result = deep_merge(base, override)
    assert result["risk"]["per_trade"] == 0.9
    assert result["risk"]["daily"] == 2.0  # untouched

# aggressive_mode only applies with dry_run
def test_aggressive_overrides_require_dry_run(self):
    cfg = {"general": {"dry_run": False, "aggressive_mode": True}, "aggressive": {"risk": {"per_trade_risk_pct": 1.5}}}
    result = apply_aggressive_overrides(cfg)
    # aggressive section NOT merged because dry_run=False
    assert result.get("risk", {}).get("per_trade_risk_pct") != 1.5

# Validate per_trade_risk_pct is positive
def test_negative_daily_loss_stop_pct_handled(self):
    # Config with daily_loss_stop_pct: -2.0 should raise or coerce to abs value

# Missing required strategy key raises KeyError (not silent)
def test_h1_signals_missing_strategy_key_raises(self):
    cfg = self._cfg()
    del cfg["strategy"]["atr_len"]
    with self.assertRaises(KeyError):
        bot.h1_signals(df, cfg, regime="trend")
```

**Acceptance criteria:** All tests pass. Config edge cases documented through tests.

---

#### TASK TEST-04 — Add integration test for full trade lifecycle
**Priority:** HIGH  
**Files:** New file `tests/test_trade_lifecycle.py`

No test covers the full path: regime check → signal → entry order → trailing stop updates → exit.

**What to test:**
- Mock exchange with a price series that triggers T_LONG entry
- Verify state.json shows open position after entry
- Advance price series to trigger trailing stop hit
- Verify state.json shows closed position after exit
- Verify CSV trade log entry written
- Verify equity updated correctly

Use `unittest.mock.patch` for all exchange calls and file I/O. Run against a temp state.json.

**Acceptance criteria:** Trade lifecycle test runs in < 5 seconds (all I/O mocked). Position opens and closes correctly. State file reflects the trade.

---

#### TASK TEST-05 — Add tests for credential loading fallback chain
**Priority:** HIGH  
**Files:** New file `tests/test_credentials.py`

**Tests to add:**

```python
# Env var override takes precedence over file
def test_env_var_overrides_file_credentials(self):
    # Set BINANCE_API_KEY in env; file has different value
    # Expected: env var value used

# Encrypted file without passphrase raises (after DEV-03)
def test_encrypted_file_without_passphrase_raises(self):
    # Create encrypted file, unset BTB_CREDENTIALS_PASSPHRASE
    # Expected: RuntimeError raised

# Encrypted file with correct passphrase loads
def test_encrypted_file_with_passphrase_loads(self):
    # Encrypt test credentials, set passphrase in env
    # Expected: credentials loaded correctly

# Missing file returns empty dict
def test_missing_all_files_returns_empty(self):
    # No file, no env vars
    # Expected: empty dict, no exception
```

**Acceptance criteria:** All tests pass with mocked filesystem. No real file I/O in tests.

---

#### TASK TEST-06 — Add tests for approval timeout and user-mismatch
**Priority:** MEDIUM  
**Files:** `tests/test_telegram_dispatch.py` (extend)

The Telegram approval flow has complex logic: token expiry, user mismatch, approval vs deny.

**Tests to add:**

```python
# Approval with correct token and correct user succeeds
def test_confirm_with_correct_token_succeeds(self):

# Approval fails with expired token
def test_confirm_expired_token_rejected(self):
    # Set token expires_at to past timestamp
    # Expected: "Pending change expired" message

# Approval fails with wrong user
def test_confirm_wrong_user_rejected(self):
    # Token requested by user_id=123, confirmed by user_id=456
    # Expected: "Only the requester can confirm" message

# Approval fails with wrong token string
def test_confirm_wrong_token_rejected(self):
    # Expected: "Invalid confirm token" message
```

**Acceptance criteria:** All 4 tests pass. No real Telegram API calls.

---

#### TASK TEST-07 — Measure and report current coverage
**Priority:** MEDIUM  
**Deliverable:** Coverage report

Run pytest with coverage and generate an HTML report. Identify the top 10 uncovered functions by line count.

```bash
source .venv/bin/activate
pytest tests/ --cov=. --cov-report=html --cov-report=term-missing \
    --cov-omit='.venv/*,webapp/static/*,webapp/templates/*' \
    -v 2>&1 | tee coverage_report.txt
```

Report which functions have 0% coverage and which have partial coverage. This report feeds the backlog for TEST-01 through TEST-06 ordering.

**Acceptance criteria:** `coverage_report.txt` created. HTML report in `htmlcov/`. Top 10 uncovered functions listed.

---

### AGENT: `btb-quant`

#### TASK QUANT-01 — Port H_V5 strategy to research backtester
**Priority:** CRITICAL  
**Files:** `scripts/btb_strategy_research.py`, `main.py`

**This is the highest-priority quant task.** The production strategy (`h1_signals` + `regime_filter` + structural exit) is not implemented in any backtest engine. All optimization results from `btb_strategy_research.py` are against proxy strategies that don't match live logic.

**What to do:**
1. Read `main.py` `h1_signals()` (lines 836–957) and `regime_filter()` (lines 788–834) carefully.
2. Implement `backtest_h_v5(df_signal, df_regime, p, costs)` in `scripts/btb_strategy_research.py` that faithfully replicates:
   - Regime filter on daily TF (EMA200 slope, EMA50 cross, RSI ≥ regime_rsi_min)
   - H_V5 signal: Donchian breakout (using `.iloc[-2]`) + pullback EMA band + RSI overheat guard
   - ATR-based initial stop: `close - atr_sl_trend_mult × ATR`
   - Trailing stop: `close - atr_trail_mult × ATR`, ratcheted up
   - Breakeven move at `breakeven_r × ATR` from entry
3. Do NOT import from `main.py` (that would create a circular dependency). Replicate the math.
4. Add the new strategy to the grid search in `search_multisymbol()`.
5. Verify against synthetic data that the backtest signals match `h1_signals()` output.

**Acceptance criteria:**
- Running `scripts/btb_strategy_research.py` produces results for "h_v5_breakout" strategy.
- Given identical synthetic OHLCV data, `backtest_h_v5()` generates the same entry signals as `h1_signals()`.
- No lookahead bias: `don_hi` comparison uses `iloc[i-1]` (previous bar high), not `iloc[i]`.

---

#### TASK QUANT-02 — Fix ML lookahead bias in `backtester.py`
**Priority:** HIGH  
**Files:** `backtester.py` lines 200–323

`ml_pattern_backtest()` trains on future-labeled data (B-01). This produces unrealistically good backtest results.

**What to do:**
1. Replace `label_future_returns()` with an expanding-window cross-validation approach:
   - Split data into N time folds
   - Train on folds 1..k, predict on fold k+1
   - Never train on data after prediction point
2. Alternatively, use proper "purged" cross-validation where train/test windows don't overlap.
3. After fix, re-run on BTC/USDT 1d data and compare before/after metrics. Expected: performance degrades significantly (honest result).

**Acceptance criteria:**
- No `df["close"].shift(-N)` in feature calculation or label calculation at prediction time.
- ML backtest metrics realistically lower than current inflated values.
- Comment in code explaining the cross-validation approach.

---

#### TASK QUANT-03 — Run full parameter optimization for H_V5 strategy
**Priority:** HIGH  
**Dependencies:** QUANT-01 must be complete first

After H_V5 is ported to the research backtester, run a systematic grid search to find optimal parameters for current market conditions (2024–2026 crypto cycle).

**Scope:**
- Symbols: BTC/USDT, ETH/USDT, SOL/USDT
- Timeframe: 4h (primary), 1h (secondary validation)
- Data: maximum available from `webapp_state.sqlite`; fetch more if < 1000 bars

**Parameter ranges to search:**

| Parameter | Range | Step |
|-----------|-------|------|
| `donchian_len` | 20, 30, 40, 60, 80 | — |
| `ema_fast` (pullback anchor) | 20, 30, 50, 100 | — |
| `regime_rsi_min` | 50, 55, 60 | — |
| `rsi_overheat` | 70, 75, 80 | — |
| `atr_sl_trend_mult` | 1.5, 2.0, 2.5, 3.0 | — |
| `atr_trail_mult` | 3.0, 5.0, 8.0, 10.0 | — |
| `breakeven_r` | 0.5, 1.0, 1.5 | — |
| `pullback_band_atr` | 0.5, 0.8, 1.2 | — |

**Selection criteria:**
- `selection_score > 5.0`
- `max_drawdown_pct > -25%` on test split
- `trade_count >= 6` in OOS (val + test combined)
- `profit_factor >= 1.4` on test split
- Test return ≥ 60% of val return (overfitting check)

**Deliverable:** Update `config.yaml` with best parameters found. Write optimization report section in this file under "Appendix: Latest Optimization Run".

---

#### TASK QUANT-04 — Analyze position sizing impact on risk-adjusted returns
**Priority:** MEDIUM

The current `per_trade_risk_pct: 0.5` is conservative. The `aggressive` mode uses `0.9`. Analyze what the optimal risk sizing is given H_V5's actual win rate and profit factor.

**What to do:**
1. Extract trade-level PnL from `logs/*.csv` if available, or from backtest output (after QUANT-01).
2. Calculate Kelly Criterion optimal fraction: `f* = (p × b - q) / b` where `p` = win rate, `q` = loss rate, `b` = average win/average loss ratio.
3. Compare Kelly f* to current `per_trade_risk_pct`. If Kelly > current, there is room to size up.
4. Apply half-Kelly as the recommended safe upper bound.
5. Update `risk.per_trade_risk_pct` in `config.yaml` if analysis supports a change.
6. Update `aggressive.risk.per_trade_risk_pct` with the full-Kelly or 80%-Kelly value.

**Acceptance criteria:** Written analysis with Kelly calculation, recommendation, and updated `config.yaml` if justified.

---

#### TASK QUANT-05 — Validate regime filter performance contribution
**Priority:** MEDIUM

The H_V5 regime filter (EMA200 + RSI daily) may be filtering out valid entries or allowing entries in bad conditions. Quantify its impact.

**What to do:**
1. Run `backtest_h_v5()` (from QUANT-01) twice:
   - With regime filter active (normal mode)
   - With regime filter disabled (`regime_rsi_min: 0`, always return `"trend"`)
2. Compare metrics across both runs for all symbols.
3. If regime filter *improves* val+test score: keep it, tune `regime_rsi_min`.
4. If regime filter *hurts* val+test score: investigate why. May need parameter adjustment.

**Acceptance criteria:** Side-by-side comparison table (filtered vs unfiltered). Recommendation with data support.

---

## Part 3 — Task Priority Matrix

| Priority | Task | Agent | Estimated Effort |
|----------|------|-------|-----------------|
| 🔴 CRITICAL | DEV-01 Empty password startup | `btb-developer` | 1 hour |
| 🔴 CRITICAL | REV-01 Auth security audit | `btb-reviewer` | 2 hours |
| 🔴 CRITICAL | QUANT-01 Port H_V5 to backtester | `btb-quant` | 1–2 days |
| 🟠 HIGH | DEV-02 Harden session tokens | `btb-developer` | 2 hours |
| 🟠 HIGH | DEV-03 Credential silent fallback | `btb-developer` | 1 hour |
| 🟠 HIGH | DEV-04 Atomic config save | `btb-developer` | 2 hours |
| 🟠 HIGH | DEV-05 Daily loss guard sign check | `btb-developer` | 1 hour |
| 🟠 HIGH | REV-02 Lookahead audit | `btb-reviewer` | 3 hours |
| 🟠 HIGH | REV-03 ML lookahead in backtester | `btb-reviewer` | 2 hours |
| 🟠 HIGH | REV-04 Credentials review | `btb-reviewer` | 2 hours |
| 🟠 HIGH | TEST-01 `h1_signals()` edge cases | `btb-tester` | 3 hours |
| 🟠 HIGH | TEST-02 `regime_filter()` tests | `btb-tester` | 3 hours |
| 🟠 HIGH | TEST-03 Config validation tests | `btb-tester` | 2 hours |
| 🟠 HIGH | TEST-04 Trade lifecycle integration | `btb-tester` | 4 hours |
| 🟠 HIGH | TEST-05 Credential fallback tests | `btb-tester` | 2 hours |
| 🟠 HIGH | QUANT-02 Fix ML lookahead | `btb-quant` | 3 hours |
| 🟠 HIGH | QUANT-03 Full H_V5 optimization | `btb-quant` | 4 hours |
| 🟡 MEDIUM | DEV-06 CSRF protection | `btb-developer` | 4 hours |
| 🟡 MEDIUM | DEV-07 Chart refresh consistency | `btb-developer` | 3 hours |
| 🟡 MEDIUM | REV-05 Risk math review | `btb-reviewer` | 2 hours |
| 🟡 MEDIUM | TEST-06 Approval flow tests | `btb-tester` | 2 hours |
| 🟡 MEDIUM | TEST-07 Coverage measurement | `btb-tester` | 1 hour |
| 🟡 MEDIUM | QUANT-04 Kelly sizing analysis | `btb-quant` | 3 hours |
| 🟡 MEDIUM | QUANT-05 Regime filter validation | `btb-quant` | 3 hours |
| 🟢 LOW | DEV-08 Fix aggressive loop_sleep key | `btb-developer` | 30 min |

---

## Part 4 — Recommended Sprint Order

### Sprint 1 — Security Hardening (do before any further testing)
1. DEV-01 → DEV-03 → DEV-02 → DEV-04 → DEV-05 (developer)
2. REV-04 → REV-01 (reviewer validates the security fixes)

### Sprint 2 — Test Foundation
1. TEST-07 (coverage baseline)
2. TEST-01 → TEST-02 → TEST-03 → TEST-05 (unit tests)
3. REV-02 → REV-03 (reviewer audits strategy and backtester)
4. TEST-04 (integration test, after unit tests green)

### Sprint 3 — Strategy Optimization
1. QUANT-01 (port H_V5 — prerequisite for everything quant)
2. QUANT-02 (fix ML lookahead)
3. QUANT-03 (full optimization, depends on QUANT-01)
4. QUANT-04 + QUANT-05 (sizing and regime analysis)

### Sprint 4 — Web UI Hardening
1. DEV-06 (CSRF)
2. DEV-07 (chart consistency)
3. REV-05 (risk math review)
4. TEST-06 (approval tests)
5. DEV-08 (minor config fix)

---

## Appendix A — Files Reviewed

| File | Lines | Role |
|------|-------|------|
| `main.py` | 1489 | CLI trading bot — strategy, risk, Telegram, main loop |
| `webapp/app.py` | 1481 | FastAPI web UI — routes, auth, scheduler, bot subprocess |
| `backtester.py` | 1021 | Standalone backtester — MR, ML, pivot strategies |
| `scripts/btb_strategy_research.py` | 465 | Grid-search optimizer with train/val/test splits |
| `webapp/models.py` | ~80 | Pydantic models |
| `webapp/config_manager.py` | ~60 | web_config.yaml load/save |
| `webapp/storage.py` | ~120 | SQLite wrapper (ohlcv + meta tables) |
| `webapp/chart_service.py` | ~100 | ccxt OHLCV fetch → storage |
| `webapp/backtest_service.py` | ~150 | In-process SMA backtest |
| `credentials.py` | 165 | Fernet-encrypted credential store |
| `telegram_shared.py` | ~80 | Shared Telegram helpers |
| `config.yaml` | 101 | Live bot configuration |
| `config.template.yaml` | 56 | Canonical template |
| `CONFIG_REFERENCE.md` | 633 | Configuration documentation |
| `tests/*.py` | 692 total | Test suite |

---

## Appendix B — Glossary

| Term | Meaning |
|------|---------|
| H_V5 | H-strategy version 5: breakout/pullback entry with breakeven move and EMA100 structural exit |
| Lookahead bias | Using future data in a signal or backtest that would not be available in live trading |
| OOS | Out-of-sample: validation or test split data not used during parameter optimization |
| profit_factor | Gross winning trades / Gross losing trades; > 1.5 is acceptable, > 2.0 is excellent |
| selection_score | Anti-overfit composite: `0.45×val_score + 0.55×test_score - trade_penalty` |
| dry_run | Simulate orders without sending to exchange; bot runs all logic but places no real trades |
| regime | Market condition classification: `trend`, `range`, or `none` |
| ATR | Average True Range; used for stop sizing and trailing stop calculation |
| breakeven_r | R-multiple (units of initial risk) at which stop moves to entry price |
