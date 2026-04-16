import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import main as bot


class TestHV5StrategyAdoption(unittest.TestCase):
    def _cfg(self):
        return {
            "general": {"timeframe_regime": "1d"},
            "strategy": {
                "mode": "h_v5_b_plus_breakeven_ema100",
                "ema_slow": 200,
                "regime_ema_fast": 50,
                "regime_rsi_len": 14,
                "regime_rsi_min": 55,
                "donchian_len": 80,
                "bb_len": 20,
                "ema_fast": 50,
                "rsi_len": 14,
                "rsi_overheat": 75,
                "atr_len": 14,
                "adx_len": 14,
                "atr_sl_trend_mult": 2.5,
                "atr_trail_mult": 8.0,
                "pullback_ema_len": 50,
                "pullback_band_atr": 0.8,
                "bb_mult": 2.0,
            },
        }

    def test_h1_signals_v5_breakout_triggers_t_long(self):
        cfg = self._cfg()
        cfg["strategy"]["rsi_overheat"] = 101
        n = 220
        base = np.linspace(100, 200, n)
        wobble = np.sin(np.linspace(0, 20, n)) * 1.5
        c = pd.Series(base + wobble)
        c.iloc[-1] = c.iloc[-2] + 4.0  # breakout push
        df = pd.DataFrame({
            "o": c - 0.5,
            "h": c + 1.0,
            "l": c - 1.0,
            "c": c,
            "v": np.ones(n),
        })

        signal, params = bot.h1_signals(df, cfg, regime="trend")
        self.assertEqual(signal, "T_LONG")
        self.assertIn("sl", params)
        self.assertIn("trail_mult", params)

    def test_h1_signals_v5_requires_trend_regime(self):
        cfg = self._cfg()
        n = 220
        c = pd.Series(np.linspace(100, 200, n))
        df = pd.DataFrame({"o": c - 0.5, "h": c + 1.0, "l": c - 1.0, "c": c, "v": np.ones(n)})
        signal, _ = bot.h1_signals(df, cfg, regime="range")
        self.assertIsNone(signal)

    # ------------------------------------------------------------------ #
    # Edge-case tests added for TEST-01                                    #
    # ------------------------------------------------------------------ #

    def test_h1_signals_insufficient_bars_returns_none(self):
        """Fewer bars than warm-up window → early return with None signal."""
        cfg = self._cfg()
        n = 10
        c = pd.Series(np.linspace(100, 110, n))
        df = pd.DataFrame({
            "o": c - 0.5,
            "h": c + 1.0,
            "l": c - 1.0,
            "c": c,
            "v": np.ones(n),
        })
        signal, params = bot.h1_signals(df, cfg, regime="trend")
        self.assertIsNone(signal)

    def test_h1_signals_rsi_overheat_suppresses_t_long(self):
        """RSI above rsi_overheat threshold blocks T_LONG entry in V5 mode."""
        cfg = self._cfg()
        cfg["strategy"]["rsi_overheat"] = 60
        n = 220
        # Very steep uptrend drives RSI well above 60 by the last bar.
        c = pd.Series(np.linspace(100, 600, n))
        df = pd.DataFrame({
            "o": c - 0.5,
            "h": c + 1.0,
            "l": c - 1.0,
            "c": c,
            "v": np.ones(n),
        })
        signal, params = bot.h1_signals(df, cfg, regime="trend")
        self.assertIsNone(signal)

    def test_h1_signals_regime_none_suppresses_entry(self):
        """regime='none' skips the V5 entry block entirely → None signal."""
        cfg = self._cfg()
        cfg["strategy"]["rsi_overheat"] = 101  # Would allow entry if regime were trend
        n = 220
        base = np.linspace(100, 200, n)
        wobble = np.sin(np.linspace(0, 20, n)) * 1.5
        c = pd.Series(base + wobble)
        c.iloc[-1] = c.iloc[-2] + 4.0  # breakout push
        df = pd.DataFrame({
            "o": c - 0.5,
            "h": c + 1.0,
            "l": c - 1.0,
            "c": c,
            "v": np.ones(n),
        })
        signal, params = bot.h1_signals(df, cfg, regime="none")
        self.assertIsNone(signal)

    def test_h1_signals_params_always_contain_atr_and_close(self):
        """params always has 'atr' and 'close' regardless of signal outcome."""
        cfg = self._cfg()
        n = 220
        c = pd.Series(np.linspace(100, 200, n))
        df = pd.DataFrame({
            "o": c - 0.5,
            "h": c + 1.0,
            "l": c - 1.0,
            "c": c,
            "v": np.ones(n),
        })
        _signal, params = bot.h1_signals(df, cfg, regime="trend")
        self.assertIn("atr", params)
        self.assertIn("close", params)

    def test_h1_signals_pullback_suppressed_without_momentum(self):
        """Flat/sideways price → ATR≈0 → pullback=False, breakout=False → None."""
        cfg = self._cfg()
        n = 220
        # Perfectly flat price: ATR is 0 so pullback guard `if atr_v > 0` is False,
        # and don_hi_prev == close so breakout is False.
        c = pd.Series(np.ones(n) * 100.0)
        df = pd.DataFrame({
            "o": c,
            "h": c,
            "l": c,
            "c": c,
            "v": np.ones(n),
        })
        signal, params = bot.h1_signals(df, cfg, regime="trend")
        self.assertIsNone(signal)

    def test_regime_filter_v5_uses_ema_slope_and_rsi(self):
        cfg = self._cfg()
        n = 260
        c = pd.Series(np.linspace(100, 240, n))
        d_df = pd.DataFrame({"o": c - 1, "h": c + 1, "l": c - 2, "c": c, "v": np.ones(n)})
        with patch.object(bot, "fetch_ohlc", return_value=d_df):
            regime, adx_v = bot.regime_filter(exchange=None, symbol="BTC/USDC", cfg=cfg)
        self.assertEqual(regime, "trend")
        self.assertTrue(np.isnan(adx_v))


if __name__ == "__main__":
    unittest.main()
