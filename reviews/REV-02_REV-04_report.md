# Code Review Report
**Date:** 2026-04-16  
**Reviewer:** python-reviewer agent  
**Tasks:** REV-02 (H_V5 Lookahead Audit), REV-04 (Credentials Module Review)

---

## REV-02 — H_V5 Lookahead Bias Audit

### `regime_filter()` — PASS

`main.py:834–880`

All indicator reads in the `h_v5_b_plus_breakeven_ema100` path and the legacy path use `.iloc[-1]` on fully fetched series. No future candle data is accessed. `fetch_ohlc(..., 400)` on the daily timeframe provides 400 bars; EMA-200 is warm at `iloc[-1]` for any symbol older than 200 days.

**Minor note:** If the exchange returns fewer than 201 rows (new listing), `ema200.iloc[-1]` would be NaN and comparisons silently evaluate `False`, suppressing the regime signal without error. Safe for established symbols, invisible for freshly listed assets.

### `h1_signals()` — PASS with WARN

`main.py:882–975`

**Donchian high:** correctly uses `don_hi.iloc[-2]` (the previous bar's rolling high) with an explicit NaN guard that sets `don_hi_prev = None` and short-circuits the breakout condition to False. **No lookahead.**

**All other indicators** (`emaF`, `emaS`, `atr`, `rsi`, `adx`, `bb lower/mid`) use `.iloc[-1]` — contemporaneous, not future, values. **No lookahead.**

**WARN — thin warm-up margin at cold-start.** The guard at line ~885:

```python
if len(df) < max(st["donchian_len"], st["bb_len"], st["ema_slow"]) + 2:
```

With defaults `donchian_len=80, bb_len=20, ema_slow=200` this requires 202 bars minimum. The signal dataframe is fetched with `limit=300`. With 300 rows and EMA-200, only 100 warmed values exist at the tail — adequate in production. However, the guard allows execution with as few as 202 rows, yielding only 2 warmed EMA-200 values. A cold-start or exchange throttle returning exactly 202 bars would produce a marginally biased EMA signal.

**Recommended fix:** Increase the fetch limit from 300 to 400, or tighten the guard to `ema_slow * 2`.

### Structural Exit — PASS

`main.py:1428–1462`

The anti-lookahead logic is correctly implemented in two layers:

1. `d_df_closed = d_df.iloc[:-1].copy()` — drops the currently open candle. EMA is computed only on fully closed daily bars. ✓
2. `prev_close = d_df_closed["c"].shift(1)` and `prev_ema = d_ema.shift(1)` — adds one additional lag so the signal is based on the prior row's close vs the prior row's EMA. Strictest possible interpretation. ✓

The length check `len(d_df_closed) >= (d_ema_len + need + 5)` provides adequate headroom (fetch=220, guard requires 107 minimum, 219 rows available after dropping the forming candle). ✓

### EMA Warm-up Adequacy — WARN

- **`regime_filter()`:** fetches 400 bars for EMA-200. `iloc[-1]` is always in the warm region for established symbols. No NaN guard before `d["c"].iloc[-1] > ema200.iloc[-1]` — a NaN EMA silently evaluates the condition to False rather than raising. Low risk in practice.
- **`h1_signals()`:** 300-bar fetch against EMA-200. Guard minimum is 202 bars, leaving only 2 warm values at the boundary. Theoretical risk in production but worth closing.

### Overall Verdict: **WARN** (not BLOCK)

No confirmed lookahead bias anywhere in the signal chain. Two WARN conditions on thin warm-up tolerance at cold-start and absent NaN guards in `regime_filter`. The existing `iloc[:-1]` drop in the structural exit is correctly implemented.

**No changes required before dry-run trading. Recommended before live trading:** Increase `h1_signals()` fetch limit to 400 and add NaN guards in `regime_filter()`.

---

## REV-04 — Credentials Module Review

### DEV-03 Fix Correctness — PASS

`credentials.py:89–91`

```python
    except RuntimeError:
        raise
    except Exception:
        return None
```

Python evaluates `except` clauses in order. `RuntimeError` is caught by the first clause and re-raised before the broad `except Exception` is reached. The `RuntimeError` is never swallowed. The fix is structurally correct. ✓

**Side note (pre-existing, not DEV-03 scope):** The broad `except Exception: return None` still silently swallows wrong-passphrase and corrupted-file errors, causing `load_credentials()` to fall through to the plaintext path without any log message. Low priority but degrades debuggability.

### Plaintext Warning Visibility — WARN → FIXED

`credentials.py:134`

The warning used `logging.getLogger("bot").warning(...)`. `load_credentials()` is called before `setup_logger()` configures the `"bot"` logger. At that point the `"bot"` logger has no handlers. The warning could be silently lost.

**Fix applied:** Changed to `logging.warning(...)` (root logger). Python's `lastResort` handler (stderr, WARNING level) activates for the root logger when no other handlers exist, guaranteeing visibility at startup.

### PBKDF2 Parameters — WARN → FIXED

`credentials.py:62`

390,000 iterations is ~35% below the OWASP 2024 Password Storage Cheat Sheet recommendation of **600,000 iterations** for PBKDF2-HMAC-SHA256. Given that these credentials protect live exchange API keys and trading funds, the OWASP minimum is applied.

**Fix applied:** Increased from `iterations=390000` to `iterations=600000`. The `"iter"` field in the JSON payload is informational; existing encrypted files remain readable after this change — only newly written files use the higher count.

### File Permissions — PASS (LOW note)

`credentials.py:109–114`

`os.chmod(path, 0o600)` is called after file creation. Brief TOCTOU window exists (file created with umask-derived permissions before chmod). This is the standard pattern (used by ssh-keygen) and negligible in practice. `os.chmod` failures are silently swallowed — a log call in the except block would improve deployment diagnostics. **Not blocking.**

### Overall Verdict: **WARN → resolved**

Two MEDIUM items identified and fixed in the same session:
1. Root logger used for plaintext warning (was: named "bot" logger with no handlers at call time)
2. PBKDF2 iterations raised to 600,000 (was: 390,000, below OWASP 2024 recommendation)

No CRITICAL or BLOCK items. Module is approved for continued use.
