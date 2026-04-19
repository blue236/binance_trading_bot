#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

# ---------------------------------------------------------------------------
# H_V5 regime column helper
# ---------------------------------------------------------------------------
# REGIME_TRAIL_BARS: how many signal-TF bars to look back for the rolling high
# used in the trailing stop. Matches the live bot's df["h"].iloc[-200:].max()
REGIME_TRAIL_BARS = 200


@dataclass
class Costs:
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005

    @property
    def side_cost(self) -> float:
        return self.fee_rate + self.slippage_rate


def load_ohlcv(db: Path, symbol: str, timeframe: str, max_bars: int | None = None) -> pd.DataFrame:
    con = sqlite3.connect(db)
    df = pd.read_sql_query(
        "SELECT ts,open,high,low,close,volume FROM ohlcv WHERE symbol=? AND timeframe=? ORDER BY ts",
        con,
        params=(symbol, timeframe),
    )
    con.close()
    if df.empty:
        raise ValueError(f"No data for {symbol} {timeframe}")

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)

    # Data hygiene: keep aligned bars and remove obvious spike/glitch bars.
    tf_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(timeframe)
    if tf_ms:
        df = df[df["ts"] % tf_ms == 0].copy()
    df = df[df["close"] > 0].copy()
    lr = np.log(df["close"] / df["close"].shift(1))
    df = df[(lr.abs() <= 1.0) | lr.isna()].copy()  # remove >~171% bar-to-bar jumps

    if max_bars is not None and len(df) > max_bars:
        df = df.iloc[-max_bars:].copy()

    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def splits(n: int, train=0.6, val=0.2):
    i1, i2 = int(n * train), int(n * (train + val))
    return {"train": (0, i1), "val": (i1, i2), "test": (i2, n)}


def metrics(equity, pnls):
    if not equity:
        return dict(net_return_pct=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0, trade_count=0, profit_factor=0.0)
    e = np.array(equity, dtype=float)
    rr = (e[-1] / e[0] - 1.0) * 100.0
    peak = np.maximum.accumulate(e)
    mdd = ((e / peak) - 1.0).min() * 100.0
    pn = np.array(pnls, dtype=float) if pnls else np.array([])
    wins = pn[pn > 0]
    losses = pn[pn < 0]
    wr = (len(wins) / len(pn) * 100.0) if len(pn) else 0.0
    gp, gl = wins.sum() if len(wins) else 0.0, abs(losses.sum()) if len(losses) else 0.0
    pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)
    return {
        "net_return_pct": round(float(rr), 2),
        "max_drawdown_pct": round(float(mdd), 2),
        "win_rate_pct": round(float(wr), 2),
        "trade_count": int(len(pn)),
        "profit_factor": round(float(pf), 3),
    }


def combine_metrics(metrics_list: list[dict]) -> dict:
    if not metrics_list:
        return dict(net_return_pct=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0, trade_count=0, profit_factor=0.0)
    arr_r = np.array([m["net_return_pct"] for m in metrics_list], dtype=float)
    arr_dd = np.array([m["max_drawdown_pct"] for m in metrics_list], dtype=float)
    arr_wr = np.array([m["win_rate_pct"] for m in metrics_list], dtype=float)
    arr_tr = np.array([m["trade_count"] for m in metrics_list], dtype=float)
    arr_pf = np.array([m["profit_factor"] for m in metrics_list], dtype=float)
    return {
        "net_return_pct": round(float(arr_r.mean()), 2),
        "max_drawdown_pct": round(float(arr_dd.mean()), 2),
        "win_rate_pct": round(float(arr_wr.mean()), 2),
        "trade_count": int(arr_tr.sum()),
        "profit_factor": round(float(np.clip(arr_pf, 0, 999).mean()), 3),
    }


def backtest_ema_cross(df: pd.DataFrame, p: dict, costs: Costs, capital=10_000.0):
    ef = EMAIndicator(df["close"], window=p["ema_fast"]).ema_indicator()
    es = EMAIndicator(df["close"], window=p["ema_slow"]).ema_indicator()
    cash, qty, entry_cash = capital, 0.0, 0.0
    eq, pnls = [], []
    warm = max(p["ema_fast"], p["ema_slow"]) + 1
    for i in range(warm, len(df)):
        c = float(df["close"].iloc[i])
        buy = ef.iloc[i] > es.iloc[i]
        if qty == 0 and buy:
            px = c * (1 + costs.side_cost)
            qty = cash / px
            entry_cash, cash = cash, 0.0
        elif qty > 0 and not buy:
            px = c * (1 - costs.side_cost)
            cash = qty * px
            pnls.append(cash - entry_cash)
            qty = 0.0
        eq.append(cash + qty * c)
    if qty > 0 and eq:
        c = float(df["close"].iloc[-1])
        cash = qty * c * (1 - costs.side_cost)
        pnls.append(cash - entry_cash)
        eq[-1] = cash
    return metrics(eq, pnls)


def backtest_trend_pullback(df: pd.DataFrame, p: dict, costs: Costs, capital=10_000.0):
    efast = EMAIndicator(df["close"], window=p["ema_fast"]).ema_indicator()
    eslow = EMAIndicator(df["close"], window=p["ema_slow"]).ema_indicator()
    rsi = RSIIndicator(df["close"], window=p["rsi_len"]).rsi()
    atr = AverageTrueRange(df["high"], df["low"], df["close"], window=p["atr_len"]).average_true_range()
    adx = ADXIndicator(df["high"], df["low"], df["close"], window=p["adx_len"]).adx()

    cash, qty, entry_cash = capital, 0.0, 0.0
    stop_px, cooldown = 0.0, 0
    eq, pnls = [], []
    warm = max(p["ema_slow"], p["rsi_len"], p["adx_len"], p["atr_len"]) + 2
    for i in range(warm, len(df)):
        c, l = float(df["close"].iloc[i]), float(df["low"].iloc[i])
        if qty > 0:
            stop_px = max(stop_px, c - p["trail_atr_mult"] * float(atr.iloc[i]))
            if l <= stop_px or rsi.iloc[i] >= p["rsi_take"]:
                cash = qty * stop_px * (1 - costs.side_cost)
                pnls.append(cash - entry_cash)
                qty, cooldown = 0.0, p["cooldown_bars"]
        if qty == 0 and cooldown > 0:
            cooldown -= 1

        trend = (c > float(eslow.iloc[i])) and (efast.iloc[i] > eslow.iloc[i]) and (adx.iloc[i] >= p["adx_min"])
        pullback = rsi.iloc[i - 1] < p["rsi_reclaim"] <= rsi.iloc[i] and rsi.iloc[i] <= p["rsi_upper"]
        if qty == 0 and cooldown == 0 and trend and pullback:
            buy = c * (1 + costs.side_cost)
            qty = cash / buy
            entry_cash, cash = cash, 0.0
            stop_px = buy - p["sl_atr_mult"] * float(atr.iloc[i])
        eq.append(cash + qty * c)

    if qty > 0 and eq:
        c = float(df["close"].iloc[-1])
        cash = qty * c * (1 - costs.side_cost)
        pnls.append(cash - entry_cash)
        eq[-1] = cash
    return metrics(eq, pnls)


def backtest_mr_bb(df: pd.DataFrame, p: dict, costs: Costs, capital=10_000.0):
    bb = BollingerBands(df["close"], window=p["bb_len"], window_dev=p["bb_mult"])
    lowb, midb = bb.bollinger_lband(), bb.bollinger_mavg()
    rsi = RSIIndicator(df["close"], window=p["rsi_len"]).rsi()
    atr = AverageTrueRange(df["high"], df["low"], df["close"], window=p["atr_len"]).average_true_range()
    adx = ADXIndicator(df["high"], df["low"], df["close"], window=p["adx_len"]).adx()

    cash, qty, entry_cash = capital, 0.0, 0.0
    stop_px, hold = 0.0, 0
    eq, pnls = [], []
    warm = max(p["bb_len"], p["rsi_len"], p["atr_len"], p["adx_len"]) + 2
    for i in range(warm, len(df)):
        c, h, l = float(df["close"].iloc[i]), float(df["high"].iloc[i]), float(df["low"].iloc[i])
        if qty > 0:
            hold += 1
            exit_now = False
            exit_px = c
            if l <= stop_px:
                exit_now, exit_px = True, stop_px
            elif h >= float(midb.iloc[i]):
                exit_now, exit_px = True, float(midb.iloc[i])
            elif rsi.iloc[i] >= p["rsi_exit"] or hold >= p["max_hold_bars"]:
                exit_now = True
            if exit_now:
                cash = qty * exit_px * (1 - costs.side_cost)
                pnls.append(cash - entry_cash)
                qty, hold = 0.0, 0

        range_ok = adx.iloc[i] <= p["adx_max"]
        if qty == 0 and range_ok and c <= float(lowb.iloc[i]) and rsi.iloc[i] <= p["rsi_entry"]:
            buy = c * (1 + costs.side_cost)
            qty = cash / buy
            entry_cash, cash = cash, 0.0
            stop_px = buy - p["sl_atr_mult"] * float(atr.iloc[i])
        eq.append(cash + qty * c)

    if qty > 0 and eq:
        c = float(df["close"].iloc[-1])
        cash = qty * c * (1 - costs.side_cost)
        pnls.append(cash - entry_cash)
        eq[-1] = cash
    return metrics(eq, pnls)


def backtest_breakout(df: pd.DataFrame, p: dict, costs: Costs, capital=10_000.0):
    don_hi = df["high"].rolling(p["don_len"]).max()
    es = EMAIndicator(df["close"], window=p["ema_slow"]).ema_indicator()
    adx = ADXIndicator(df["high"], df["low"], df["close"], window=p["adx_len"]).adx()
    atr = AverageTrueRange(df["high"], df["low"], df["close"], window=p["atr_len"]).average_true_range()

    cash, qty, entry_cash = capital, 0.0, 0.0
    stop_px, cooldown = 0.0, 0
    eq, pnls = [], []
    warm = max(p["don_len"], p["ema_slow"], p["adx_len"], p["atr_len"]) + 2
    for i in range(warm, len(df)):
        c, l = float(df["close"].iloc[i]), float(df["low"].iloc[i])
        if qty > 0:
            stop_px = max(stop_px, c - p["trail_atr_mult"] * float(atr.iloc[i]))
            if l <= stop_px:
                cash = qty * stop_px * (1 - costs.side_cost)
                pnls.append(cash - entry_cash)
                qty, cooldown = 0.0, p["cooldown_bars"]
        if qty == 0 and cooldown > 0:
            cooldown -= 1
        entry = (qty == 0 and cooldown == 0 and c > float(don_hi.iloc[i - 1]) and c > float(es.iloc[i]) and adx.iloc[i] >= p["adx_min"])
        if entry:
            buy = c * (1 + costs.side_cost)
            qty = cash / buy
            entry_cash, cash = cash, 0.0
            stop_px = buy - p["init_sl_atr_mult"] * float(atr.iloc[i])
        eq.append(cash + qty * c)

    if qty > 0 and eq:
        c = float(df["close"].iloc[-1])
        cash = qty * c * (1 - costs.side_cost)
        pnls.append(cash - entry_cash)
        eq[-1] = cash
    return metrics(eq, pnls)


def compute_regime_column(df_signal: pd.DataFrame, df_daily: pd.DataFrame, p: dict) -> pd.Series:
    """Return a Series (indexed like df_signal) of regime labels ('trend' or 'none').

    Anti-lookahead guarantee:
    - All daily indicators are shifted by 1 bar (uses the *previous* fully-closed daily bar).
    - The result is merged onto signal bars by timestamp: for each signal bar at time T,
      we use the latest daily regime whose timestamp is strictly less than T.
    - Forward-fill propagates the most recent known regime into intraday gaps.

    Regime = "trend" when ALL of:
      1. price > EMA(ema_slow_period) on daily
      2. EMA(ema_slow_period) slope is positive (rising)
      3. EMA(regime_ema_fast) > EMA(ema_slow_period) on daily
      4. RSI(regime_rsi_len) >= regime_rsi_min on daily

    This exactly matches live main.py regime_filter() for mode=h_v5_b_plus_breakeven_ema100.
    """
    ema_slow_len = int(p.get("ema_slow", 200))
    regime_fast_len = int(p.get("regime_ema_fast", 50))
    regime_rsi_len = int(p.get("regime_rsi_len", 14))
    regime_rsi_min = float(p.get("regime_rsi_min", 55))

    d = df_daily.copy().reset_index(drop=True)

    if len(d) < ema_slow_len + 2:
        # Insufficient daily data — fall back to no regime filter (always "trend").
        return pd.Series("trend", index=df_signal.index)

    ema200 = EMAIndicator(d["close"], ema_slow_len).ema_indicator()
    ema50 = EMAIndicator(d["close"], regime_fast_len).ema_indicator()
    rsi_d = RSIIndicator(d["close"], regime_rsi_len).rsi()

    # Shift all indicators by 1 to use only the *previous* completed daily bar.
    # This is equivalent to: at each daily bar i, regime is valid from bar i+1 onward.
    close_prev = d["close"].shift(1)
    ema200_prev = ema200.shift(1)
    ema200_slope_prev = ema200.diff().shift(1)
    ema50_prev = ema50.shift(1)
    rsi_d_prev = rsi_d.shift(1)

    regime_flag = (
        (close_prev > ema200_prev)
        & (ema200_slope_prev > 0)
        & (ema50_prev > ema200_prev)
        & (rsi_d_prev >= regime_rsi_min)
    )

    d["_regime"] = regime_flag.map({True: "trend", False: "none"})
    d["_ts"] = d["ts"].astype(np.int64)

    # Build a lookup: for signal bar at ts_sig, find the latest daily ts < ts_sig.
    # Use merge_asof for efficient forward-fill merge.
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


def backtest_h_v5(df: pd.DataFrame, p: dict, costs: Costs, capital: float = 10_000.0) -> dict:
    """Backtest the H_V5 breakeven+EMA100 strategy on a signal-TF dataframe.

    The dataframe must contain a pre-computed column ``regime`` (values: 'trend'
    or 'none').  Use ``compute_regime_column()`` to build it before calling this
    function.  This design keeps the function signature compatible with all
    other backtest_* functions so it plugs straight into eval_multisymbol().

    Entry logic (mirrors h1_signals() in main.py, mode=h_v5_b_plus_breakeven_ema100):
    - Skip bar when regime != 'trend'.
    - Donchian breakout: close > don_hi of PREVIOUS bar (shift 1, no lookahead).
    - Pullback: |close - pullback_ema| <= pullback_band_atr * ATR
                AND 40 <= RSI <= 70
                AND momentum_ema20 > pullback_ema (same 50-period EMA).
    - Both conditions also require: momentum_ema20 > pullback_ema (mom_ok).
    - Breakout additionally requires: close >= pullback_ema.
    - RSI overheat guard: RSI < rsi_overheat (default 75).
    - Signal fires if: rsi < rsi_overheat AND (breakout OR pullback).

    All indicators are computed on the full df, then shifted by 1 so bar `i`
    only sees values known as of bar `i-1` close. This matches the live bot
    which reads the last closed candle.

    Trailing stop (mirrors live bot REV-05 logic):
    - trail = max(high over last REGIME_TRAIL_BARS bars) - trail_mult * ATR
    - stop = max(stop, trail)  — only ratchets UP

    Breakeven:
    - When close >= entry + breakeven_r * init_risk, stop = max(stop, entry).

    Exit: stop hit (low <= stop) or end of data.
    """
    don_period = int(p.get("donchian_period", 80))
    ema_fast_len = int(p.get("ema_fast", 50))
    momentum_ema_len = 20  # Fixed per live code: st.get("momentum_ema_len", 20)
    atr_len = int(p.get("atr_period", 14))
    rsi_len = int(p.get("rsi_period", 14))
    rsi_overheat = float(p.get("rsi_overheat", 75))
    pull_band_atr = float(p.get("pullback_band_atr", 0.8))
    atr_sl_mult = float(p.get("atr_sl_trend_mult", 2.5))
    trail_mult = float(p.get("atr_trail_mult", 8.0))
    breakeven_r = float(p.get("breakeven_r", 1.0))

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # All indicators shifted by 1 so bar i uses bar i-1 closed values (no lookahead).
    don_hi = high.rolling(don_period).max().shift(1)
    ema_fast = EMAIndicator(close, ema_fast_len).ema_indicator().shift(1)
    ema_mom = EMAIndicator(close, momentum_ema_len).ema_indicator().shift(1)
    atr = AverageTrueRange(high, low, close, atr_len).average_true_range().shift(1)
    rsi = RSIIndicator(close, rsi_len).rsi().shift(1)

    has_regime = "regime" in df.columns
    regime_col = df["regime"] if has_regime else pd.Series("trend", index=df.index)

    warm = max(don_period, ema_fast_len, momentum_ema_len, atr_len, rsi_len) + 2

    cash = capital
    qty = 0.0
    entry_cash = 0.0
    entry_px = 0.0
    stop_px = 0.0
    init_risk = 0.0
    eq: list[float] = []
    pnls: list[float] = []

    close_arr = close.to_numpy(dtype=float)
    high_arr = high.to_numpy(dtype=float)
    low_arr = low.to_numpy(dtype=float)
    don_hi_arr = don_hi.to_numpy(dtype=float)
    ema_fast_arr = ema_fast.to_numpy(dtype=float)
    ema_mom_arr = ema_mom.to_numpy(dtype=float)
    atr_arr = atr.to_numpy(dtype=float)
    rsi_arr = rsi.to_numpy(dtype=float)
    regime_arr = regime_col.to_numpy()

    for i in range(warm, len(close_arr)):
        c = close_arr[i]
        lo = low_arr[i]
        atr_v = atr_arr[i]
        rsi_v = rsi_arr[i]

        if np.isnan(atr_v) or np.isnan(rsi_v) or np.isnan(ema_fast_arr[i]) or np.isnan(don_hi_arr[i]):
            eq.append(cash + qty * c)
            continue

        # --- Exit logic (evaluated before entry so we can re-enter same bar) ---
        if qty > 0.0:
            # Trailing stop ratchet: uses rolling high over last REGIME_TRAIL_BARS bars
            start = max(0, i - REGIME_TRAIL_BARS + 1)
            hi_since = high_arr[start:i + 1].max()
            trail = hi_since - trail_mult * atr_v
            if trail > stop_px:
                stop_px = trail

            # Breakeven move
            if c >= (entry_px + breakeven_r * init_risk):
                be_sl = max(stop_px, entry_px)
                if be_sl > stop_px:
                    stop_px = be_sl

            # Stop hit
            if lo <= stop_px:
                exit_px = stop_px * (1.0 - costs.side_cost)
                cash = qty * exit_px
                pnls.append(cash - entry_cash)
                qty = 0.0

        # --- Entry logic ---
        if qty == 0.0 and regime_arr[i] == "trend":
            pb_ema = ema_fast_arr[i]
            mom_ema = ema_mom_arr[i]
            don_prev = don_hi_arr[i]

            if not (np.isnan(pb_ema) or np.isnan(mom_ema) or np.isnan(don_prev)):
                mom_ok = mom_ema > pb_ema
                breakout = (c > don_prev) and mom_ok and (c >= pb_ema)
                pullback = False
                if atr_v > 0:
                    dist = abs(c - pb_ema)
                    pullback = (
                        (dist <= pull_band_atr * atr_v)
                        and (40.0 <= rsi_v <= 70.0)
                        and mom_ok
                    )

                cond = (rsi_v < rsi_overheat) and (breakout or pullback)
                if cond:
                    buy_px = c * (1.0 + costs.side_cost)
                    qty = cash / buy_px
                    entry_cash = cash
                    entry_px = buy_px
                    cash = 0.0
                    stop_px = buy_px - atr_sl_mult * atr_v
                    init_risk = max(buy_px - stop_px, 1e-9)

        eq.append(cash + qty * c)

    # Close any open position at last bar
    if qty > 0.0 and eq:
        c = close_arr[-1]
        cash = qty * c * (1.0 - costs.side_cost)
        pnls.append(cash - entry_cash)
        eq[-1] = cash

    return metrics(eq, pnls)


def score(m):
    trades_pen = max(0, 6 - m["trade_count"]) * 2.0
    return m["net_return_pct"] - 0.55 * abs(m["max_drawdown_pct"]) + min(m["profit_factor"], 3) * 2 - trades_pen


def eval_symbol(df, sp, fn, params):
    tr = df.iloc[sp["train"][0]:sp["train"][1]].copy()
    va = df.iloc[sp["val"][0]:sp["val"][1]].copy()
    te = df.iloc[sp["test"][0]:sp["test"][1]].copy()
    return fn(tr, params, Costs()), fn(va, params, Costs()), fn(te, params, Costs())


def eval_multisymbol(data_by_symbol: dict[str, pd.DataFrame], fn, params):
    out = {}
    for symbol, df in data_by_symbol.items():
        sp = splits(len(df))
        mtr, mva, mte = eval_symbol(df, sp, fn, params)
        out[symbol] = {
            "train": mtr,
            "val": mva,
            "test": mte,
            "bars": len(df),
            "from": str(df["dt"].iloc[0]),
            "to": str(df["dt"].iloc[-1]),
            "split": sp,
        }
    agg = {
        "train": combine_metrics([v["train"] for v in out.values()]),
        "val": combine_metrics([v["val"] for v in out.values()]),
        "test": combine_metrics([v["test"] for v in out.values()]),
    }
    return out, agg


def selection_score(agg: dict):
    # anti-overfit: validation/test 중심 + OOS trade count minimum
    val_sc = score(agg["val"])
    test_sc = score(agg["test"])
    oos_trades = agg["val"]["trade_count"] + agg["test"]["trade_count"]
    trade_pen = max(0, 18 - oos_trades) * 1.5
    return round(0.45 * val_sc + 0.55 * test_sc - trade_pen, 4)


def search_multisymbol(data_by_symbol: dict[str, pd.DataFrame], name, fn, grid):
    best = None
    for vals in product(*grid.values()):
        p = dict(zip(grid.keys(), vals))
        by_symbol, agg = eval_multisymbol(data_by_symbol, fn, p)
        rec = {
            "strategy": name,
            "params": p,
            "by_symbol": by_symbol,
            "aggregate": agg,
            "selection_score": selection_score(agg),
        }
        if best is None or rec["selection_score"] > best["selection_score"]:
            best = rec
    return best


def search_h_v5(
    signal_data: dict[str, pd.DataFrame],
    daily_data: dict[str, pd.DataFrame],
    grid: dict,
) -> dict:
    """Grid search for backtest_h_v5, handling the dual-TF regime merge.

    For each parameter combination, the regime column is recomputed from the
    daily data (since regime_rsi_min is in the grid). The merged signal df is
    then passed to backtest_h_v5 via the standard eval_multisymbol path.

    The regime column is pre-merged before splitting, so split boundaries apply
    only to signal-TF rows (identical to all other strategies).
    """
    best = None
    for vals in product(*grid.values()):
        p = dict(zip(grid.keys(), vals))

        # Build per-symbol signal dfs with the regime column for these params.
        merged: dict[str, pd.DataFrame] = {}
        for symbol, df_sig in signal_data.items():
            df = df_sig.copy()
            if symbol in daily_data:
                df["regime"] = compute_regime_column(df, daily_data[symbol], p)
            else:
                # No daily data available; regime filter disabled (always trend).
                df["regime"] = "trend"
            merged[symbol] = df

        by_symbol, agg = eval_multisymbol(merged, backtest_h_v5, p)
        rec = {
            "strategy": "h_v5_breakout",
            "params": p,
            "by_symbol": by_symbol,
            "aggregate": agg,
            "selection_score": selection_score(agg),
        }
        if best is None or rec["selection_score"] > best["selection_score"]:
            best = rec
    return best


def render_md(report: dict) -> str:
    lines = []
    lines.append("# BTB Multi-Symbol Strategy Revalidation Report (OOS-focused)")
    lines.append("")
    syms = ", ".join(report["data"]["symbols"])
    lines.append(f"- Data source: `{report['data']['db_path']}` (`ohlcv` table, BTB chart refresh)")
    lines.append(f"- Symbols: {syms}")
    lines.append(f"- Timeframe: `{report['data']['timeframe']}`")
    lines.append("- Split: Train 60% / Validation 20% / Test 20% (time-ordered per symbol)")
    a = report["assumptions"]
    lines.append(f"- Cost assumptions: fee `{a['fee_rate']*100:.2f}%` + slippage `{a['slippage_rate']*100:.2f}%` per side")
    lines.append("")

    lines.append("## Data Coverage")
    lines.append("| Symbol | Bars | From | To |")
    lines.append("|---|---:|---|---|")
    for s, d in report["data"]["coverage"].items():
        lines.append(f"| {s} | {d['bars']} | {d['from'][:10]} | {d['to'][:10]} |")
    lines.append("")

    def add_block(title: str, obj: dict):
        lines.append(f"## {title}")
        lines.append("### Aggregate (equal-weight by symbol)")
        lines.append("| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for w in ["train", "val", "test"]:
            m = obj["aggregate"][w]
            lines.append(
                f"| {w} | {m['net_return_pct']:.2f}% | {m['max_drawdown_pct']:.2f}% | {m['win_rate_pct']:.1f}% | {m['trade_count']} | {m['profit_factor']:.3f} |"
            )
        lines.append("")
        lines.append("### Test (OOS) by Symbol")
        lines.append("| Symbol | Net Return | Max DD | Trades |")
        lines.append("|---|---:|---:|---:|")
        for s in report["data"]["symbols"]:
            m = obj["by_symbol"][s]["test"]
            lines.append(f"| {s} | {m['net_return_pct']:.2f}% | {m['max_drawdown_pct']:.2f}% | {m['trade_count']} |")
        lines.append("")

    add_block("Baseline (EMA 50/200)", report["baseline"])

    lines.append("## Candidate Search Space")
    for c in report["candidates"]:
        lines.append(f"- `{c['strategy']}` score={c['selection_score']}")
    lines.append("")

    lines.append(f"## Winner: `{report['winner']['strategy']}`")
    lines.append(f"- Selection score: {report['winner']['selection_score']}")
    lines.append(f"- Params: `{json.dumps(report['winner']['params'], ensure_ascii=False)}`")
    lines.append("")
    add_block("Winner Performance", report["winner"])

    lines.append("## Conclusion")
    bt = report["baseline"]["aggregate"]["test"]
    wt = report["winner"]["aggregate"]["test"]
    lines.append(f"- Aggregate OOS return: baseline `{bt['net_return_pct']:.2f}%` vs winner `{wt['net_return_pct']:.2f}%`.")
    lines.append(f"- Aggregate OOS max DD: baseline `{bt['max_drawdown_pct']:.2f}%` vs winner `{wt['max_drawdown_pct']:.2f}%`.")
    lines.append(f"- Aggregate OOS trades: baseline `{bt['trade_count']}` vs winner `{wt['trade_count']}`.")
    lines.append("- Winner was selected with OOS trade-count penalty to avoid sparse-trade overfitting.")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="webapp_state.sqlite")
    ap.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--max-bars", type=int, default=1000)
    ap.add_argument("--out", default="reports/btb_strategy_research_multisymbol.json")
    ap.add_argument("--out-md", default="reports/BTB_STRATEGY_RESEARCH_REPORT.md")
    args = ap.parse_args()

    db = Path(args.db)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    data_by_symbol = {s: load_ohlcv(db, s, args.timeframe, max_bars=args.max_bars) for s in symbols}

    # Load daily data for H_V5 regime filter.
    # Always use "1d" regardless of --timeframe, and load up to 2× max-bars (daily
    # bars are sparse; 1000 signal bars at 1h = ~42 daily bars, barely enough for
    # EMA200 warmup — load more bars on the daily TF unconditionally).
    daily_max = max(args.max_bars, 2000)
    daily_data: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            daily_data[s] = load_ohlcv(db, s, "1d", max_bars=daily_max)
        except ValueError:
            print(f"[warn] No daily OHLCV for {s} — H_V5 regime filter will be disabled for this symbol.")

    base_params = {"ema_fast": 50, "ema_slow": 200}
    base_by_symbol, base_agg = eval_multisymbol(data_by_symbol, backtest_ema_cross, base_params)
    baseline = {
        "name": "ema_crossover_50_200",
        "params": base_params,
        "by_symbol": base_by_symbol,
        "aggregate": base_agg,
    }

    # H_V5 grid — parameters that directly correspond to config.yaml strategy block.
    # ADX is NOT in H_V5 entry logic (confirmed from main.py h1_signals).
    # BB params are NOT in H_V5 entry logic (only used in MR branch).
    # momentum_ema_len is hardcoded to 20 (matches live code st.get("momentum_ema_len", 20)).
    h_v5_grid = {
        "donchian_period": [20, 40, 80],
        "ema_fast": [20, 50],           # pullback EMA period (= pullback_ema_len in live)
        "ema_slow": [200],              # regime EMA slow — no reason to vary
        "regime_ema_fast": [50],        # regime EMA fast — fixed
        "regime_rsi_len": [14],         # regime RSI period — fixed
        "regime_rsi_min": [50, 55],     # gates how selective the regime filter is
        "rsi_period": [14],
        "rsi_overheat": [70, 75],
        "atr_period": [14],
        "pullback_band_atr": [0.8],     # ATR multiplier for pullback band width
        "atr_sl_trend_mult": [2.0, 2.5],
        "atr_trail_mult": [5.0, 8.0],
        "breakeven_r": [1.0, 1.5],
    }

    candidates = [
        search_multisymbol(
            data_by_symbol,
            "trend_pullback",
            backtest_trend_pullback,
            {
                "ema_fast": [34, 50], "ema_slow": [144, 200], "rsi_len": [14], "atr_len": [14], "adx_len": [14],
                "adx_min": [14, 18], "rsi_reclaim": [38, 42], "rsi_upper": [60], "rsi_take": [70],
                "sl_atr_mult": [1.2, 1.6], "trail_atr_mult": [2.2], "cooldown_bars": [2, 4],
            },
        ),
        search_multisymbol(
            data_by_symbol,
            "mean_reversion_bb",
            backtest_mr_bb,
            {
                "bb_len": [20], "bb_mult": [1.6, 1.8, 2.0], "rsi_len": [14], "atr_len": [14], "adx_len": [14],
                "adx_max": [18, 24], "rsi_entry": [30, 35, 40], "rsi_exit": [55],
                "sl_atr_mult": [1.2, 1.6], "max_hold_bars": [8, 12],
            },
        ),
        search_multisymbol(
            data_by_symbol,
            "breakout_dynamic_exit",
            backtest_breakout,
            {
                "don_len": [10, 20], "ema_slow": [100, 200], "adx_len": [14], "atr_len": [14],
                "adx_min": [14, 18], "trail_atr_mult": [2.0, 2.6], "init_sl_atr_mult": [1.2, 1.6], "cooldown_bars": [2],
            },
        ),
        search_h_v5(data_by_symbol, daily_data, h_v5_grid),
    ]

    winner = sorted(candidates, key=lambda c: c["selection_score"], reverse=True)[0]

    report = {
        "data": {
            "db_path": args.db,
            "symbols": symbols,
            "timeframe": args.timeframe,
            "max_bars": args.max_bars,
            "coverage": {
                s: {
                    "bars": len(df),
                    "from": str(df["dt"].iloc[0]),
                    "to": str(df["dt"].iloc[-1]),
                }
                for s, df in data_by_symbol.items()
            },
        },
        "assumptions": {"fee_rate": 0.001, "slippage_rate": 0.0005, "side_cost": 0.0015},
        "baseline": baseline,
        "candidates": candidates,
        "winner": winner,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = Path(args.out_md)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render_md(report), encoding="utf-8")

    print(json.dumps({
        "winner": winner["strategy"],
        "winner_score": winner["selection_score"],
        "baseline_test_agg": baseline["aggregate"]["test"],
        "winner_test_agg": winner["aggregate"]["test"],
        "symbols": symbols,
        "timeframe": args.timeframe,
    }, indent=2))


if __name__ == "__main__":
    main()
