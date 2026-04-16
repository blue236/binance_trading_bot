"""
TEST-02: Tests for regime_filter() in main.py.

regime_filter(exchange, symbol, cfg) -> (regime_str, adx_value)

V5 mode (h_v5_b_plus_breakeven_ema100):
  - Returns ("trend", nan) when: close > EMA200, EMA200 slope positive,
    EMA50 > EMA200, RSI >= regime_rsi_min
  - Returns ("none", nan) otherwise

Legacy mode (any other mode string):
  - Returns ("trend", adx) when ADX > trend_adx_threshold AND EMA200 slope positive
  - Returns ("range", adx) when ADX <= trend_adx_threshold
"""

import logging
import math
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import main as bot


def _make_df(closes, noise_amplitude=1.0):
    """Build a minimal OHLCV DataFrame from a close price array."""
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "o": c - noise_amplitude * 0.5,
        "h": c + noise_amplitude,
        "l": c - noise_amplitude,
        "c": c,
        "v": np.ones(len(c)),
    })


class TestRegimeFilterV5(unittest.TestCase):
    """Tests for regime_filter() when mode = h_v5_b_plus_breakeven_ema100."""

    def _cfg(self):
        return {
            "general": {
                "symbols": ["BTC/USDC"],
                "timeframe_signal": "4h",
                "timeframe_regime": "1d",
                "exchange": "binance",
                "dry_run": True,
                "aggressive_mode": False,
                "base_currency": "USDC",
                "min_notional_usdc": 10.0,
            },
            "risk": {
                "per_trade_risk_pct": 0.5,
                "daily_loss_stop_pct": 2.0,
                "max_concurrent_positions": 2,
                "cooldown_hours": 8,
            },
            "strategy": {
                "mode": "h_v5_b_plus_breakeven_ema100",
                "adx_len": 14,
                "trend_adx_threshold": 20,
                "ema_slow": 200,
                "regime_ema_fast": 50,
                "regime_rsi_len": 14,
                "regime_rsi_min": 55,
                "donchian_len": 80,
                "pullback_ema_len": 50,
                "pullback_band_atr": 0.8,
                "ema_fast": 50,
                "rsi_len": 14,
                "rsi_overheat": 75,
                "atr_len": 14,
                "atr_sl_trend_mult": 2.5,
                "atr_trail_mult": 8.0,
                "breakeven_r": 1.0,
                "use_structural_exit": True,
                "structural_exit_daily_ema_len": 100,
                "structural_exit_timeframe": "1d",
                "structural_exit_confirm_days": 2,
                "bb_len": 20,
                "bb_mult": 2.0,
                "atr_sl_mr_mult": 1.2,
                "rsi_mr_threshold": 35,
                "mean_reversion_time_stop_hours": 24,
                "loop_sleep_seconds": 60,
            },
            "alerts": {
                "enable_telegram": False,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "enable_trade_approval": False,
                "approval_timeout_sec": 180,
                "telegram_owner_user_id": "",
            },
            "logging": {
                "csv_dir": "/tmp/btb_test_logs",
                "state_file": "/tmp/btb_test_state.json",
                "tz": "UTC",
                "level": "DEBUG",
            },
            "network": {"retry_count": 1, "retry_backoff_sec": 0.0},
            "credentials": {"api_key": "test", "api_secret": "test"},
        }

    # ------------------------------------------------------------------
    # V5 mode — all conditions met → "trend"
    # ------------------------------------------------------------------

    def test_regime_v5_trending_returns_trend(self):
        """
        Strong uptrend (100 -> 400 over 260 bars):
        - close >> EMA200
        - EMA200 slope is positive
        - EMA50 > EMA200
        - RSI >> 55 (strong momentum)
        Expected: ("trend", nan)
        """
        cfg = self._cfg()
        n = 260
        closes = np.linspace(100, 400, n)
        df = _make_df(closes)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertEqual(regime, "trend")
        self.assertTrue(math.isnan(adx_v))

    # ------------------------------------------------------------------
    # V5 mode — RSI below regime_rsi_min → "none"
    # ------------------------------------------------------------------

    def test_regime_v5_low_rsi_returns_none(self):
        """
        Strong rise then a gentle but sustained decline:
        - close = 380 at end, EMA200 ≈ 280–320 → close > EMA200 still holds
        - EMA200 slope remains positive (dominated by prior uptrend)
        - EMA50 > EMA200 still holds after a small decline
        - RSI ≈ 0–5 due to consistent sell pressure → well below regime_rsi_min=55
        Expected: ("none", nan)
        """
        cfg = self._cfg()
        # 200 bars of rise pushes close far above EMA200 and warms indicators.
        # 100 bars of slight decline drives RSI to ~0 while keeping close >> EMA200.
        rises = np.linspace(100, 400, 200)
        drops = np.linspace(400, 380, 100)
        closes = np.concatenate([rises, drops])
        df = _make_df(closes)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertEqual(regime, "none")
        self.assertTrue(math.isnan(adx_v))

    # ------------------------------------------------------------------
    # V5 mode — price below EMA200 → "none"
    # ------------------------------------------------------------------

    def test_regime_v5_below_ema200_returns_none(self):
        """
        Consistent downtrend (400 -> 100 over 260 bars):
        - close < EMA200 throughout
        Expected: ("none", nan)
        """
        cfg = self._cfg()
        n = 260
        closes = np.linspace(400, 100, n)
        df = _make_df(closes)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertEqual(regime, "none")
        self.assertTrue(math.isnan(adx_v))

    # ------------------------------------------------------------------
    # V5 mode — adx_v is always nan regardless of conditions
    # ------------------------------------------------------------------

    def test_regime_v5_always_returns_nan_adx(self):
        """
        V5 mode never computes ADX; second tuple element must always be nan.
        """
        cfg = self._cfg()
        n = 260
        closes = np.linspace(100, 400, n)
        df = _make_df(closes)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            _regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertTrue(math.isnan(adx_v))

    # ------------------------------------------------------------------
    # V5 mode — EMA50 below EMA200 → "none"
    # ------------------------------------------------------------------

    def test_regime_v5_ema50_below_ema200_returns_none(self):
        """
        Price plunges steeply at the end so the short EMA50 falls through
        EMA200 while close is still near EMA200 level.
        A sharp drop at end: EMA50 reacts faster and drops below EMA200.
        Expected: ("none", nan)
        """
        cfg = self._cfg()
        # Rise to 300, then sharp decline to 80 in last 40 bars.
        # EMA50 reacts fast → drops below EMA200.
        n_rise = 220
        n_drop = 40
        rises = np.linspace(100, 300, n_rise)
        drops = np.linspace(300, 80, n_drop)
        closes = np.concatenate([rises, drops])
        df = _make_df(closes)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertEqual(regime, "none")
        self.assertTrue(math.isnan(adx_v))


class TestRegimeFilterLegacy(unittest.TestCase):
    """Tests for regime_filter() when mode is NOT h_v5_b_plus_breakeven_ema100."""

    def _cfg(self):
        cfg = {
            "general": {
                "symbols": ["BTC/USDC"],
                "timeframe_signal": "4h",
                "timeframe_regime": "1d",
                "exchange": "binance",
                "dry_run": True,
                "aggressive_mode": False,
                "base_currency": "USDC",
                "min_notional_usdc": 10.0,
            },
            "risk": {
                "per_trade_risk_pct": 0.5,
                "daily_loss_stop_pct": 2.0,
                "max_concurrent_positions": 2,
                "cooldown_hours": 8,
            },
            "strategy": {
                "mode": "legacy",
                "adx_len": 14,
                "trend_adx_threshold": 20,
                "ema_slow": 200,
                "regime_ema_fast": 50,
                "regime_rsi_len": 14,
                "regime_rsi_min": 55,
                "donchian_len": 80,
                "pullback_ema_len": 50,
                "pullback_band_atr": 0.8,
                "ema_fast": 50,
                "rsi_len": 14,
                "rsi_overheat": 75,
                "atr_len": 14,
                "atr_sl_trend_mult": 2.5,
                "atr_trail_mult": 8.0,
                "breakeven_r": 1.0,
                "use_structural_exit": True,
                "structural_exit_daily_ema_len": 100,
                "structural_exit_timeframe": "1d",
                "structural_exit_confirm_days": 2,
                "bb_len": 20,
                "bb_mult": 2.0,
                "atr_sl_mr_mult": 1.2,
                "rsi_mr_threshold": 35,
                "mean_reversion_time_stop_hours": 24,
                "loop_sleep_seconds": 60,
            },
            "alerts": {
                "enable_telegram": False,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "enable_trade_approval": False,
                "approval_timeout_sec": 180,
                "telegram_owner_user_id": "",
            },
            "logging": {
                "csv_dir": "/tmp/btb_test_logs",
                "state_file": "/tmp/btb_test_state.json",
                "tz": "UTC",
                "level": "DEBUG",
            },
            "network": {"retry_count": 1, "retry_backoff_sec": 0.0},
            "credentials": {"api_key": "test", "api_secret": "test"},
        }
        return cfg

    # ------------------------------------------------------------------
    # Legacy mode — high ADX, positive slope → "trend"
    # ------------------------------------------------------------------

    def test_regime_legacy_high_adx_returns_trend(self):
        """
        Strong monotonic uptrend drives ADX well above 20 and EMA200 slope
        is positive.
        Expected: ("trend", adx) where adx > 20.
        """
        cfg = self._cfg()
        n = 300
        # Steep uptrend with small wobble to avoid degenerate ATR=0.
        base = np.linspace(100, 500, n)
        wobble = np.sin(np.linspace(0, 40, n)) * 2.0
        closes = base + wobble
        df = _make_df(closes, noise_amplitude=2.0)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertEqual(regime, "trend")
        self.assertFalse(math.isnan(adx_v))
        self.assertGreater(adx_v, 20.0)

    # ------------------------------------------------------------------
    # Legacy mode — low ADX → "range"
    # ------------------------------------------------------------------

    def test_regime_legacy_low_adx_returns_range(self):
        """
        Perfectly alternating price (201 / 199 / 201 / 199 ...):
        - +DM and -DM cancel each other exactly → DX ≈ 0 → ADX ≈ 0
        - Empirically measured ADX ≈ 3.7, well below threshold of 20
        Expected: ("range", adx) where adx <= 20.
        """
        cfg = self._cfg()
        n = 300
        # Alternating bars: each up-move is cancelled by the next down-move.
        closes = np.where(np.arange(n) % 2 == 0, 201.0, 199.0).astype(float)
        df = _make_df(closes, noise_amplitude=0.0)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertEqual(regime, "range")
        self.assertFalse(math.isnan(adx_v))
        self.assertLessEqual(adx_v, 20.0)

    # ------------------------------------------------------------------
    # Legacy mode — adx_v is a float (not nan)
    # ------------------------------------------------------------------

    def test_regime_legacy_returns_numeric_adx(self):
        """
        Legacy mode always returns a numeric (non-nan) ADX value in the
        second element of the tuple.
        """
        cfg = self._cfg()
        n = 300
        base = np.linspace(100, 400, n)
        wobble = np.sin(np.linspace(0, 30, n)) * 3.0
        closes = base + wobble
        df = _make_df(closes, noise_amplitude=2.0)

        with patch.object(bot, "fetch_ohlc", return_value=df):
            _regime, adx_v = bot.regime_filter(
                exchange=None,
                symbol="BTC/USDC",
                cfg=cfg,
            )

        self.assertFalse(math.isnan(adx_v))
        self.assertIsInstance(adx_v, float)


if __name__ == "__main__":
    unittest.main()
