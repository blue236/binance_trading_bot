#!/usr/bin/env python3
"""QUANT-06: H_V5 Regime Filter Redesign — variant comparison.

Tests 7 regime filter variants (A through G) across BTC/USDT, ETH/USDT, SOL/USDT
on 4h signal bars with 1d daily bars used for regime computation.

Each variant recomputes the regime_flag condition locally without modifying
compute_regime_column() in btb_strategy_research.py.

Primary ranking metric: average val profit_factor across 3 symbols.
Eligibility: val_dd > -25% AND val_trades >= 6 (per variant × symbol aggregate).

Usage:
    python scripts/run_quant06.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable

import ccxt
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

sys.path.insert(0, str(Path(__file__).parent))
from btb_strategy_research import (
    Costs,
    backtest_h_v5,
    eval_multisymbol,
    splits,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SIGNAL_TF = "4h"
DAILY_TF = "1d"
SIGNAL_LIMIT = 4000   # ~2 years of 4h data
DAILY_LIMIT = 1000

# Fixed strategy params (from task specification and config.yaml current values)
PARAMS = {
    "donchian_period": 40,
    "ema_fast": 50,
    "regime_rsi_min": 50,
    "rsi_overheat": 75,
    "atr_sl_trend_mult": 2.5,
    "atr_trail_mult": 8.0,
    "breakeven_r": 1.0,
    "pullback_band_atr": 0.8,
    "ema_slow": 200,
    "regime_ema_fast": 50,
    "regime_rsi_len": 14,
    "cooldown_bars": 2,
    # Required by backtest_h_v5 internals
    "atr_period": 14,
    "rsi_period": 14,
}

# Variant definitions
VARIANTS = [
    {
        "id": "A",
        "name": "Current (baseline)",
        "desc": "ALL 4 conditions: price>EMA200 AND slope>0 AND EMA50>EMA200 AND RSI>=50",
        "conditions": ["price_above_ema200", "ema200_slope", "golden_cross", "rsi_min"],
    },
    {
        "id": "B",
        "name": "No golden cross",
        "desc": "Conditions 1+2+4: price>EMA200 AND slope>0 AND RSI>=50",
        "conditions": ["price_above_ema200", "ema200_slope", "rsi_min"],
    },
    {
        "id": "C",
        "name": "Price + RSI only",
        "desc": "Conditions 1+4: price>EMA200 AND RSI>=50",
        "conditions": ["price_above_ema200", "rsi_min"],
    },
    {
        "id": "D",
        "name": "RSI only",
        "desc": "Condition 4 only: RSI>=50",
        "conditions": ["rsi_min"],
    },
    {
        "id": "E",
        "name": "Price only",
        "desc": "Condition 1 only: price>EMA200",
        "conditions": ["price_above_ema200"],
    },
    {
        "id": "F",
        "name": "Unfiltered",
        "desc": "Always 'trend' — no regime filter",
        "conditions": [],
    },
    {
        "id": "G",
        "name": "Slope + RSI",
        "desc": "Conditions 2+4: EMA200 rising AND RSI>=50",
        "conditions": ["ema200_slope", "rsi_min"],
    },
]


# ---------------------------------------------------------------------------
# Data fetch (copied from run_quant03.py)
# ---------------------------------------------------------------------------

def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame | None:
    """Fetch OHLCV bars from Binance, paginating backwards to collect `limit` bars."""
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        page_size = 1000
        all_rows: list = []
        since = None

        while len(all_rows) < limit:
            if since is not None:
                raw = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=page_size)
            else:
                raw = exchange.fetch_ohlcv(symbol, timeframe, limit=page_size)

            if not raw:
                break

            all_rows = raw + all_rows
            if len(raw) < page_size:
                break

            earliest_ts = raw[0][0]
            tf_ms = exchange.parse_timeframe(timeframe) * 1000
            since = earliest_ts - page_size * tf_ms

            if len(all_rows) >= limit:
                break

        if len(all_rows) > limit:
            all_rows = all_rows[-limit:]

        df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        df = df[df["close"] > 0].copy()
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
# Local regime computation with variant support
# ---------------------------------------------------------------------------

def compute_regime_variant(
    df_signal: pd.DataFrame,
    df_daily: pd.DataFrame,
    p: dict,
    conditions: list[str],
) -> pd.Series:
    """Build a regime Series for df_signal using only the specified conditions.

    Each condition name maps to one of the 4 legs of the baseline filter:
      'price_above_ema200' : close > EMA(200) on daily
      'ema200_slope'       : EMA(200).diff() > 0 (rising slope)
      'golden_cross'       : EMA(50) > EMA(200)
      'rsi_min'            : RSI(14) >= regime_rsi_min

    When conditions is empty (variant F), all bars are 'trend'.
    All indicators are shifted by 1 bar (uses previous completed daily bar only).
    Merge is forward-fill by timestamp (same as compute_regime_column).
    """
    # Unfiltered variant — skip all indicator computation
    if not conditions:
        return pd.Series("trend", index=df_signal.index)

    ema_slow_len = int(p.get("ema_slow", 200))
    regime_fast_len = int(p.get("regime_ema_fast", 50))
    regime_rsi_len = int(p.get("regime_rsi_len", 14))
    regime_rsi_min = float(p.get("regime_rsi_min", 50))

    d = df_daily.copy().reset_index(drop=True)

    if len(d) < ema_slow_len + 2:
        print(
            f"  [regime] insufficient daily data ({len(d)} bars < {ema_slow_len + 2}), "
            "regime filter disabled"
        )
        return pd.Series("trend", index=df_signal.index)

    ema200 = EMAIndicator(d["close"], ema_slow_len).ema_indicator()
    rsi_d = RSIIndicator(d["close"], regime_rsi_len).rsi()

    # Shift by 1 — only use the previous completed daily bar at each signal bar.
    close_prev = d["close"].shift(1)
    ema200_prev = ema200.shift(1)
    ema200_slope_prev = ema200.diff().shift(1)
    rsi_d_prev = rsi_d.shift(1)

    # Build flag as conjunction of requested conditions only
    flag = pd.Series(True, index=d.index)

    if "price_above_ema200" in conditions:
        flag = flag & (close_prev > ema200_prev)

    if "ema200_slope" in conditions:
        flag = flag & (ema200_slope_prev > 0)

    if "golden_cross" in conditions:
        ema50 = EMAIndicator(d["close"], regime_fast_len).ema_indicator()
        ema50_prev = ema50.shift(1)
        flag = flag & (ema50_prev > ema200_prev)

    if "rsi_min" in conditions:
        flag = flag & (rsi_d_prev >= regime_rsi_min)

    d["_regime"] = flag.map({True: "trend", False: "none"})
    d["_ts"] = d["ts"].astype(np.int64)

    sig = df_signal[["ts"]].copy()
    sig["_ts_sig"] = sig["ts"].astype(np.int64)

    daily_regime = d[["_ts", "_regime"]].dropna(subset=["_regime"]).copy()
    daily_regime = daily_regime.sort_values("_ts").reset_index(drop=True)
    sig_sorted = sig.sort_values("_ts_sig").reset_index()

    merged = pd.merge_asof(
        sig_sorted,
        daily_regime.rename(columns={"_ts": "_ts_sig"}),
        on="_ts_sig",
        direction="backward",
    )
    merged = merged.set_index("index").sort_index()

    result = merged["_regime"].fillna("none")
    result.index = df_signal.index
    return result


# ---------------------------------------------------------------------------
# Per-variant evaluation
# ---------------------------------------------------------------------------

def evaluate_variant(
    variant: dict,
    signal_data: dict[str, pd.DataFrame],
    daily_data: dict[str, pd.DataFrame],
    params: dict,
) -> dict:
    """Evaluate a single regime variant across all symbols.

    Returns dict with:
        - variant metadata (id, name, desc)
        - per_symbol: {symbol: {train/val/test metrics, trend_pct}}
        - aggregate: {train/val/test combined metrics}
        - avg_trend_pct: mean trend_pct across symbols
        - avg_val_pf: mean val profit_factor across symbols (primary rank metric)
    """
    conditions = variant["conditions"]
    per_symbol: dict[str, dict] = {}
    trend_pcts: list[float] = []

    # Build merged signal dfs with regime column for this variant
    merged_dfs: dict[str, pd.DataFrame] = {}
    for symbol, df_sig in signal_data.items():
        df = df_sig.copy()
        if symbol in daily_data:
            regime_series = compute_regime_variant(df, daily_data[symbol], params, conditions)
        else:
            regime_series = pd.Series("trend", index=df.index)
        df["regime"] = regime_series
        trend_pct = (regime_series == "trend").mean() * 100.0
        trend_pcts.append(trend_pct)
        merged_dfs[symbol] = df

    # Run backtest across all symbols
    by_symbol, agg = eval_multisymbol(merged_dfs, backtest_h_v5, params)

    # Enrich per_symbol with trend_pct
    for i, symbol in enumerate(signal_data.keys()):
        sym_res = by_symbol.get(symbol, {})
        sym_res["trend_pct"] = round(trend_pcts[i], 1)
        per_symbol[symbol] = sym_res

    # Average val profit_factor across symbols (primary rank key)
    val_pfs = [per_symbol[s]["val"]["profit_factor"] for s in signal_data if s in per_symbol]
    avg_val_pf = round(float(np.mean(val_pfs)) if val_pfs else 0.0, 3)
    avg_trend_pct = round(float(np.mean(trend_pcts)) if trend_pcts else 0.0, 1)

    return {
        "id": variant["id"],
        "name": variant["name"],
        "desc": variant["desc"],
        "per_symbol": per_symbol,
        "aggregate": agg,
        "avg_trend_pct": avg_trend_pct,
        "avg_val_pf": avg_val_pf,
    }


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------

def is_eligible(result: dict) -> bool:
    """Variant is eligible as winner if: val_dd > -25% AND val_trades >= 6."""
    agg_val = result["aggregate"]["val"]
    return (
        agg_val["max_drawdown_pct"] > -25.0
        and agg_val["trade_count"] >= 6
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def fmt_metrics(m: dict) -> str:
    return (
        f"ret={m['net_return_pct']:+.1f}%  "
        f"dd={m['max_drawdown_pct']:.1f}%  "
        f"wr={m['win_rate_pct']:.0f}%  "
        f"tr={m['trade_count']}  "
        f"pf={m['profit_factor']:.3f}"
    )


def print_results_table(results: list[dict], symbols: list[str]) -> None:
    print("\n" + "=" * 80)
    print("  QUANT-06: Regime Filter Variant Comparison Results")
    print("=" * 80)

    # Header
    header = (
        f"{'ID':<3}  {'Variant':<22}  {'TrendPct':>8}  "
        f"{'ValRet':>8}  {'ValDD':>7}  {'ValWR':>6}  "
        f"{'ValTr':>6}  {'ValPF':>7}  {'Eligible':>8}"
    )
    print("\n" + header)
    print("-" * 80)

    for r in results:
        agg_val = r["aggregate"]["val"]
        elig = "PASS" if is_eligible(r) else "FAIL"
        print(
            f"{r['id']:<3}  {r['name']:<22}  {r['avg_trend_pct']:>7.1f}%  "
            f"{agg_val['net_return_pct']:>+7.1f}%  "
            f"{agg_val['max_drawdown_pct']:>6.1f}%  "
            f"{agg_val['win_rate_pct']:>5.0f}%  "
            f"{agg_val['trade_count']:>6}  "
            f"{agg_val['profit_factor']:>7.3f}  "
            f"{elig:>8}"
        )

    print()

    # Per-symbol detail
    print("\n  Per-symbol val metrics:")
    print("-" * 80)
    for r in results:
        print(f"\n  [{r['id']}] {r['name']}")
        for sym in symbols:
            ps = r["per_symbol"].get(sym)
            if ps:
                val = ps["val"]
                print(
                    f"       {sym:<12}  trend={ps['trend_pct']:5.1f}%  "
                    f"{fmt_metrics(val)}"
                )


def print_winner(winner: dict | None, results: list[dict]) -> None:
    print("\n" + "=" * 80)
    print("  WINNER SELECTION")
    print("=" * 80)

    if winner is None:
        print("  No variant passed eligibility (val_dd > -25% AND val_trades >= 6).")
        # Fall back to best avg_val_pf regardless
        best = max(results, key=lambda r: r["avg_val_pf"])
        print(f"  Best available (no filter): [{best['id']}] {best['name']}")
        print(f"  avg_val_pf={best['avg_val_pf']:.3f}  avg_trend_pct={best['avg_trend_pct']:.1f}%")
        return

    print(f"\n  WINNER: [{winner['id']}] {winner['name']}")
    print(f"  Description: {winner['desc']}")
    print(f"  avg_val_pf={winner['avg_val_pf']:.3f}  avg_trend_pct={winner['avg_trend_pct']:.1f}%")
    print(f"\n  Aggregate metrics:")
    print(f"    TRAIN:  {fmt_metrics(winner['aggregate']['train'])}")
    print(f"    VAL:    {fmt_metrics(winner['aggregate']['val'])}")
    print(f"    TEST:   {fmt_metrics(winner['aggregate']['test'])}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> dict:
    print("\n" + "=" * 80)
    print("  QUANT-06: H_V5 Regime Filter Redesign")
    print(f"  Symbols: {SYMBOLS}  |  Signal TF: {SIGNAL_TF}  |  Regime TF: {DAILY_TF}")
    print(f"  Signal bars: {SIGNAL_LIMIT}  |  Daily bars: {DAILY_LIMIT}")
    print("=" * 80)

    # ------------------------------------------------------------------
    # Step 1 — Fetch data
    # ------------------------------------------------------------------
    print("\n[Step 1] Fetching OHLCV data from Binance ...")
    signal_data: dict[str, pd.DataFrame] = {}
    daily_data: dict[str, pd.DataFrame] = {}

    for sym in SYMBOLS:
        sig = fetch_ohlcv(sym, SIGNAL_TF, SIGNAL_LIMIT)
        if sig is not None and len(sig) >= 200:
            signal_data[sym] = sig
        else:
            print(f"  [skip] {sym} signal data insufficient — skipped")

        day = fetch_ohlcv(sym, DAILY_TF, DAILY_LIMIT)
        if day is not None and len(day) >= 202:
            daily_data[sym] = day
        else:
            print(f"  [warn] {sym} daily data insufficient — regime filter disabled for this symbol")

    if not signal_data:
        print("[ERROR] No usable signal data. Aborting.")
        sys.exit(1)

    active_symbols = list(signal_data.keys())
    print(f"\n  Active symbols: {active_symbols}")
    if daily_data:
        # Print date ranges
        for sym, df in signal_data.items():
            sp = splits(len(df))
            n = len(df)
            val_from = df["dt"].iloc[sp["val"][0]].date()
            val_to = df["dt"].iloc[sp["val"][1] - 1].date()
            test_from = df["dt"].iloc[sp["test"][0]].date()
            test_to = df["dt"].iloc[n - 1].date()
            print(f"    {sym}: {n} bars  val=[{val_from}..{val_to}]  test=[{test_from}..{test_to}]")

    # ------------------------------------------------------------------
    # Step 2 — Evaluate each variant
    # ------------------------------------------------------------------
    print(f"\n[Step 2] Evaluating {len(VARIANTS)} regime filter variants ...")
    results: list[dict] = []
    t0 = time.time()

    for variant in VARIANTS:
        print(f"  [{variant['id']}] {variant['name']} ...")
        t_var = time.time()
        res = evaluate_variant(variant, signal_data, daily_data, PARAMS)
        results.append(res)
        elapsed = time.time() - t_var
        agg_val = res["aggregate"]["val"]
        print(
            f"       done in {elapsed:.1f}s  "
            f"avg_trend_pct={res['avg_trend_pct']:.1f}%  "
            f"val_pf={agg_val['profit_factor']:.3f}  "
            f"val_trades={agg_val['trade_count']}"
        )

    total_elapsed = time.time() - t0
    print(f"\n  All variants completed in {total_elapsed:.1f}s")

    # ------------------------------------------------------------------
    # Step 3 — Report results
    # ------------------------------------------------------------------
    print_results_table(results, active_symbols)

    # ------------------------------------------------------------------
    # Step 4 — Select winner
    # ------------------------------------------------------------------
    print("\n[Step 4] Selecting winner ...")
    eligible = [r for r in results if is_eligible(r)]
    print(f"  Eligible variants (val_dd > -25% AND val_trades >= 6): {len(eligible)}/{len(results)}")

    winner: dict | None = None
    if eligible:
        # Primary: highest avg val profit_factor
        winner = max(eligible, key=lambda r: r["avg_val_pf"])

    print_winner(winner, results)

    # ------------------------------------------------------------------
    # Step 5 — Machine-readable summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  MACHINE-READABLE SUMMARY")
    print("=" * 80)
    print(f"SYMBOLS={','.join(active_symbols)}")
    print(f"SIGNAL_TF={SIGNAL_TF}")
    print(f"SIGNAL_LIMIT={SIGNAL_LIMIT}")
    print(f"DAILY_LIMIT={DAILY_LIMIT}")
    print(f"ELIGIBLE_VARIANTS={len(eligible)}")

    if winner:
        print(f"WINNER_ID={winner['id']}")
        print(f"WINNER_NAME={winner['name']}")
        print(f"WINNER_AVG_TREND_PCT={winner['avg_trend_pct']}")
        print(f"WINNER_AVG_VAL_PF={winner['avg_val_pf']}")
        for split in ["train", "val", "test"]:
            m = winner["aggregate"][split]
            for metric, val in m.items():
                print(f"WINNER_{split.upper()}_{metric.upper()}={val}")
    else:
        print("WINNER_ID=NONE")

    print("\nALL_VARIANTS:")
    for r in results:
        agg_val = r["aggregate"]["val"]
        agg_test = r["aggregate"]["test"]
        elig = is_eligible(r)
        print(
            f"  {r['id']}|{r['name']}|"
            f"trend_pct={r['avg_trend_pct']:.1f}|"
            f"val_ret={agg_val['net_return_pct']:+.1f}|"
            f"val_dd={agg_val['max_drawdown_pct']:.1f}|"
            f"val_wr={agg_val['win_rate_pct']:.1f}|"
            f"val_tr={agg_val['trade_count']}|"
            f"val_pf={agg_val['profit_factor']:.3f}|"
            f"test_ret={agg_test['net_return_pct']:+.1f}|"
            f"test_dd={agg_test['max_drawdown_pct']:.1f}|"
            f"test_tr={agg_test['trade_count']}|"
            f"test_pf={agg_test['profit_factor']:.3f}|"
            f"eligible={elig}"
        )

    print("\n[Done]")
    return {"winner": winner, "results": results}


if __name__ == "__main__":
    main()
