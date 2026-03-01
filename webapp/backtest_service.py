from __future__ import annotations

import re
import pandas as pd

from ta.trend import EMAIndicator, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.momentum import RSIIndicator

from .storage import Storage
from .models import (
    BacktestCurve,
    BacktestMarker,
    BacktestMetrics,
    BacktestSummary,
    UnifiedBacktestBundle,
    UnifiedBacktestResult,
)


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
        markers = []

        for i, r in enumerate(df.itertuples(index=False), start=0):
            ts = int(r.ts)
            price = float(r.close)
            signal_buy = r.fast > r.slow
            if signal_buy and qty == 0.0:
                qty = (cash * (1.0 - fee_rate)) / price
                cash = 0.0
                trades += 1
                markers.append({
                    "ts": ts,
                    "index": i,
                    "price": price,
                    "side": "buy",
                    "reason": f"sma_cross_up(fast={fast},slow={slow})",
                })
            elif (not signal_buy) and qty > 0.0:
                cash = qty * price * (1.0 - fee_rate)
                qty = 0.0
                trades += 1
                markers.append({
                    "ts": ts,
                    "index": i,
                    "price": price,
                    "side": "sell",
                    "reason": f"sma_cross_down(fast={fast},slow={slow})",
                })
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
            "markers": markers,
            "signal_basis": "SMA crossover replay",
        }

    def run_config_simulation(self, symbol: str, main_cfg: dict, starting_capital: float, fee_rate: float):
        general = (main_cfg or {}).get("general", {}) or {}
        strategy = (main_cfg or {}).get("strategy", {}) or {}

        timeframe_signal = str(general.get("timeframe_signal") or "1h")
        timeframe_regime = str(general.get("timeframe_regime") or "1d")

        fast = int(strategy.get("ema_fast", 20) or 20)
        slow = int(strategy.get("ema_slow", 60) or 60)
        atr_len = int(strategy.get("atr_len", 14) or 14)
        rsi_len = int(strategy.get("rsi_len", 14) or 14)
        adx_len = int(strategy.get("adx_len", 14) or 14)
        don_len = int(strategy.get("donchian_len", 55) or 55)
        bb_len = int(strategy.get("bb_len", 20) or 20)
        bb_mult = float(strategy.get("bb_mult", 2.0) or 2.0)
        mode = str(strategy.get("mode", "legacy_hybrid") or "legacy_hybrid").lower()

        rows_sig = self.storage.fetch_ohlcv(symbol, timeframe_signal, limit=2500)
        rows_reg = self.storage.fetch_ohlcv(symbol, timeframe_regime, limit=2500)
        if len(rows_sig) < max(slow, don_len, bb_len) + 5:
            raise ValueError("Not enough signal timeframe OHLCV data. Refresh charts first.")
        if len(rows_reg) < max(slow, adx_len) + 5:
            raise ValueError("Not enough regime timeframe OHLCV data. Refresh charts first.")

        sig = pd.DataFrame(rows_sig, columns=["ts", "open", "high", "low", "close", "volume"])
        reg = pd.DataFrame(rows_reg, columns=["ts", "open", "high", "low", "close", "volume"])
        sig = sig.sort_values("ts").reset_index(drop=True)
        reg = reg.sort_values("ts").reset_index(drop=True)

        # signal timeframe indicators (main.py-aligned)
        sig["ema_fast"] = EMAIndicator(sig["close"], fast).ema_indicator()
        sig["ema_slow"] = EMAIndicator(sig["close"], slow).ema_indicator()
        sig["atr"] = AverageTrueRange(sig["high"], sig["low"], sig["close"], atr_len).average_true_range()
        sig["rsi"] = RSIIndicator(sig["close"], rsi_len).rsi()
        sig["adx"] = ADXIndicator(sig["high"], sig["low"], sig["close"], adx_len).adx()
        sig["don_hi"] = sig["high"].rolling(don_len).max()
        bb = BollingerBands(sig["close"], bb_len, bb_mult)
        sig["bb_lower"] = bb.bollinger_lband()
        sig["bb_mid"] = bb.bollinger_mavg()

        # regime timeframe indicators (main.py-aligned)
        reg_ema = EMAIndicator(reg["close"], slow).ema_indicator()
        reg["ema_slow"] = reg_ema
        reg["ema_slope_pos"] = reg_ema.diff() > 0
        reg["adx"] = ADXIndicator(reg["high"], reg["low"], reg["close"], adx_len).adx()
        trend_adx_threshold = float(strategy.get("trend_adx_threshold", 20.0) or 20.0)
        reg["regime"] = "none"
        reg.loc[(reg["ema_slope_pos"] == True) & (reg["adx"] > trend_adx_threshold), "regime"] = "trend"
        reg.loc[reg["adx"] <= trend_adx_threshold, "regime"] = "range"

        regime_ts = reg["ts"].tolist()

        cash = float(starting_capital)
        qty = 0.0
        entry_ts = None
        sl = None
        trail_mult = float(strategy.get("atr_trail_mult", 3.5) or 3.5)
        hi_since_entry = None
        tp_mid = None
        pos_signal = None

        equity_curve = []
        markers = []
        trades_count = 0

        for i in range(len(sig)):
            r = sig.iloc[i]
            ts = int(r["ts"])
            price = float(r["close"])

            # map signal candle -> latest regime candle at or before ts
            ridx = pd.Index(regime_ts).searchsorted(ts, side="right") - 1
            regime = "none" if ridx < 0 else str(reg.iloc[int(ridx)]["regime"])

            # build entry signal (similar to main.py h1_signals)
            signal = None
            if i >= 2 and pd.notna(r["ema_fast"]) and pd.notna(r["ema_slow"]) and pd.notna(r["atr"]) and pd.notna(r["rsi"]):
                close = price
                atr_v = float(r["atr"])
                ema_f = float(r["ema_fast"])
                ema_s = float(r["ema_slow"])
                rsi_v = float(r["rsi"])
                adx_v = float(r["adx"]) if pd.notna(r["adx"]) else 0.0
                don_hi_prev = float(sig.iloc[i - 1]["don_hi"]) if pd.notna(sig.iloc[i - 1]["don_hi"]) else None
                bb_lower = float(r["bb_lower"]) if pd.notna(r["bb_lower"]) else None
                bb_mid = float(r["bb_mid"]) if pd.notna(r["bb_mid"]) else None

                if mode == "mean_reversion_bb_regime":
                    adx_max = float(strategy.get("mr_adx_max", 24) or 24)
                    rsi_entry = float(strategy.get("mr_rsi_entry", strategy.get("rsi_mr_threshold", 35)) or 35)
                    if bb_lower is not None and close <= bb_lower and rsi_v <= rsi_entry and adx_v <= adx_max:
                        signal = "R_LONG"
                elif regime == "trend":
                    rsi_overheat = float(strategy.get("rsi_overheat", 70) or 70)
                    if (don_hi_prev is not None) and (close > don_hi_prev) and (ema_f > ema_s) and (rsi_v < rsi_overheat):
                        signal = "T_LONG"
                elif regime == "range":
                    rsi_mr_threshold = float(strategy.get("rsi_mr_threshold", 35) or 35)
                    if bb_lower is not None and close < bb_lower and rsi_v <= rsi_mr_threshold:
                        signal = "R_LONG"

                if qty == 0.0 and signal:
                    qty = (cash * (1.0 - fee_rate)) / price if price > 0 else 0.0
                    if qty > 0:
                        cash = 0.0
                        entry_ts = ts
                        sl = close - float(strategy.get("atr_sl_trend_mult", 2.0) if signal == "T_LONG" else strategy.get("atr_sl_mr_mult", 1.2)) * atr_v
                        hi_since_entry = close
                        tp_mid = bb_mid if signal == "R_LONG" else None
                        pos_signal = signal
                        trades_count += 1
                        markers.append({"ts": ts, "index": i, "price": close, "side": "buy", "reason": signal})

            # manage exits
            if qty > 0.0:
                close = price
                if hi_since_entry is None:
                    hi_since_entry = close
                hi_since_entry = max(hi_since_entry, close)

                if pos_signal == "T_LONG" and pd.notna(r["atr"]):
                    trail = hi_since_entry - trail_mult * float(r["atr"])
                    sl = max(float(sl or trail), float(trail))

                should_exit = False
                reason = None

                if sl is not None and close <= float(sl):
                    should_exit = True
                    reason = "STOP"
                elif entry_ts is not None and tp_mid is not None:
                    max_h = float(strategy.get("mean_reversion_time_stop_hours", 12) or 12)
                    if (ts - int(entry_ts)) >= int(max_h * 3600 * 1000):
                        should_exit = True
                        reason = "TIME_STOP"
                    elif close >= float(tp_mid):
                        should_exit = True
                        reason = "TP_MID"

                if should_exit:
                    cash = qty * close * (1.0 - fee_rate)
                    qty = 0.0
                    trades_count += 1
                    markers.append({"ts": ts, "index": i, "price": close, "side": "sell", "reason": reason or "exit"})
                    entry_ts = None
                    sl = None
                    tp_mid = None
                    hi_since_entry = None
                    pos_signal = None

            equity = cash + qty * price
            equity_curve.append((ts, float(equity), price))

        if qty > 0.0 and len(sig):
            last_price = float(sig.iloc[-1]["close"])
            cash = qty * last_price * (1.0 - fee_rate)
            qty = 0.0

        final_equity = cash
        roi = (final_equity / starting_capital - 1.0) * 100.0 if starting_capital > 0 else 0.0
        max_equity = max((e for _, e, _ in equity_curve), default=starting_capital)
        min_equity = min((e for _, e, _ in equity_curve), default=starting_capital)
        drawdown = (min_equity / max_equity - 1.0) * 100.0 if max_equity > 0 else 0.0

        labels = [pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M") for ts, _, _ in equity_curve]
        values = [e for _, e, _ in equity_curve]

        return {
            "symbol": symbol,
            "roi_pct": round(roi, 2),
            "final_equity": round(final_equity, 2),
            "max_drawdown_pct": round(drawdown, 2),
            "trades": trades_count,
            "equity_curve": {"labels": labels, "values": values},
            "markers": markers,
            "signal_basis": f"main.py strategy simulation (signal={timeframe_signal}, regime={timeframe_regime})",
        }

    def _normalize_markers(self, markers: list | None) -> list[dict]:
        normalized = []
        for m in (markers or []):
            try:
                normalized.append(BacktestMarker(**m).model_dump())
            except Exception:
                continue
        return normalized

    def _normalize_curve(self, curve: dict | None) -> dict:
        try:
            return BacktestCurve(**(curve or {})).model_dump()
        except Exception:
            return BacktestCurve().model_dump()

    def _normalize_metrics(self, metrics: dict | None) -> dict:
        try:
            return BacktestMetrics(**(metrics or {})).model_dump()
        except Exception:
            return BacktestMetrics().model_dump()

    def to_unified_quick(self, symbol: str, quick_result: dict) -> dict:
        markers_norm = self._normalize_markers(quick_result.get("markers"))
        payload = UnifiedBacktestResult(
            engine="quick",
            summary=BacktestSummary(
                symbol=symbol,
                status="ok",
                source="quick",
                signal_basis=str(quick_result.get("signal_basis") or "main.py strategy simulation from config.yaml"),
            ),
            metrics=BacktestMetrics(
                roi_pct=quick_result.get("roi_pct"),
                final_equity=quick_result.get("final_equity"),
                max_drawdown_pct=quick_result.get("max_drawdown_pct"),
                trades=quick_result.get("trades"),
            ),
            trades=markers_norm,
            equity_curve=BacktestCurve(**(quick_result.get("equity_curve") or {})),
            markers=[BacktestMarker(**m) for m in markers_norm],
        )
        return payload.model_dump()

    def to_unified_legacy(self, symbol: str, legacy_output: str, returncode: int = 0) -> dict:
        roi = self._extract_float(legacy_output, r"ROI\s*[:=]\s*([-+]?\d+(?:\.\d+)?)")
        mdd = self._extract_float(legacy_output, r"(?:MDD|max[_\s-]?drawdown)\s*[:=]\s*([-+]?\d+(?:\.\d+)?)")
        trades = self._extract_int(legacy_output, r"trades?\s*[:=]\s*(\d+)")

        payload = UnifiedBacktestResult(
            engine="legacy",
            summary=BacktestSummary(symbol=symbol, status="ok" if int(returncode) == 0 else "error", source="legacy"),
            metrics=BacktestMetrics(
                roi_pct=roi,
                final_equity=None,
                max_drawdown_pct=mdd,
                trades=trades,
            ),
            trades=[],
            equity_curve=BacktestCurve(),
            markers=[],
            raw_output=(legacy_output or "")[:8000],
        )
        return payload.model_dump()

    def to_unified_both(self, symbol: str, quick_result: dict, legacy_output: str, legacy_returncode: int = 0) -> dict:
        quick = self.to_unified_quick(symbol, quick_result)
        legacy = self.to_unified_legacy(symbol, legacy_output, returncode=legacy_returncode)

        merged = UnifiedBacktestBundle(
            summary=BacktestSummary(
                symbol=symbol,
                status="ok" if quick["summary"]["status"] == "ok" and legacy["summary"]["status"] == "ok" else "error",
                source="quick+legacy",
                note="Merged view for convergence layer",
            ),
            metrics=BacktestMetrics(
                roi_pct=quick.get("metrics", {}).get("roi_pct"),
                final_equity=quick.get("metrics", {}).get("final_equity"),
                max_drawdown_pct=legacy.get("metrics", {}).get("max_drawdown_pct"),
                trades=quick.get("metrics", {}).get("trades"),
            ),
            markers=[BacktestMarker(**m) for m in self._normalize_markers(quick.get("markers"))],
            quick=UnifiedBacktestResult(**quick),
            legacy=UnifiedBacktestResult(**legacy),
        )
        return merged.model_dump()

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
