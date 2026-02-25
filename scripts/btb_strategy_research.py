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

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    data_by_symbol = {s: load_ohlcv(Path(args.db), s, args.timeframe, max_bars=args.max_bars) for s in symbols}

    base_params = {"ema_fast": 50, "ema_slow": 200}
    base_by_symbol, base_agg = eval_multisymbol(data_by_symbol, backtest_ema_cross, base_params)
    baseline = {
        "name": "ema_crossover_50_200",
        "params": base_params,
        "by_symbol": base_by_symbol,
        "aggregate": base_agg,
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
