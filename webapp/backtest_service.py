from __future__ import annotations

import re
import pandas as pd

from .storage import Storage


class BacktestService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def run_sma_crossover(self, symbol: str, timeframe: str, fast: int, slow: int, starting_capital: float, fee_rate: float):
        rows = self.storage.fetch_ohlcv(symbol, timeframe, limit=2000)
        if len(rows) < slow + 5:
            raise ValueError("Not enough OHLCV data. Refresh charts first.")

        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["fast"] = df["close"].rolling(fast).mean()
        df["slow"] = df["close"].rolling(slow).mean()
        df = df.dropna().copy()

        cash = starting_capital
        qty = 0.0
        equity_curve = []
        trades = 0

        for _, r in df.iterrows():
            ts = int(r["ts"])
            price = float(r["close"])
            signal_buy = r["fast"] > r["slow"]
            if signal_buy and qty == 0.0:
                qty = (cash * (1.0 - fee_rate)) / price
                cash = 0.0
                trades += 1
            elif (not signal_buy) and qty > 0.0:
                cash = qty * price * (1.0 - fee_rate)
                qty = 0.0
                trades += 1
            equity = cash + qty * price
            equity_curve.append((ts, float(equity), price))

        if qty > 0.0:
            last_price = float(df.iloc[-1]["close"])
            cash = qty * last_price * (1.0 - fee_rate)
            qty = 0.0

        final_equity = cash
        roi = (final_equity / starting_capital - 1.0) * 100.0
        max_equity = max(e for _, e, _ in equity_curve)
        min_equity = min(e for _, e, _ in equity_curve)
        drawdown = (min_equity / max_equity - 1.0) * 100.0 if max_equity > 0 else 0.0

        labels = [pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m-%d") for ts, _, _ in equity_curve]
        values = [e for _, e, _ in equity_curve]
        prices = [p for _, _, p in equity_curve]

        return {
            "symbol": symbol,
            "roi_pct": round(roi, 2),
            "final_equity": round(final_equity, 2),
            "max_drawdown_pct": round(drawdown, 2),
            "trades": trades,
            "equity_curve": {"labels": labels, "values": values},
            "price_curve": {"labels": labels, "values": prices},
        }

    def to_unified_quick(self, symbol: str, quick_result: dict) -> dict:
        return {
            "engine": "quick",
            "summary": {
                "symbol": symbol,
                "status": "ok",
            },
            "metrics": {
                "roi_pct": quick_result.get("roi_pct"),
                "final_equity": quick_result.get("final_equity"),
                "max_drawdown_pct": quick_result.get("max_drawdown_pct"),
                "trades": quick_result.get("trades"),
            },
            "trades": [],
            "equity_curve": quick_result.get("equity_curve") or {"labels": [], "values": []},
        }

    def to_unified_legacy(self, symbol: str, legacy_output: str, returncode: int = 0) -> dict:
        roi = self._extract_float(legacy_output, r"ROI\s*[:=]\s*([-+]?\d+(?:\.\d+)?)")
        mdd = self._extract_float(legacy_output, r"(?:MDD|max[_\s-]?drawdown)\s*[:=]\s*([-+]?\d+(?:\.\d+)?)")
        trades = self._extract_int(legacy_output, r"trades?\s*[:=]\s*(\d+)")

        return {
            "engine": "legacy",
            "summary": {
                "symbol": symbol,
                "status": "ok" if int(returncode) == 0 else "error",
            },
            "metrics": {
                "roi_pct": roi,
                "final_equity": None,
                "max_drawdown_pct": mdd,
                "trades": trades,
            },
            "trades": [],
            "equity_curve": {"labels": [], "values": []},
            "raw_output": (legacy_output or "")[:8000],
        }

    @staticmethod
    def _extract_float(text: str, pattern: str):
        if not text:
            return None
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1))
        except Exception:
            return None

    @staticmethod
    def _extract_int(text: str, pattern: str):
        if not text:
            return None
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None
