# REV-05: Risk Management Math Audit

**Reviewer:** Code Reviewer Agent  
**Date:** 2026-04-16  
**Scope:** `main.py` — risk sizing, order constraints, daily PnL guard, trailing stop, breakeven logic  
**Verdict: BLOCK** — 1 FAIL, 1 HIGH

---

## 1. `position_size` — WARN

**Lines:** 1050–1065

```python
def position_size(equity_usdt, price, atr, atr_mult, risk_pct):
    risk_usdt = equity_usdt * (risk_pct / 100.0)
    stop_dist = atr_mult * atr
    if stop_dist <= 0:
        return 0.0
    qty = max(risk_usdt / stop_dist, 0.0)
    ...
```

**Formula correctness:** The task spec describes `risk_amount / (close - sl)`. The actual formula is `risk_usdt / (atr_mult * atr)`. These are equivalent because at entry `sl = close - atr_mult * atr`, so `close - sl = atr_mult * atr`. The math is correct.

**Division by zero:** Guarded — `if stop_dist <= 0: return 0.0` at line 1053.

**Equity cap:** Not inside this function, but correctly applied at the call site (lines 1345–1346):
```python
max_affordable = free_base / price if price > 0 else 0.0
qty = clamp_qty(exchange, symbol, min(risk_qty, max_affordable))
```
This is acceptable, but the function accepts a `price` parameter (line 1050) that is **never used inside the function body**. It is logged in the debug message but plays no role in sizing. This is a dead parameter in a safety-critical function.

**[MEDIUM] Dead parameter `price`**  
File: `main.py:1050`  
Issue: `price` is accepted but not used in the sizing formula. This is confusing — a reader may assume price factors into the size calculation.  
Fix: Either remove `price` from the signature and the debug log line, or add a comment explaining it is passed for logging purposes only. If the intent was to implement a position-value cap (qty × price ≤ some fraction of equity), that logic is missing from the function itself.

---

## 2. `order_constraints_ok` — PASS

**Lines:** 1084–1090

```python
def order_constraints_ok(exchange, symbol, qty, price, min_notional):
    lim = symbol_limits(exchange, symbol)
    if lim["min_amount"] is not None and qty < lim["min_amount"]:
        return False
    if lim["min_cost"] is not None and (qty * price) < lim["min_cost"]:
        return False
    return notional_ok(qty, price, min_notional)
```

- Correctly checks exchange `min_amount` from the market structure.
- Correctly checks exchange `min_cost` (distinct from `min_notional_usdc`).
- `notional_ok` (line 1067–1068) checks `qty * price >= min_notional`.
- Balance check is not inside this function, but the call site already caps `qty` via `min(risk_qty, max_affordable)` before reaching this gate, so double-checking balance here is not required.
- No edge-case issues with very small `qty` or `price`: both `min_amount` and `min_cost` checks use `None`-safe guards; `notional_ok` will simply return `False` for dust amounts.

**No issues found.**

---

## 3. `daily_pnl_guard` — FAIL

**Lines:** 1184–1188

```python
def daily_pnl_guard(cfg, equity_now, equity_start):
    if equity_start <= 0:
        return False
    dd = (equity_now - equity_start) / equity_start * 100.0
    return dd <= -abs(cfg["risk"]["daily_loss_stop_pct"])
```

**Sign convention:** Correct. `dd` is negative when equity has fallen; `dd <= -abs(threshold)` triggers when the loss equals or exceeds the configured stop. The `-abs()` double-negation is defensive and correct given the post-DEV-05 `validate_config` guarantee that the stored value is non-negative.

**[FAIL] Unguarded `KeyError` / `TypeError` when `daily_loss_stop_pct` is absent or null**  
File: `main.py:1188`  
Issue: `cfg["risk"]["daily_loss_stop_pct"]` raises `KeyError` if the key is absent from the YAML, or `TypeError` from `abs(None)` if it is set to `null`. `validate_config` (lines 34–85) only validates the key when it is *present*; it does not insert a default. If the key is missing, the guard crashes rather than either triggering or failing safe. This crashes the trading loop at line 1302 where `daily_pnl_guard` is called on every iteration.

Fix — add a safe default in the guard itself:

```python
def daily_pnl_guard(cfg, equity_now, equity_start):
    if equity_start <= 0:
        return False
    stop_pct = cfg.get("risk", {}).get("daily_loss_stop_pct")
    if stop_pct is None:
        return False   # guard disabled when not configured
    dd = (equity_now - equity_start) / equity_start * 100.0
    return dd <= -abs(float(stop_pct))
```

Alternatively, ensure `validate_config` raises on a missing key so the bot refuses to start rather than crashing mid-session. The current behaviour (crash mid-session) is the worst of both options.

---

## 4. Trailing Stop Update Logic — HIGH

**Lines:** 1411–1417

```python
if pos["signal"] == "T_LONG" and "trail_mult" in pos:
    df = fetch_ohlc(exchange, sym, cfg["general"]["timeframe_signal"], 200, cfg=cfg, logger=logger)
    atr = AverageTrueRange(df["h"], df["l"], df["c"], cfg["strategy"]["atr_len"]).average_true_range().iloc[-1]
    hi_since = df["c"].iloc[-200:].max()   # <-- BUG: uses closes, not highs
    trail = hi_since - pos["trail_mult"] * atr
    if trail > sl:
        sl = trail; pos["sl"] = float(sl); changed = True
```

**Widening guard:** `if trail > sl` at line 1416 correctly ensures the stop can only move up. PASS.

**ATR freshness:** The ATR is freshly computed on each exit-check cycle from the latest OHLCV (not the ATR from entry). This is correct for an ATR-based trailing stop that should adapt to current volatility.

**[HIGH] Trail anchor uses closing prices instead of period highs**  
File: `main.py:1414`  
Issue: `hi_since = df["c"].iloc[-200:].max()` takes the maximum *closing* price as the peak. A trailing stop on a long should anchor to the highest *high* reached, not the highest close. Using closes underestimates the actual peak the asset reached, which produces a trail level lower than intended — the stop is too loose. In a strongly trending candle where close is significantly below the high, the trail can lag materially.  
Fix:
```python
hi_since = df["h"].iloc[-200:].max()
```

---

## 5. Breakeven Move — PASS

**Lines:** 1421–1430

```python
init_r = float(pos.get("init_r", max(float(pos.get("entry_price", last)) - float(pos.get("sl", sl)), 1e-9)))
be_r = float(pos.get("breakeven_r", cfg.get("strategy", {}).get("breakeven_r", 1.0)))
entry_px = float(pos.get("entry_price", last))
if float(last) >= (entry_px + be_r * init_r):
    be_sl = max(float(pos.get("sl", sl)), entry_px)
    if be_sl > sl:
        sl = be_sl
        pos["sl"] = float(sl)
        changed = True
```

**Trigger level:** `entry_px + be_r * init_r` where `init_r` is the initial ATR-based stop distance. For `breakeven_r=1.0` this is `entry + 1×init_R`. Correct.

**Breakeven price:** `be_sl = max(current_sl, entry_px)`. The stop moves to `entry_px` (true capital-preserving breakeven), not to `entry_px + ATR`. Correct.

**Idempotency:** `if be_sl > sl` — this comparison is against the local `sl` variable, which already reflects any trailing stop update from the block above (lines 1411–1417). So re-applying breakeven when the trailing stop has already pushed the stop above entry is correctly a no-op. Idempotency is sound.

**Fallback for missing `init_r`:** If `pos["init_r"]` was never stored (e.g., a position entered before this field was added), the inline fallback `max(entry_price - sl, 1e-9)` reconstructs it from the stored stop. The `1e-9` floor prevents a zero denominator. Acceptable.

**No issues found.**

---

## Summary Table

| Area | Verdict | Severity | Line(s) |
|------|---------|----------|---------|
| `position_size` formula | PASS | — | 1051–1055 |
| `position_size` dead `price` param | WARN | MEDIUM | 1050 |
| `position_size` equity cap | PASS | — | call site 1345–1346 |
| `order_constraints_ok` | PASS | — | 1084–1090 |
| `daily_pnl_guard` sign convention | PASS | — | 1184–1188 |
| `daily_pnl_guard` missing key crash | FAIL | CRITICAL | 1188 |
| Trailing stop widening guard | PASS | — | 1416 |
| Trailing stop uses closes not highs | FAIL | HIGH | 1414 |
| Trailing stop ATR freshness | PASS | — | 1413 |
| Breakeven trigger level | PASS | — | 1425 |
| Breakeven price (to entry, not entry+ATR) | PASS | — | 1426 |
| Breakeven idempotency | PASS | — | 1427 |

**Overall: BLOCK**  
Two issues must be fixed before this code handles live capital:

1. `daily_pnl_guard` — line 1188 — crashes if `daily_loss_stop_pct` is absent from config.
2. Trailing stop anchor — line 1414 — must use `df["h"]` not `df["c"]`.
