#!/usr/bin/env python3
"""QUANT-03: Full H_V5 parameter grid search.

Fetches fresh OHLCV from Binance, runs a two-phase grid search
(coarse → fine-tune), filters candidates by OOS criteria, and prints
a ranked report.

Usage:
    python scripts/run_quant03.py

Results are printed to stdout as structured text.
"""
from __future__ import annotations

import sys
import time
from itertools import product
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Import shared backtest infrastructure from the existing research module.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
from btb_strategy_research import (
    Costs,
    backtest_h_v5,
    combine_metrics,
    compute_regime_column,
    eval_multisymbol,
    score,
    selection_score,
    splits,
)

COSTS = Costs(fee_rate=0.001, slippage_rate=0.0005)
CAPITAL = 10_000.0

# Fixed params required by compute_regime_column and backtest_h_v5 that are
# not part of the optimisation grid.
FIXED_PARAMS = {
    "ema_slow": 200,
    "regime_ema_fast": 50,
    "regime_rsi_len": 14,
    "atr_period": 14,
    "rsi_period": 14,
    "cooldown_bars": 3,
}

# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame | None:
    """Fetch OHLCV bars from Binance, paginating backwards to collect `limit` bars."""
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        page_size = 1000
        all_rows: list = []
        since = None  # fetch backwards from current time

        while len(all_rows) < limit:
            if since is not None:
                raw = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=page_size)
            else:
                raw = exchange.fetch_ohlcv(symbol, timeframe, limit=page_size)

            if not raw:
                break

            # Prepend (we're walking backwards by setting since to before earliest bar)
            all_rows = raw + all_rows
            if len(raw) < page_size:
                break  # no more history

            # Walk back: set since to just before the earliest bar we got
            earliest_ts = raw[0][0]
            # Compute one bar duration in ms
            tf_ms = exchange.parse_timeframe(timeframe) * 1000
            since = earliest_ts - page_size * tf_ms

            if len(all_rows) >= limit:
                break

        # Trim to requested limit (keep most recent bars)
        if len(all_rows) > limit:
            all_rows = all_rows[-limit:]

        df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
        # Drop duplicate timestamps from pagination overlap
        df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        df = df[df["close"] > 0].copy()
        # Remove obvious price glitches (>171% bar-to-bar move)
        lr = np.log(df["close"] / df["close"].shift(1))
        df = df[(lr.abs() <= 1.0) | lr.isna()].copy()
        df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.reset_index(drop=True)
        print(
            f"  [fetch] {symbol} {timeframe}: {len(df)} bars "
            f"({df['dt'].iloc[0].date()} to {df['dt'].iloc[-1].date()})"
        )
        return df
    except Exception as exc:
        print(f"  [warn] Failed to fetch {symbol} {timeframe}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Grid search helper — collects ALL results (not just best)
# ---------------------------------------------------------------------------

def run_grid_search(
    signal_data: dict[str, pd.DataFrame],
    daily_data: dict[str, pd.DataFrame],
    grid: dict,
    label: str = "",
) -> list[dict]:
    """Run H_V5 grid search, returning all (params, agg, by_symbol, sel_score) dicts."""
    keys = list(grid.keys())
    combos = list(product(*grid.values()))
    total = len(combos)
    print(f"  [{label}] {total} combinations × {len(signal_data)} symbols …")

    results = []
    t0 = time.time()

    for idx, vals in enumerate(combos):
        p = {**FIXED_PARAMS, **dict(zip(keys, vals))}

        # Build per-symbol signal dfs with the regime column for these params.
        merged: dict[str, pd.DataFrame] = {}
        for symbol, df_sig in signal_data.items():
            df = df_sig.copy()
            if symbol in daily_data:
                df["regime"] = compute_regime_column(df, daily_data[symbol], p)
            else:
                df["regime"] = "trend"
            merged[symbol] = df

        by_symbol, agg = eval_multisymbol(merged, backtest_h_v5, p)
        sel = selection_score(agg)
        results.append(
            {
                "params": dict(p),  # full param dict including fixed
                "aggregate": agg,
                "by_symbol": by_symbol,
                "selection_score": round(sel, 4),
            }
        )

        # Progress logging every 10%
        if (idx + 1) % max(1, total // 10) == 0 or idx == 0:
            elapsed = time.time() - t0
            pct = (idx + 1) / total * 100
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            eta = (total - idx - 1) / rate if rate > 0 else 0
            print(
                f"    {pct:5.1f}%  {idx+1}/{total}  "
                f"elapsed={elapsed:.0f}s  ETA={eta:.0f}s  "
                f"best_so_far={max(r['selection_score'] for r in results):.2f}"
            )

    elapsed = time.time() - t0
    print(f"  [{label}] done in {elapsed:.1f}s — {total} combos evaluated")
    return results


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def passes_filters(rec: dict) -> bool:
    """Apply Step 3 OOS quality filters."""
    agg = rec["aggregate"]
    test = agg["test"]
    val = agg["val"]

    if rec["selection_score"] <= 5.0:
        return False
    if test["max_drawdown_pct"] < -25.0:
        return False
    oos_trades = val["trade_count"] + test["trade_count"]
    if oos_trades < 6:
        return False
    if test["profit_factor"] < 1.4:
        return False
    # Test return >= 60% of val return (overfitting guard)
    if val["net_return_pct"] > 0 and test["net_return_pct"] < 0.6 * val["net_return_pct"]:
        return False
    return True


def per_symbol_passes(by_symbol: dict, symbol: str) -> bool:
    """Check if a single symbol passes OOS criteria independently."""
    test = by_symbol[symbol]["test"]
    val = by_symbol[symbol]["val"]
    oos_trades = val["trade_count"] + test["trade_count"]
    if oos_trades < 3:  # per-symbol threshold is half the aggregate
        return False
    if test["max_drawdown_pct"] < -30.0:
        return False
    if test["profit_factor"] < 1.2:
        return False
    return True


def symbols_passing(rec: dict, symbols: list[str]) -> int:
    """Count how many symbols pass independent OOS filters."""
    return sum(per_symbol_passes(rec["by_symbol"], s) for s in symbols)


# ---------------------------------------------------------------------------
# Fine-tuning grid around a best region
# ---------------------------------------------------------------------------

def build_fine_grid(best_params: dict) -> dict:
    """Build a fine-tune grid centred on best_params from coarse search."""
    def neighbours(val, coarse_vals, fine_step_ratio=0.5):
        """Return val and its immediate neighbours in the coarse list."""
        vals = sorted(coarse_vals)
        idx = vals.index(val) if val in vals else 0
        candidates = {val}
        if idx > 0:
            mid = val - (val - vals[idx - 1]) * fine_step_ratio
            candidates.add(round(mid, 3))
        if idx < len(vals) - 1:
            mid = val + (vals[idx + 1] - val) * fine_step_ratio
            candidates.add(round(mid, 3))
        return sorted(candidates)

    COARSE_DON = [20, 40, 80]
    COARSE_EMA = [20, 50, 100]
    COARSE_RSI_MIN = [50, 55, 60]
    COARSE_OVERHEAT = [70, 75, 80]
    COARSE_SL = [1.5, 2.0, 2.5, 3.0]
    COARSE_TRAIL = [3.0, 5.0, 8.0, 10.0]
    COARSE_BE = [0.5, 1.0, 1.5]
    COARSE_PB = [0.5, 0.8, 1.2]

    return {
        "donchian_period": neighbours(best_params["donchian_period"], COARSE_DON),
        "ema_fast": neighbours(best_params["ema_fast"], COARSE_EMA),
        "regime_rsi_min": neighbours(best_params["regime_rsi_min"], COARSE_RSI_MIN),
        "rsi_overheat": neighbours(best_params["rsi_overheat"], COARSE_OVERHEAT),
        "atr_sl_trend_mult": neighbours(best_params["atr_sl_trend_mult"], COARSE_SL),
        "atr_trail_mult": neighbours(best_params["atr_trail_mult"], COARSE_TRAIL),
        "breakeven_r": neighbours(best_params["breakeven_r"], COARSE_BE),
        "pullback_band_atr": neighbours(best_params["pullback_band_atr"], COARSE_PB),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def fmt_metrics(m: dict) -> str:
    return (
        f"ret={m['net_return_pct']:+.1f}%  "
        f"dd={m['max_drawdown_pct']:.1f}%  "
        f"wr={m['win_rate_pct']:.0f}%  "
        f"tr={m['trade_count']}  "
        f"pf={m['profit_factor']:.2f}"
    )


def print_top5(results: list[dict], symbols: list[str], label: str = "") -> None:
    sorted_res = sorted(results, key=lambda r: r["selection_score"], reverse=True)
    passing = [r for r in sorted_res if passes_filters(r)]
    print(f"\n{'='*70}")
    print(f"  {label} — Top 5 by selection_score ({len(passing)} passed filters)")
    print(f"{'='*70}")
    for rank, rec in enumerate(sorted_res[:5], 1):
        p = rec["params"]
        agg = rec["aggregate"]
        sym_pass = symbols_passing(rec, symbols)
        filtered = passes_filters(rec)
        print(
            f"\n  #{rank}  score={rec['selection_score']:.4f}  "
            f"filters={'PASS' if filtered else 'FAIL'}  "
            f"robust_symbols={sym_pass}/{len(symbols)}"
        )
        print(
            f"       don={p['donchian_period']}  ema_fast={p['ema_fast']}  "
            f"rsi_min={p['regime_rsi_min']}  overheat={p['rsi_overheat']}"
        )
        print(
            f"       sl={p['atr_sl_trend_mult']}  trail={p['atr_trail_mult']}  "
            f"be={p['breakeven_r']}  pb_atr={p['pullback_band_atr']}"
        )
        print(f"       TRAIN:  {fmt_metrics(agg['train'])}")
        print(f"       VAL:    {fmt_metrics(agg['val'])}")
        print(f"       TEST:   {fmt_metrics(agg['test'])}")
        for sym in symbols:
            bsym = rec["by_symbol"].get(sym, {})
            if bsym:
                print(f"         {sym}: test={fmt_metrics(bsym['test'])}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    SIGNAL_TF = "4h"
    DAILY_TF = "1d"
    SIGNAL_LIMIT = 4000   # ~2 years of 4h data — covers 2024 bull run + 2025 correction
    DAILY_LIMIT = 1000

    print("\n" + "=" * 70)
    print("  QUANT-03: H_V5 Full Parameter Grid Search")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Step 1 — Fetch data
    # ------------------------------------------------------------------
    print("\n[Step 1] Fetching OHLCV data from Binance …")
    signal_data: dict[str, pd.DataFrame] = {}
    daily_data: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        sig = fetch_ohlcv(sym, SIGNAL_TF, SIGNAL_LIMIT)
        if sig is not None and len(sig) >= 200:
            signal_data[sym] = sig
        else:
            print(f"  [skip] {sym} signal data insufficient — skipped")
        day = fetch_ohlcv(sym, DAILY_TF, DAILY_LIMIT)
        if day is not None and len(day) >= 202:  # EMA200 warmup
            daily_data[sym] = day
        else:
            print(f"  [warn] {sym} daily data insufficient — regime filter disabled")

    if not signal_data:
        print("[ERROR] No usable signal data. Aborting.")
        sys.exit(1)

    active_symbols = list(signal_data.keys())
    print(f"\nActive symbols: {active_symbols}")

    # ------------------------------------------------------------------
    # Step 2a — Time 50 combos to estimate total runtime
    # ------------------------------------------------------------------
    print("\n[Step 2] Timing 50 probe combinations …")

    # Mini coarse grid for timing probe (50 combos)
    probe_grid = {
        "donchian_period": [20, 40, 80],
        "ema_fast": [20, 50],
        "regime_rsi_min": [50, 55],
        "rsi_overheat": [70, 75],
        "atr_sl_trend_mult": [2.0, 2.5],
        "atr_trail_mult": [5.0, 8.0],
        "breakeven_r": [1.0],
        "pullback_band_atr": [0.8],
    }
    probe_combos = list(product(*probe_grid.values()))[:50]
    probe_keys = list(probe_grid.keys())

    t_probe_start = time.time()
    for vals in probe_combos:
        p = {**FIXED_PARAMS, **dict(zip(probe_keys, vals))}
        merged = {}
        for sym, df_sig in signal_data.items():
            df = df_sig.copy()
            if sym in daily_data:
                df["regime"] = compute_regime_column(df, daily_data[sym], p)
            else:
                df["regime"] = "trend"
            merged[sym] = df
        eval_multisymbol(merged, backtest_h_v5, p)

    probe_elapsed = time.time() - t_probe_start
    rate_per_combo = probe_elapsed / 50.0

    # Full grid size
    FULL_GRID = {
        "donchian_period": [20, 30, 40, 60, 80],
        "ema_fast": [20, 30, 50, 100],
        "regime_rsi_min": [50, 55, 60],
        "rsi_overheat": [70, 75, 80],
        "atr_sl_trend_mult": [1.5, 2.0, 2.5, 3.0],
        "atr_trail_mult": [3.0, 5.0, 8.0, 10.0],
        "breakeven_r": [0.5, 1.0, 1.5],
        "pullback_band_atr": [0.5, 0.8, 1.2],
    }
    full_total = 1
    for v in FULL_GRID.values():
        full_total *= len(v)

    full_est_sec = rate_per_combo * full_total
    print(
        f"  50 probe combos: {probe_elapsed:.1f}s  ({rate_per_combo:.2f}s/combo)\n"
        f"  Full grid: {full_total} combos  estimated {full_est_sec/60:.1f} min"
    )

    # ------------------------------------------------------------------
    # Step 2b — Coarse grid if full grid > 30 min
    # ------------------------------------------------------------------
    COARSE_GRID = {
        "donchian_period": [20, 40, 80],
        "ema_fast": [20, 50, 100],
        "regime_rsi_min": [50, 55, 60],
        "rsi_overheat": [70, 75, 80],
        "atr_sl_trend_mult": [1.5, 2.0, 2.5, 3.0],
        "atr_trail_mult": [3.0, 5.0, 8.0, 10.0],
        "breakeven_r": [0.5, 1.0, 1.5],
        "pullback_band_atr": [0.5, 0.8, 1.2],
    }
    coarse_total = 1
    for v in COARSE_GRID.values():
        coarse_total *= len(v)
    coarse_est_sec = rate_per_combo * coarse_total

    if full_est_sec <= 1800:  # 30 min
        print("\n  Full grid feasible — running full search")
        primary_grid = FULL_GRID
        phase_label = "full"
    else:
        print(
            f"\n  Full grid would take ~{full_est_sec/60:.0f} min — switching to coarse grid "
            f"({coarse_total} combos, ~{coarse_est_sec/60:.1f} min)"
        )
        primary_grid = COARSE_GRID
        phase_label = "coarse"

    # ------------------------------------------------------------------
    # Phase 1: primary grid search
    # ------------------------------------------------------------------
    print(f"\n[Step 2c] Phase 1 — {phase_label} grid search")
    all_results = run_grid_search(signal_data, daily_data, primary_grid, label=phase_label)
    print_top5(all_results, active_symbols, label=f"Phase 1 ({phase_label})")

    # ------------------------------------------------------------------
    # Phase 2: fine-tune around best coarse result (if we ran coarse)
    # ------------------------------------------------------------------
    fine_results = []
    if phase_label == "coarse":
        best_coarse = max(all_results, key=lambda r: r["selection_score"])
        print(f"\n[Step 2d] Phase 2 — fine grid around best coarse params: score={best_coarse['selection_score']}")
        fine_grid = build_fine_grid(best_coarse["params"])
        print(f"  Fine grid: {fine_grid}")
        fine_results = run_grid_search(signal_data, daily_data, fine_grid, label="fine")
        print_top5(fine_results, active_symbols, label="Phase 2 (fine)")

    # ------------------------------------------------------------------
    # Step 3/4 — Filter and select best
    # ------------------------------------------------------------------
    all_combined = all_results + fine_results
    all_sorted = sorted(all_combined, key=lambda r: r["selection_score"], reverse=True)
    passing = [r for r in all_sorted if passes_filters(r)]

    print(f"\n[Step 3] Filter summary: {len(passing)}/{len(all_combined)} combos pass all filters")

    # Robustness check: prefer params that pass independently on >= 2 symbols
    robust_passing = [r for r in passing if symbols_passing(r, active_symbols) >= min(2, len(active_symbols))]
    print(f"  Robust (>= 2 symbols): {len(robust_passing)}")

    # Select winner
    if robust_passing:
        winner = robust_passing[0]
        winner_source = "robust (>= 2 symbols)"
    elif passing:
        winner = passing[0]
        winner_source = "best filtered (aggregate only)"
    else:
        winner = all_sorted[0]
        winner_source = "best overall (no filter passed)"
        print("  WARNING: No combination passed all filters. Using best available.")

    print(f"\n[Step 4] WINNER ({winner_source})")
    print(f"  selection_score={winner['selection_score']:.4f}")
    wp = winner["params"]
    print(
        f"  donchian_period={wp['donchian_period']}  ema_fast={wp['ema_fast']}  "
        f"regime_rsi_min={wp['regime_rsi_min']}  rsi_overheat={wp['rsi_overheat']}"
    )
    print(
        f"  atr_sl_trend_mult={wp['atr_sl_trend_mult']}  atr_trail_mult={wp['atr_trail_mult']}  "
        f"breakeven_r={wp['breakeven_r']}  pullback_band_atr={wp['pullback_band_atr']}"
    )
    agg = winner["aggregate"]
    print("\n  AGGREGATE METRICS:")
    print(f"    TRAIN:  {fmt_metrics(agg['train'])}")
    print(f"    VAL:    {fmt_metrics(agg['val'])}")
    print(f"    TEST:   {fmt_metrics(agg['test'])}")
    for sym in active_symbols:
        bsym = winner["by_symbol"].get(sym, {})
        if bsym:
            print(f"    {sym}: test={fmt_metrics(bsym['test'])}")

    # ------------------------------------------------------------------
    # Summary block for external reading
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  MACHINE-READABLE SUMMARY (for config update)")
    print("=" * 70)
    print(f"WINNER_SCORE={winner['selection_score']}")
    print(f"WINNER_SOURCE={winner_source}")
    print(f"SYMBOLS={','.join(active_symbols)}")
    print(f"TIMEFRAME={SIGNAL_TF}")
    print(f"PHASE={phase_label}")
    print(f"TOTAL_COMBOS={len(all_combined)}")
    print(f"PASSING={len(passing)}")
    print(f"ROBUST_PASSING={len(robust_passing)}")

    for k, v in wp.items():
        print(f"PARAM_{k.upper()}={v}")

    for split in ["train", "val", "test"]:
        m = agg[split]
        for metric, val in m.items():
            print(f"AGG_{split.upper()}_{metric.upper()}={val}")

    # Top 5 for report
    print("\nTOP5:")
    for rank, rec in enumerate(all_sorted[:5], 1):
        rp = rec["params"]
        ra = rec["aggregate"]
        filt = passes_filters(rec)
        rsym = symbols_passing(rec, active_symbols)
        print(
            f"  {rank}| score={rec['selection_score']:.4f}| "
            f"don={rp['donchian_period']} ema={rp['ema_fast']} "
            f"rsi_min={rp['regime_rsi_min']} oh={rp['rsi_overheat']} "
            f"sl={rp['atr_sl_trend_mult']} tr={rp['atr_trail_mult']} "
            f"be={rp['breakeven_r']} pb={rp['pullback_band_atr']}| "
            f"test_ret={ra['test']['net_return_pct']:+.1f}% "
            f"test_dd={ra['test']['max_drawdown_pct']:.1f}% "
            f"test_pf={ra['test']['profit_factor']:.2f}| "
            f"filter={'PASS' if filt else 'FAIL'} robust={rsym}/{len(active_symbols)}"
        )

    print("\n[Done]")
    return winner


if __name__ == "__main__":
    main()
