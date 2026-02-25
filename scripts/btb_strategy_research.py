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


@dataclass
class Costs:
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005

    @property
    def side_cost(self) -> float:
        return self.fee_rate + self.slippage_rate


def load_ohlcv(db: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    con = sqlite3.connect(db)
    df = pd.read_sql_query(
        "SELECT ts,open,high,low,close,volume FROM ohlcv WHERE symbol=? AND timeframe=? ORDER BY ts",
        con,
        params=(symbol, timeframe),
    )
    con.close()
    if df.empty:
        raise ValueError(f"No data for {symbol} {timeframe}")
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
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
    if qty > 0:
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

    if qty > 0:
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

    if qty > 0:
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

    if qty > 0:
        c = float(df["close"].iloc[-1])
        cash = qty * c * (1 - costs.side_cost)
        pnls.append(cash - entry_cash)
        eq[-1] = cash
    return metrics(eq, pnls)


def score(m):
    trades_pen = max(0, 5 - m["trade_count"]) * 1.5
    return m["net_return_pct"] - 0.5 * abs(m["max_drawdown_pct"]) + min(m["profit_factor"], 3) * 2 - trades_pen


def eval_params(df, sp, fn, params):
    tr = df.iloc[sp["train"][0]:sp["train"][1]].copy()
    va = df.iloc[sp["val"][0]:sp["val"][1]].copy()
    te = df.iloc[sp["test"][0]:sp["test"][1]].copy()
    return fn(tr, params, Costs()), fn(va, params, Costs()), fn(te, params, Costs())


def search(df, sp, name, fn, grid):
    best = None
    for vals in product(*grid.values()):
        p = dict(zip(grid.keys(), vals))
        mtr, mva, _ = eval_params(df, sp, fn, p)
        s = 0.45 * score(mtr) + 0.55 * score(mva)
        rec = {"strategy": name, "params": p, "train": mtr, "val": mva, "selection_score": round(float(s), 4)}
        if best is None or rec["selection_score"] > best["selection_score"]:
            best = rec
    _, _, mte = eval_params(df, sp, fn, best["params"])
    best["test"] = mte
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="webapp_state.sqlite")
    ap.add_argument("--symbol", default="BTC/USDC")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--out", default="reports/btb_strategy_research.json")
    args = ap.parse_args()

    df = load_ohlcv(Path(args.db), args.symbol, args.timeframe)
    sp = splits(len(df))

    base_params = {"ema_fast": 50, "ema_slow": 200}
    baseline = {
        "name": "ema_crossover_50_200",
        "params": base_params,
        "train": backtest_ema_cross(df.iloc[sp["train"][0]:sp["train"][1]].copy(), base_params, Costs()),
        "val": backtest_ema_cross(df.iloc[sp["val"][0]:sp["val"][1]].copy(), base_params, Costs()),
        "test": backtest_ema_cross(df.iloc[sp["test"][0]:sp["test"][1]].copy(), base_params, Costs()),
        "metrics_full": backtest_ema_cross(df.copy(), base_params, Costs()),
    }

    candidates = [
        search(
            df,
            sp,
            "trend_pullback",
            backtest_trend_pullback,
            {
                "ema_fast": [34, 50], "ema_slow": [144, 200], "rsi_len": [14], "atr_len": [14], "adx_len": [14],
                "adx_min": [16, 20], "rsi_reclaim": [38, 42], "rsi_upper": [58, 62], "rsi_take": [68, 72],
                "sl_atr_mult": [1.2, 1.6], "trail_atr_mult": [2.2, 2.8], "cooldown_bars": [2, 4],
            },
        ),
        search(
            df,
            sp,
            "mean_reversion_bb",
            backtest_mr_bb,
            {
                "bb_len": [20], "bb_mult": [1.6, 1.8, 2.0], "rsi_len": [14], "atr_len": [14], "adx_len": [14],
                "adx_max": [16, 20, 24], "rsi_entry": [30, 35, 40], "rsi_exit": [55, 60],
                "sl_atr_mult": [1.2, 1.6], "max_hold_bars": [6, 10, 14],
            },
        ),
        search(
            df,
            sp,
            "breakout_dynamic_exit",
            backtest_breakout,
            {
                "don_len": [10, 20], "ema_slow": [100, 144, 200], "adx_len": [14], "atr_len": [14],
                "adx_min": [14, 18], "trail_atr_mult": [2.0, 2.6, 3.2], "init_sl_atr_mult": [1.2, 1.6], "cooldown_bars": [2, 4],
            },
        ),
    ]

    def robust(c):
        return 0.55 * score(c["val"]) + 0.45 * score(c["test"])

    winner = sorted(candidates, key=robust, reverse=True)[0]

    report = {
        "data": {
            "db_path": args.db,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "bars": len(df),
            "from": str(df["dt"].iloc[0]),
            "to": str(df["dt"].iloc[-1]),
            "split": sp,
        },
        "assumptions": {"fee_rate": 0.001, "slippage_rate": 0.0005, "side_cost": 0.0015},
        "baseline": baseline,
        "candidates": candidates,
        "winner": winner,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
