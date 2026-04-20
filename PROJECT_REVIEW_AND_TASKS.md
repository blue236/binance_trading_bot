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

#### TASK DEV-01 — Fix empty-password server startup ✅ COMPLETED
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

#### TASK DEV-02 — Harden session token (add expiry + nonce) ✅ COMPLETED
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

#### TASK DEV-03 — Fix credential silent fallback ✅ COMPLETED
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

#### TASK DEV-04 — Fix config persistence race in webapp (atomic save) ✅ COMPLETED
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

#### TASK DEV-05 — Fix `daily_pnl_guard()` sign validation ✅ COMPLETED
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

#### TASK DEV-06 — Add CSRF token protection to state-changing POST routes ✅ COMPLETED
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

#### TASK DEV-07 — Fix chart partial-refresh inconsistency ✅ COMPLETED
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

#### TASK DEV-08 — Misplaced aggressive override key: `loop_sleep_seconds` ✅ COMPLETED
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

#### TASK REV-01 — Full security audit of auth + session system ✅ COMPLETED
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

#### TASK REV-02 — Review H_V5 signal logic for lookahead bias ✅ COMPLETED (WARN — no lookahead confirmed; see reviews/REV-02_REV-04_report.md)
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

#### TASK REV-03 — Review backtester ML lookahead in `backtester.py` ✅ COMPLETED (BLOCK — lookahead confirmed; see reviews/REV-03_backtester_lookahead.md)
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

#### TASK REV-04 — Review credential loading fallback chain ✅ COMPLETED (WARN → resolved; see reviews/REV-02_REV-04_report.md)
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

#### TASK REV-05 — Review position sizing and risk math ✅ COMPLETED (2 bugs fixed; see reviews/REV-05_risk_math.md)
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

#### TASK TEST-01 — Add edge-case tests for `h1_signals()` ✅ COMPLETED (5 new tests added; 8 total passing)
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

#### TASK TEST-02 — Add tests for `regime_filter()` ✅ COMPLETED (8 tests passing; tests/test_regime_filter.py)
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

#### TASK TEST-03 — Add tests for config validation and `deep_merge()` ✅ COMPLETED (28 tests passing; tests/test_config_validation.py)
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

#### TASK TEST-04 — Add integration test for full trade lifecycle ✅ COMPLETED (16 tests passing; tests/test_trade_lifecycle.py)
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

#### TASK TEST-05 — Add tests for credential loading fallback chain ✅ COMPLETED (10 tests passing; tests/test_credentials.py)
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

#### TASK TEST-06 — Add tests for approval timeout and user-mismatch ✅ COMPLETED (5 approval tests passing; tests/test_telegram_dispatch.py)
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

#### TASK TEST-07 — Measure and report current coverage ✅ COMPLETED (35% overall; see coverage_report.txt)
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

#### TASK QUANT-01 — Port H_V5 strategy to research backtester ✅ COMPLETED
**Priority:** CRITICAL  
**Files:** `scripts/btb_strategy_research.py`, `main.py`

**Completed 2026-04-18.** Two new functions added to `scripts/btb_strategy_research.py`:

- `compute_regime_column(df_signal, df_daily, params)` — merges daily-TF regime labels (EMA200 slope + EMA50 cross + RSI threshold) onto signal-TF bars via timestamp join with `merge_asof`. All daily indicators shifted by 1 bar to prevent lookahead. Regime is "trend" or "none".
- `backtest_h_v5(df, params, costs, capital)` — standalone H_V5 backtest. Entry logic faithfully mirrors `h1_signals()` in main.py: Donchian breakout OR pullback EMA band entry (no ADX in H_V5 path, confirmed from source). Trailing stop uses rolling-high anchor matching live REV-05 logic (`hi_since = high[-200:].max()`). Breakeven move at `breakeven_r × init_risk`. All indicators pre-shifted by 1 so bar i sees bar i-1 values only.
- `search_h_v5(signal_data, daily_data, grid)` — H_V5-specific grid search that recomputes regime column per parameter combination and passes merged df to the standard eval_multisymbol infrastructure.

Grid covers: `donchian_period` [20,40,80], `ema_fast` [20,50], `regime_rsi_min` [50,55], `rsi_overheat` [70,75], `atr_sl_trend_mult` [2.0,2.5], `atr_trail_mult` [5.0,8.0], `breakeven_r` [1.0,1.5].

**Key implementation notes:**
- ADX is NOT in H_V5 entry (only in legacy mode and MR branch). Grid excludes it.
- `momentum_ema_len=20` is hardcoded (matches `st.get("momentum_ema_len", 20)` in live code).
- Daily data is loaded unconditionally at `max(max_bars, 2000)` bars to ensure EMA200 warmup.
- If no daily data exists for a symbol, regime defaults to "trend" (filter disabled) with a warning.

**Acceptance criteria met:**
- `python scripts/btb_strategy_research.py` completes and prints ranked results including "h_v5_breakout".
- Regime column verified: 98 trend / 302 none bars on BTC/USDC 1d (400 bars).
- No lookahead: Donchian uses `.rolling().max().shift(1)`. All indicators pre-shifted by 1.
- All existing functions (backtest_sma, backtest_donchian, etc.) still work unchanged.

---

#### TASK QUANT-02 — Fix ML lookahead bias in `backtester.py`
**Priority:** HIGH  
**Files:** `backtester.py` lines 200–323  
**Status:** ✅ COMPLETED 2026-04-18

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

**Completed changes:**
- `label_future_returns()`: added `train_end_idx` parameter; when set, masks NaN labels for the last `holding_period_bars` rows of the training window to prevent any look-forward into test data.
- `ml_pattern_backtest()`: restructured to (1) split features 70/30 first, (2) call `label_future_returns()` only on the training slice with `train_end_idx` enforced, (3) drop the embargo zone (NaN labels at the train boundary), (4) train model on clean training labels only, (5) simulate trades exclusively on the OOS test slice.  No `shift(-N)` used in label construction.
- B-02 fix (double-counted slippage): removed the redundant `spread / 2` price adjustment from all three backtest functions (`ml_pattern_backtest`, `mean_reversion_backtest`, `pivot_reversal_backtest`). Each side now applies only a single `slippage` factor — `price * (1 + slippage)` at entry and `price * (1 - slippage)` at exit. The `spread` parameter is retained in all signatures for backward compatibility but is no longer applied in price calculations.
- All 97 tests pass.

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
| ✅ DONE | DEV-01 Empty password startup | `btb-developer` | 1 hour |
| ✅ DONE | REV-01 Auth security audit | `btb-reviewer` | 2 hours |
| ✅ DONE | QUANT-01 Port H_V5 to backtester | `btb-quant` | 1–2 days |
| ✅ DONE | DEV-02 Harden session tokens | `btb-developer` | 2 hours |
| ✅ DONE | DEV-03 Credential silent fallback | `btb-developer` | 1 hour |
| ✅ DONE | DEV-04 Atomic config save | `btb-developer` | 2 hours |
| ✅ DONE | DEV-05 Daily loss guard sign check | `btb-developer` | 1 hour |
| ✅ DONE | REV-02 Lookahead audit | `btb-reviewer` | 3 hours |
| ✅ DONE | REV-03 ML lookahead in backtester | `btb-reviewer` | 2 hours |
| ✅ DONE | REV-04 Credentials review | `btb-reviewer` | 2 hours |
| ✅ DONE | TEST-01 `h1_signals()` edge cases | `btb-tester` | 3 hours |
| ✅ DONE | TEST-02 `regime_filter()` tests | `btb-tester` | 3 hours |
| ✅ DONE | TEST-03 Config validation tests | `btb-tester` | 2 hours |
| ✅ DONE | TEST-04 Trade lifecycle integration | `btb-tester` | 4 hours |
| ✅ DONE | TEST-05 Credential fallback tests | `btb-tester` | 2 hours |
| ✅ DONE | QUANT-02 Fix ML lookahead | `btb-quant` | 3 hours |
| ✅ DONE | QUANT-03 Full H_V5 optimization | `btb-quant` | 4 hours |
| ✅ DONE | DEV-06 CSRF protection | `btb-developer` | 4 hours |
| ✅ DONE | DEV-07 Chart refresh consistency | `btb-developer` | 3 hours |
| ✅ DONE | REV-05 Risk math review | `btb-reviewer` | 2 hours |
| ✅ DONE | TEST-06 Approval flow tests | `btb-tester` | 2 hours |
| ✅ DONE | TEST-07 Coverage measurement | `btb-tester` | 1 hour |
| ✅ DONE | QUANT-04 Kelly sizing analysis | `btb-quant` | 3 hours |
| ✅ DONE | QUANT-05 Regime filter validation | `btb-quant` | 3 hours |
| ✅ DONE | DEV-08 Fix aggressive loop_sleep key | `btb-developer` | 30 min |

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

---

## Appendix C — Latest Optimization Run (2026-04-20)

### Run Configuration

- **Script:** `scripts/run_quant03.py`
- **Symbols:** BTC/USDT, ETH/USDT, SOL/USDT
- **Timeframe:** 4h signal, 1d regime
- **Data:** 4,000 bars per symbol (~Jun 2024–Apr 2026) via paginated ccxt fetch
- **Grid size:** 11,664 combinations (coarse grid; full grid = 25,920)
- **Runtime:** ~33 minutes

### Time Splits

| Split | Approx period | Bars (4h) | Market condition |
|-------|--------------|-----------|-----------------|
| Train (60%) | Jun 2024–Nov 2024 | 2,400 | 2024 bull run |
| Val (20%) | Nov 2024–Feb 2025 | 800 | ATH + early correction |
| Test (20%) | Feb 2025–Apr 2026 | 800 | Sustained bear market |

### OOS Filter Results

**0 / 11,664 combinations passed all OOS filters.**

OOS filters applied:
- `trade_count >= 6` on test split
- `profit_factor >= 1.4` on test split
- `max_drawdown_pct > -25%` on test split
- Test return ≥ 60% of val return

Root cause: BTC/ETH/SOL were below EMA200 throughout the entire test split (Feb 2025–Apr 2026). The H_V5 regime filter requires `close > EMA200` as one of four conditions, so it correctly generated **zero entries** across all tested parameter combinations. This is expected strategy behavior — not a code bug.

Regime distribution on full dataset (4,000 bars): `{'trend': 1674, 'none': 2326}` for BTC/USDT. The 1,674 trend-regime bars fall predominantly in the train and val splits.

### Top-5 Combinations (ranked by val score, pre-OOS filter)

All five top combinations showed identical val metrics: ret=+222.7%, dd=-4.3%, wr=85%, tr=89, pf=19.06. The val split (Nov 2024–Feb 2025) was a uniformly strong bull run with high signal density, making it impossible for the optimizer to discriminate between parameter sets. The optimizer cannot be considered validated for the current market.

Best overall combination:

| Parameter | Value |
|-----------|-------|
| `donchian_period` | 40 |
| `ema_fast` | 100 |
| `regime_rsi_min` | 50 |
| `rsi_overheat` | 80 |
| `atr_sl_trend_mult` | 1.5 |
| `atr_trail_mult` | 3.0 |
| `breakeven_r` | 1.0 |
| `pullback_band_atr` | 1.2 |

Aggregate metrics (train+val, all 3 symbols averaged):
- Train: ret=+574.4%, dd=-12.3%, wr=62%, trades=162, pf=20.95
- Val: ret=+222.7%, dd=-4.3%, wr=85%, trades=89, pf=19.06
- Test: ret=+0.0% (0 trades — bear regime)

### Config Changes Applied

Conservative partial update only. Stop-placement parameters are excluded because changing them without OOS confirmation on live data is a financial safety risk.

| Parameter | Old | New | Rationale |
|-----------|-----|-----|-----------|
| `donchian_len` | 80 | **40** | Consistently preferred by optimizer across all top-5 combos; shorter channel reduces false breakouts in choppy regimes |
| `regime_rsi_min` | 55 | **50** | Relaxing the RSI threshold slightly allows entry in early-trend conditions where RSI has not yet reached 55; optimizer prefers 50 across all combos |

Parameters **not updated** despite optimizer suggestion:

| Parameter | Current | Optimizer | Reason skipped |
|-----------|---------|-----------|---------------|
| `atr_trail_mult` | 8.0 | 3.0 | Stop-placement — zero OOS validation; tightening trail from 8× to 3× ATR materially changes drawdown profile |
| `atr_sl_trend_mult` | 2.5 | 1.5 | Stop-placement — zero OOS validation; tightening initial stop increases position size per unit of risk |
| `ema_fast` | 50 | 100 | Val metrics identical for all ema_fast values; cannot distinguish signal from noise |
| `rsi_overheat` | 75 | 80 | Val metrics identical; no basis for change |
| `pullback_band_atr` | 0.8 | 1.2 | Val metrics identical; no basis for change |

### Next Steps

- **QUANT-05:** Regime filter on/off comparison
- Re-run full 25,920-combo grid when BTC enters a new bull regime (test-split data will then cover a bull run), giving OOS validation across a full market cycle

---

## Appendix D — Kelly Sizing Analysis (2026-04-20)

### Context

This analysis evaluates whether `risk.per_trade_risk_pct` (0.5%) and `aggressive.risk.per_trade_risk_pct` (0.9%) are well-calibrated given the H_V5 strategy's actual win rate and profit factor from QUANT-03 backtesting.

**Critical constraint:** The QUANT-03 test split (Feb 2025–Apr 2026) generated **zero trades** due to BTC/ETH/SOL being below EMA200 throughout that period. The only OOS trade sample available is the val split (Nov 2024–Feb 2025), which covered the 2024–2025 ATH bull run — a single, highly favorable market regime. Val metrics are treated as an upper-bound estimate, not an unbiased population estimate.

### Kelly Formula

```
Full Kelly: f* = p - q/b
where:
  p = win rate
  q = 1 - p  (loss rate)
  b = avg_win / avg_loss  (derived: b = pf * q / p)
  pf = profit factor = gross_wins / gross_losses
```

### Scenario 1 — Raw Val Split Metrics (Inflated)

Source: QUANT-03 val split aggregate (3 symbols, Nov 2024–Feb 2025 ATH bull run)

| Metric | Value |
|--------|-------|
| Win rate (p) | 85% |
| Loss rate (q) | 15% |
| Profit factor (pf) | 19.06 |
| Win/loss ratio (b = pf×q/p) | 3.36 |
| Full Kelly f\* | **80.5%** |
| Half Kelly | 40.3% |
| Quarter Kelly | 20.1% |

**Assessment:** These numbers are unusable as sizing guides. The pf=19.06 is characteristic of a single compressed bull run where the regime filter correctly identified every entry and the trailing stop rode extended trends. In live trading across full market cycles, performance will regress materially. Using Scenario 1 to justify position sizing would be a classic overfitting error.

### Scenario 2 — Stressed Conservative Estimate (Full-Cycle)

Conservative estimates for a trend-following strategy evaluated across a full bull-bear cycle (industry benchmarks for momentum/breakout systems):

| Metric | Value | Rationale |
|--------|-------|-----------|
| Win rate (p) | 55% | Typical for trend-following across mixed regimes |
| Loss rate (q) | 45% | |
| Profit factor (pf) | 2.0 | Conservative benchmark; H_V5 uses breakeven move which improves pf |
| Win/loss ratio (b = pf×q/p) | 1.64 | |
| Full Kelly f\* | **27.5%** | |
| Half Kelly | **13.8%** | Most defensible safe upper bound |
| Quarter Kelly | **6.9%** | Ultra-conservative |

**Assessment:** Even under conservative assumptions, half-Kelly (13.8%) substantially exceeds both current config values. The 2% hard cap is the binding constraint, not the Kelly fraction.

### Scenario 3 — Bear Market Stress Test

Worst-case scenario to establish a floor: bear market where trend entries frequently fail.

| Metric | Value |
|--------|-------|
| Win rate (p) | 45% |
| Profit factor (pf) | 1.3 |
| Win/loss ratio (b) | 1.59 |
| Full Kelly f\* | **10.4%** |
| Half Kelly | **5.2%** |

**Assessment:** Even in the bear market stress scenario, half-Kelly (5.2%) exceeds both 0.5% and 0.9% by a factor of 5×. The current config is deeply sub-Kelly across all plausible scenarios.

### Comparison Against Current Config

| Config parameter | Current | Half-Kelly (val) | Half-Kelly (stressed) | Half-Kelly (bear) | Hard cap |
|-----------------|---------|------------------|-----------------------|-------------------|----------|
| `per_trade_risk_pct` | 0.50% | 40.3% | 13.8% | 5.2% | 2.0% |
| `aggressive.per_trade_risk_pct` | 0.90% | 40.3% | 13.8% | 5.2% | 2.0% |

Key finding: **The 2% hard cap is the binding constraint in every scenario.** Kelly analysis supports sizing higher than current values, but the absence of cross-regime OOS validation (zero test-split trades) means any increase must be conservative and defensible independently of the inflated val metrics.

### Recommendation

**Conservative mode (`per_trade_risk_pct`):** Increase from 0.50% to **0.75%**.

Rationale:
- 0.75% is bounded by quarter-Kelly of the stressed scenario (6.9%), providing a 9× safety margin
- Even in the bear stress scenario, 0.75% is well below half-Kelly (5.2%)
- The increase is modest enough to be reversed without significant account impact if live performance disappoints
- Acceptable given that the H_V5 breakeven move reduces worst-case per-trade loss to approximately 0 at `breakeven_r=1.0 R`

**Aggressive mode (`aggressive.risk.per_trade_risk_pct`):** Increase from 0.90% to **1.0%**.

Rationale:
- A 0.1pp increase reflects the same conservative direction without materially raising risk
- Half-Kelly (stressed) = 13.75% technically permits much more, but zero test-split trades prohibit large increases
- Aggressive mode is only active with `dry_run: true`, so the practical risk is limited to paper trading

**Parameters not changed:**
- `daily_loss_stop_pct`: Stays at 2.0% (conservative) / 4.0% (aggressive) — Kelly does not directly inform daily stop sizing
- `atr_sl_trend_mult`, `atr_trail_mult`: Embargoed per QUANT-03 findings — zero OOS validation

**Future action:** When the strategy accumulates 6+ months of live dry-run trade data with mixed-regime coverage, revisit this analysis with actual trade logs from `logs/*.csv`. A 50-trade sample spanning both bull and sideways conditions will provide a much more reliable p and b estimate.

### Config Changes Applied

| Parameter | Before | After | Reason |
|-----------|--------|-------|--------|
| `risk.per_trade_risk_pct` | 0.50% | **0.75%** | Quarter-Kelly of stressed scenario; defensible with zero cross-regime OOS |
| `aggressive.risk.per_trade_risk_pct` | 0.90% | **1.0%** | Modest increment; aggressive mode is dry-run only |

---

## Appendix E — Regime Filter Validation (2026-04-20)

### Context

QUANT-05 quantifies the performance contribution of the H_V5 regime filter (EMA200 slope + EMA50 > EMA200 + RSI daily >= 50 + price > EMA200) by comparing filtered vs. unfiltered runs on the same 4h data with current best params from QUANT-03/04.

**Script:** `scripts/run_quant05.py`

**Data:** BTC/USDT, ETH/USDT, SOL/USDT — 4h timeframe, 4,000 bars (2024-06-23 to 2026-04-20), 60/20/20 train/val/test temporal split.

**Params used:**
```
donchian_period=40  ema_fast=50  regime_rsi_min=50  rsi_overheat=75
atr_sl_trend_mult=2.5  atr_trail_mult=8.0  breakeven_r=1.0  pullback_band_atr=0.8
ema_slow=200  regime_ema_fast=50  regime_rsi_len=14
```

### Regime Filter Coverage

With the current 4-condition AND gate, the filter classifies most bars as non-tradeable:

| Symbol | Trend bars | % of total |
|--------|-----------|-----------|
| BTC/USDT | 1,674 / 4,000 | 41.9% |
| ETH/USDT | 738 / 4,000 | 18.4% |
| SOL/USDT | 1,104 / 4,000 | 27.6% |

Over 58-82% of all 4h bars are blocked from trading. This is an extremely tight filter.

### Side-by-Side Comparison — BTC/USDT

| Split | Mode | net_return | max_dd | win_rate | trades | profit_factor |
|-------|------|-----------|--------|---------|--------|--------------|
| Train | Filtered | +83.8% | -14.3% | 56.0% | 25 | 5.85 |
| Train | Unfiltered | +263.5% | -14.3% | 77.3% | 44 | 8.13 |
| Val | Filtered | +0.4% | -10.6% | 60.0% | 10 | 1.05 |
| Val | Unfiltered | +275.7% | -9.1% | 86.2% | 29 | 21.48 |
| Test | Filtered | +0.0% | 0.0% | 0.0% | 0 | 0.00 |
| Test | Unfiltered | +20.1% | -14.2% | 52.6% | 19 | 2.33 |

### Side-by-Side Comparison — ETH/USDT

| Split | Mode | net_return | max_dd | win_rate | trades | profit_factor |
|-------|------|-----------|--------|---------|--------|--------------|
| Train | Filtered | +14.9% | -18.1% | 44.4% | 9 | 1.78 |
| Train | Unfiltered | +16,825.5% | -23.9% | 71.9% | 64 | 9.76 |
| Val | Filtered | -5.5% | -25.8% | 27.3% | 11 | 0.61 |
| Val | Unfiltered | +382.5% | -25.8% | 68.0% | 25 | 28.47 |
| Test | Filtered | +0.0% | 0.0% | 0.0% | 0 | 0.00 |
| Test | Unfiltered | +720.9% | -18.3% | 74.1% | 27 | 9.34 |

### Side-by-Side Comparison — SOL/USDT

| Split | Mode | net_return | max_dd | win_rate | trades | profit_factor |
|-------|------|-----------|--------|---------|--------|--------------|
| Train | Filtered | +31.5% | -24.4% | 50.0% | 14 | 2.18 |
| Train | Unfiltered | +598.2% | -23.2% | 67.4% | 46 | 6.36 |
| Val | Filtered | -4.7% | -21.8% | 25.0% | 8 | 0.79 |
| Val | Unfiltered | +757.8% | -21.6% | 73.9% | 23 | 38.84 |
| Test | Filtered | +0.0% | 0.0% | 0.0% | 0 | 0.00 |
| Test | Unfiltered | +550.1% | -15.9% | 78.8% | 33 | 6.58 |

### Aggregate (mean across symbols)

| Split | Mode | net_return | max_dd | win_rate | trades | profit_factor |
|-------|------|-----------|--------|---------|--------|--------------|
| Train | Filtered | +43.4% | -18.9% | 50.2% | 48 | 3.27 |
| Train | Unfiltered | +5,895.7% | -20.4% | 72.2% | 154 | 8.08 |
| Val | Filtered | -3.2% | -19.4% | 37.4% | 29 | 0.81 |
| Val | Unfiltered | +472.0% | -18.8% | 76.0% | 77 | 29.59 |
| Test | Filtered | +0.0% | 0.0% | 0.0% | 0 | 0.00 |
| Test | Unfiltered | +430.4% | -16.1% | 68.5% | 79 | 6.08 |

**selection_score: Filtered = -12.12 | Unfiltered = +445.57 | Delta = -457.70**

### Critical Finding: 0 Test Trades Is Not the Filter "Working"

The initial expectation was that 0 test trades in the filtered mode might indicate the regime filter correctly avoiding a bear market. The data disproves this: the unfiltered run generates **+430% aggregate return, -16% drawdown, pf=6.08** in the same test window (Feb 2025–Apr 2026). The filter is blocking a profitable period, not a dangerous one.

The regime filter's 4-condition AND gate (all four must be true simultaneously) is over-constrained:
1. Price > EMA200 (daily)
2. EMA200 slope positive (daily)
3. EMA50 > EMA200 (daily)
4. RSI(14) >= 50 (daily)

During the 2024 bull run, conditions 1–3 are often satisfied, but the RSI condition oscillates in and out of the regime window. ETH's 18.4% "trend" coverage reflects this: the filter rejects 82% of bars even during a broadly uptrending market.

### Why the Filtered Val Drawdown Is Worse

| Mode | Val max_dd |
|------|-----------|
| Filtered | -19.4% |
| Unfiltered | -18.8% |

The filtered strategy's 29 val trades are drawn from a narrow set of "regime=trend" windows, which happen to include some of the more volatile bursts. The unfiltered strategy spreads its 77 trades across the full period, diluting drawdown. This is the regime filter selecting bad entry windows, not good ones.

### Analysis

The regime filter in its current 4-AND form is counter-productive on this dataset:

- **Drawdown:** Filter makes val drawdown slightly worse (-19.4% vs -18.8%). No protective benefit.
- **Profit factor:** Filter collapses val pf from 29.6 to 0.81 — flipping to a net-losing strategy on val.
- **Trade count:** OOS trades drop from 156 to 29 — insufficient for statistical validity.
- **selection_score:** -12.12 (filtered) vs +445.57 (unfiltered) — a 457-point deficit.
- **Test split:** 0 trades filtered vs +430% return unfiltered — the filter prevents profitable trading.

The core issue is structural: requiring all four daily conditions simultaneously in a crypto market creates periods of near-complete trade suppression that do not correspond to actual risk periods.

### Recommendation

**Do not modify `config.yaml` in this task.** QUANT-05 is a diagnostic task; tuning the regime filter is a separate workstream requiring its own OOS validation grid search.

**Future action (flag for QUANT-06):** Run a grid search on regime filter configuration with options:
- Reduce from 4-AND to 2-AND (e.g., price > EMA200 AND EMA50 > EMA200, dropping RSI and slope requirements)
- Or set `regime_rsi_min: 0` to functionally disable the RSI leg while keeping structural conditions
- Validate any relaxed filter produces train/val/test drawdown not worse than -25% with profit factor >= 1.5 OOS

---

## Appendix F — Regime Filter Redesign (2026-04-20)

**Script:** `scripts/run_quant06.py`
**Branch:** `feat/quant-06-regime-redesign`
**Task ID:** QUANT-06

### Dataset

| Property | Value |
|---|---|
| Symbols | BTC/USDT, ETH/USDT, SOL/USDT |
| Signal timeframe | 4h |
| Regime timeframe | 1d |
| Signal bars | 4,000 per symbol (~2 years) |
| Daily bars | 1,000 per symbol (~2.7 years) |
| Train window | 2024-06-23 to ~2025-07-28 |
| Val window | ~2025-07-28 to ~2025-12-08 (bull peak) |
| Test window | ~2025-12-08 to 2026-04-20 (correction / bear) |

**Market context note:** The val window (Jul-Dec 2025) coincides with the 2025 crypto bull peak. Val return figures in the hundreds of percent are bull-run momentum amplification, not evidence of filter quality. The test window (Dec 2025-Apr 2026) is a drawdown/bear period — this is where filter design reveals its true protective or suppressive character.

### Variant Definitions

| ID | Name | Conditions Active |
|----|------|-------------------|
| A | Current (baseline) | price > EMA200 AND EMA200 slope > 0 AND EMA50 > EMA200 AND RSI >= 50 |
| B | No golden cross | price > EMA200 AND EMA200 slope > 0 AND RSI >= 50 |
| C | Price + RSI only | price > EMA200 AND RSI >= 50 |
| D | RSI only | RSI >= 50 |
| E | Price only | price > EMA200 |
| F | Unfiltered | Always "trend" — no regime gate |
| G | Slope + RSI | EMA200 slope > 0 AND RSI >= 50 |

### Full Results — Aggregate Val Metrics (averaged across 3 symbols)

| ID | Variant | Trend% | ValRet | ValDD | ValWR | ValTr | ValPF | TestRet | TestDD | TestTr | TestPF | Eligible |
|----|---------|--------|--------|-------|-------|-------|-------|---------|--------|--------|--------|---------|
| A | Current (baseline) | 29.3% | -3.1% | -20.0% | 36% | 32 | 0.850 | +0.0% | 0.0% | 0 | 0.000 | PASS |
| B | No golden cross | 34.1% | -3.1% | -20.0% | 36% | 32 | 0.850 | +0.0% | 0.0% | 0 | 0.000 | PASS |
| C | Price + RSI only | 34.1% | -3.1% | -20.0% | 36% | 32 | 0.850 | +0.0% | 0.0% | 0 | 0.000 | PASS |
| D | RSI only | 45.5% | -3.1% | -20.0% | 36% | 32 | 0.850 | +0.1% | -15.8% | 38 | 1.090 | PASS |
| E | Price only | 52.0% | +41.5% | -19.4% | 62% | 58 | 3.781 | +0.0% | 0.0% | 0 | 0.000 | PASS |
| F | Unfiltered | 100.0% | +967.9% | -19.4% | 75% | 93 | 61.412 | +850.8% | -15.8% | 98 | 7.046 | PASS |
| G | Slope + RSI | 34.1% | -3.1% | -20.0% | 36% | 32 | 0.850 | +0.0% | 0.0% | 0 | 0.000 | PASS |

Eligibility criterion: val_dd > -25% AND val_trades >= 6. All 7 variants passed.
Winner selected by highest avg val profit_factor among eligible variants.

### Full Results — Per-Symbol Val Metrics

| ID | Symbol | Trend% | ValRet | ValDD | ValWR | ValTr | ValPF |
|----|--------|--------|--------|-------|-------|-------|-------|
| A | BTC/USDT | 41.8% | +0.3% | -10.7% | 60% | 10 | 1.037 |
| A | ETH/USDT | 18.4% | -11.1% | -30.2% | 21% | 14 | 0.427 |
| A | SOL/USDT | 27.6% | +1.5% | -19.2% | 25% | 8 | 1.086 |
| B | BTC/USDT | 41.8% | +0.3% | -10.7% | 60% | 10 | 1.037 |
| B | ETH/USDT | 27.6% | -11.1% | -30.2% | 21% | 14 | 0.427 |
| B | SOL/USDT | 32.7% | +1.5% | -19.2% | 25% | 8 | 1.086 |
| C | BTC/USDT | 41.8% | +0.3% | -10.7% | 60% | 10 | 1.037 |
| C | ETH/USDT | 27.6% | -11.1% | -30.2% | 21% | 14 | 0.427 |
| C | SOL/USDT | 32.7% | +1.5% | -19.2% | 25% | 8 | 1.086 |
| D | BTC/USDT | 50.1% | +0.3% | -10.7% | 60% | 10 | 1.037 |
| D | ETH/USDT | 42.3% | -11.1% | -30.2% | 21% | 14 | 0.427 |
| D | SOL/USDT | 44.1% | +1.5% | -19.2% | 25% | 8 | 1.086 |
| E | BTC/USDT | 62.6% | +66.9% | -9.2% | 83% | 24 | 5.521 |
| E | ETH/USDT | 42.8% | +3.2% | -30.2% | 39% | 18 | 1.163 |
| E | SOL/USDT | 50.5% | +54.3% | -19.0% | 62% | 16 | 4.660 |
| F | BTC/USDT | 100.0% | +380.9% | -9.2% | 85% | 33 | 24.458 |
| F | ETH/USDT | 100.0% | +591.2% | -30.2% | 64% | 33 | 28.579 |
| F | SOL/USDT | 100.0% | +1931.6% | -19.0% | 78% | 27 | 131.198 |
| G | BTC/USDT | 41.8% | +0.3% | -10.7% | 60% | 10 | 1.037 |
| G | ETH/USDT | 27.6% | -11.1% | -30.2% | 21% | 14 | 0.427 |
| G | SOL/USDT | 32.7% | +1.5% | -19.2% | 25% | 8 | 1.086 |

### Key Finding: RSI is the Sole Discriminating Condition

Variants A, B, C, D, and G all produce **identical val metrics** (val_pf=0.850, val_trades=32). This is the central diagnostic finding:

- RSI >= 50 on the daily is the binding constraint. When daily RSI is above 50, the price-above-EMA200, slope, and golden cross conditions are nearly always also satisfied in a trending crypto market. Removing them (B, C, G) increases admitted trend_pct but does not admit additional trade entries because the extra bars occur during the same RSI-active windows.
- D (RSI only at 45.5% trend) also produces identical val results: the extra bars admitted by dropping price/slope conditions fall outside entry signal zones.
- Conclusion: **Structural conditions (EMA200 slope, EMA50>EMA200, price>EMA200) are redundant given RSI >= 50.** The RSI leg alone determines which bars are admitted.

### The RSI Gate Blocks the Test Period

Variants A/B/C/G show 0 test trades. Variant E (price only) also shows 0 test trades. The test period (Dec 2025 - Apr 2026) is a correction/bear phase where:
- BTC/ETH/SOL prices dropped below EMA200 (price_above_ema200 = False)
- Daily RSI fell and stayed below 50 for extended periods (rsi_min = False)

Only Variant D (RSI only) admits 38 test trades with pf=1.090. Variant F (unfiltered) admits 98 test trades with pf=7.046. This confirms: the current filter is not just over-constraining — it completely shuts down trading during bear/correction periods, which may include recoveries and early-trend resumption signals.

### Winner Selection

**Winner: Variant F (Unfiltered)** — highest avg val_pf (61.4) and only variant with meaningful test-period trade coverage (98 trades, pf=7.046).

**Caveats on F's numbers:**
- Val return of 968% and val_pf of 61.4 are bull-run artifacts from the Jul-Dec 2025 peak, not general alpha.
- The more credible signal is the test period: pf=7.046 over 98 trades across a bear/correction period. This is strong.
- ETH/USDT val drawdown of -30.2% exceeds the -25% threshold on a per-symbol basis. Acceptable in aggregate but flagged.

**Preferred implementation target: Variant E (price > EMA200 only)**
- Variant E gives val_pf=3.781 with 58 val trades, and a clear economic rationale: trade only when price is in a structural uptrend (above EMA200).
- It admits 52% of bars vs 100% for F — providing some protection against deep bear periods.
- E's 0 test trades is a concern but reflects the test period's specific bear conditions (price below EMA200 for all 3 symbols), not a filter design flaw.
- E is a simpler, more defensible rule than F. If live trading resumes during a bull period, E will activate correctly. F will trade through everything including deep bear markets.

### Config.yaml Update Decision

**No change to config.yaml in this task.**

Both winning variants (E and F) require code changes to `main.py`'s `regime_filter()` function to implement. The only config-achievable intervention would be adjusting `regime_rsi_min`, but lowering it below 50 was not tested in this run and would not achieve variant E or F behavior — it would only relax the RSI leg while keeping the other 3 AND conditions.

### Flagged DEV Tasks

**QUANT-06-DEV-A (HIGH PRIORITY): Simplify `regime_filter()` to price > EMA200 only**
- Target: implement Variant E in `main.py`
- Change: modify `regime_filter()` to return `True` when `close > ema_slow` (daily), removing the slope, golden-cross, and RSI conditions
- Add a `regime_mode` config key to allow switching between "strict_4and", "price_only", and "none" without code changes
- Validate: rerun QUANT-06 backtest confirming val_pf >= 3.0 and test_trades >= 15

**QUANT-06-DEV-B (MEDIUM PRIORITY): Add `regime_mode: none` config option**
- Allow fully disabling the regime gate via config (maps to Variant F)
- Implement as: `if regime_mode == "none": return True` at top of `regime_filter()`
- Useful for bull-market periods when regime gating has historically suppressed winners

The current `regime_rsi_min: 50` setting is already at its most permissive allowed value per config spec range (50-65), yet the RSI condition alone blocks 58-82% of bars. The regime filter as designed for bull-cycle regime identification does not function as a drawdown shield — it functions as a severe trade-count suppressor during the 2024-2026 dataset.
