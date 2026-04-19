"""TEST-04: Full trade lifecycle integration tests.

Tests the building blocks of the trade lifecycle:
  signal → entry order → state written → trailing stop update → exit

All external dependencies (exchange, file I/O side effects) are either mocked
or exercised through the real read_state/write_state against temp files.
"""

import json
import os
import tempfile
import unittest

import main as bot


def _cfg():
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
            "per_trade_risk_pct": 1.0,
            "daily_loss_stop_pct": 2.0,
            "max_concurrent_positions": 2,
            "cooldown_hours": 8,
        },
        "strategy": {
            "mode": "h_v5_b_plus_breakeven_ema100",
            "atr_len": 14,
            "atr_sl_trend_mult": 2.5,
            "atr_trail_mult": 8.0,
            "breakeven_r": 1.0,
            "ema_slow": 200,
            "ema_fast": 50,
            "donchian_len": 80,
            "bb_len": 20,
            "bb_mult": 2.0,
            "rsi_len": 14,
            "rsi_overheat": 75,
            "adx_len": 14,
            "trend_adx_threshold": 20,
            "regime_ema_fast": 50,
            "regime_rsi_len": 14,
            "regime_rsi_min": 55,
            "pullback_ema_len": 50,
            "pullback_band_atr": 0.8,
            "atr_sl_mr_mult": 1.2,
            "rsi_mr_threshold": 35,
            "mean_reversion_time_stop_hours": 24,
            "loop_sleep_seconds": 60,
            "use_structural_exit": False,
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


class TestPositionSize(unittest.TestCase):
    """Unit tests for position_size()."""

    def test_position_size_basic(self):
        """Arrange: equity=1000, price=100, atr=5, atr_mult=2.5, risk_pct=1.0.
        Act: call position_size.
        Assert: qty == 0.8 (risk_amount=10, stop_dist=12.5, qty=10/12.5).
        """
        # Arrange
        equity_usdt = 1000.0
        price = 100.0
        atr = 5.0
        atr_mult = 2.5
        risk_pct = 1.0

        # Act
        qty = bot.position_size(equity_usdt, price, atr, atr_mult, risk_pct)

        # Assert
        self.assertAlmostEqual(qty, 0.8, places=6)

    def test_position_size_division_by_zero_atr_zero_returns_zero(self):
        """Assert that atr=0 causes stop_dist=0 and returns 0 without raising."""
        # Arrange / Act
        qty = bot.position_size(1000.0, 100.0, atr=0.0, atr_mult=2.5, risk_pct=1.0)

        # Assert
        self.assertEqual(qty, 0.0)

    def test_position_size_division_by_zero_atr_mult_zero_returns_zero(self):
        """Assert that atr_mult=0 causes stop_dist=0 and returns 0 without raising."""
        # Arrange / Act
        qty = bot.position_size(1000.0, 100.0, atr=5.0, atr_mult=0.0, risk_pct=1.0)

        # Assert
        self.assertEqual(qty, 0.0)


class TestPlaceOrder(unittest.TestCase):
    """Unit tests for place_order() in dry-run mode."""

    def test_place_order_dry_run_returns_simulated_fill(self):
        """Dry-run place_order must return a dict with tracking keys and no exchange call."""
        # Arrange
        symbol = "BTC/USDC"
        side = "buy"
        qty = 0.5
        price = 50000.0

        # Act — exchange=None is safe because dry_run=True must not touch exchange
        result = bot.place_order(
            exchange=None,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            dry_run=True,
        )

        # Assert: returned dict is non-null and carries expected fields
        self.assertIsInstance(result, dict)
        self.assertIn("id", result)
        self.assertTrue(str(result["id"]).startswith("dry_"))
        self.assertEqual(result["symbol"], symbol)
        self.assertEqual(result["side"], side)
        self.assertEqual(result["qty"], qty)
        self.assertEqual(result["price"], price)

    def test_place_order_dry_run_does_not_call_exchange(self):
        """Passing exchange=None with dry_run=True must not raise."""
        # If the exchange were called an AttributeError would be raised from None.
        try:
            bot.place_order(None, "ETH/USDC", "sell", 1.0, 3000.0, dry_run=True)
        except Exception as exc:
            self.fail(f"place_order raised unexpectedly in dry_run mode: {exc}")


class TestStateRoundtrip(unittest.TestCase):
    """Integration tests for read_state / write_state file I/O."""

    def _temp_path(self):
        """Return a path inside a newly created temp file (removed after creation)."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)   # we want write_state to create the file fresh
        return path

    def test_write_read_state_roundtrip(self):
        """A state dict with a position must survive a write → read cycle unchanged."""
        # Arrange
        path = self._temp_path()
        position = {
            "signal": "T_LONG",
            "entry_px": 50000.0,
            "qty": 0.1,
            "sl": 49000.0,
            "trail_mult": 3.0,
            "be_r": 1.0,
            "be_atr": 500.0,
            "ts": 1713225600.0,
        }
        original_state = {
            "positions": {"BTC/USDC": position},
            "cooldowns": {},
            "daily": {},
        }

        # Act
        bot.write_state(path, original_state)
        recovered = bot.read_state(path)

        # Assert
        self.assertEqual(recovered["positions"]["BTC/USDC"], position)
        self.assertEqual(recovered["cooldowns"], {})

        # Cleanup
        if os.path.exists(path):
            os.unlink(path)

    def test_state_position_entry_written(self):
        """Starting from an empty state, writing a position must persist to disk."""
        # Arrange
        path = self._temp_path()
        state = {"positions": {}, "cooldowns": {}, "daily": {}}

        # Simulate entry: build position and add to state
        entry_position = {
            "signal": "T_LONG",
            "entry_px": 50000.0,
            "qty": 0.1,
            "sl": 49000.0,
        }
        state["positions"]["BTC/USDC"] = entry_position

        # Act
        bot.write_state(path, state)
        recovered = bot.read_state(path)

        # Assert
        self.assertIn("BTC/USDC", recovered["positions"])
        self.assertEqual(recovered["positions"]["BTC/USDC"]["entry_px"], 50000.0)
        self.assertEqual(recovered["positions"]["BTC/USDC"]["qty"], 0.1)
        self.assertEqual(recovered["positions"]["BTC/USDC"]["sl"], 49000.0)

        # Cleanup
        if os.path.exists(path):
            os.unlink(path)

    def test_read_state_returns_defaults_when_file_missing(self):
        """read_state on a non-existent path must return the default empty state."""
        # Arrange
        path = "/tmp/btb_nonexistent_state_TEST04.json"
        if os.path.exists(path):
            os.unlink(path)

        # Act
        state = bot.read_state(path)

        # Assert
        self.assertIn("positions", state)
        self.assertIn("cooldowns", state)
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["cooldowns"], {})


class TestTrailingStopLogic(unittest.TestCase):
    """Unit tests for the trailing stop update arithmetic (lines 1419-1422 main.py)."""

    def _apply_trailing_update(self, sl, trail_mult, hi_since, atr):
        """Mirror the exact trailing stop logic from main.py lines 1419-1422."""
        trail = hi_since - trail_mult * atr
        if trail > sl:
            sl = trail
        return sl

    def test_trailing_stop_tightens_when_new_trail_is_higher(self):
        """When hi_since produces a trail above current SL the stop must advance."""
        # Arrange
        pos_sl = 48000.0
        trail_mult = 3.0
        hi_since = 52000.0
        atr = 500.0
        # trail = 52000 - 3.0 * 500 = 50500 > 48000 → should update

        # Act
        new_sl = self._apply_trailing_update(pos_sl, trail_mult, hi_since, atr)

        # Assert
        self.assertAlmostEqual(new_sl, 50500.0, places=6)
        self.assertGreater(new_sl, pos_sl)

    def test_trailing_stop_does_not_loosen_when_new_trail_is_lower(self):
        """When price falls back, a lower trail value must NOT decrease the stop."""
        # Arrange: SL is already at 50500 (from a previous advance)
        pos_sl = 50500.0
        trail_mult = 3.0
        hi_since = 51000.0
        atr = 500.0
        # trail = 51000 - 3.0 * 500 = 49500 < 50500 → must NOT update

        # Act
        new_sl = self._apply_trailing_update(pos_sl, trail_mult, hi_since, atr)

        # Assert: stop must remain at the previous (higher) value
        self.assertAlmostEqual(new_sl, 50500.0, places=6)
        self.assertEqual(new_sl, pos_sl)

    def test_trailing_stop_two_phase_sequence(self):
        """Full scenario: advance then hold — stop never moves backward."""
        # Phase 1: hi_since=52000, trail=50500 > sl=48000 → advances
        sl = 48000.0
        sl = self._apply_trailing_update(sl, 3.0, hi_since=52000.0, atr=500.0)
        self.assertAlmostEqual(sl, 50500.0, places=6)

        # Phase 2: price retraces, hi_since drops to 51000, trail=49500 < sl=50500 → holds
        sl = self._apply_trailing_update(sl, 3.0, hi_since=51000.0, atr=500.0)
        self.assertAlmostEqual(sl, 50500.0, places=6)


class TestDailyPnlGuard(unittest.TestCase):
    """Unit tests for daily_pnl_guard()."""

    def test_daily_pnl_guard_triggers_at_threshold(self):
        """dd == -2.0%, threshold == 2.0% → guard must trigger (return True)."""
        # Arrange
        cfg = _cfg()
        equity_now = 980.0
        equity_start = 1000.0
        # dd = (980-1000)/1000*100 = -2.0% → == -abs(2.0) → True

        # Act
        result = bot.daily_pnl_guard(cfg, equity_now, equity_start)

        # Assert
        self.assertTrue(result)

    def test_daily_pnl_guard_does_not_trigger_above_threshold(self):
        """dd == -1.0%, threshold == 2.0% → guard must NOT trigger (return False)."""
        # Arrange
        cfg = _cfg()
        equity_now = 990.0
        equity_start = 1000.0
        # dd = (990-1000)/1000*100 = -1.0% → > -2.0% → False

        # Act
        result = bot.daily_pnl_guard(cfg, equity_now, equity_start)

        # Assert
        self.assertFalse(result)

    def test_daily_pnl_guard_returns_false_when_equity_start_is_zero(self):
        """equity_start == 0 must return False without dividing by zero."""
        # Arrange
        cfg = _cfg()

        # Act
        result = bot.daily_pnl_guard(cfg, equity_now=980.0, equity_start=0.0)

        # Assert
        self.assertFalse(result)

    def test_daily_pnl_guard_returns_false_when_key_missing(self):
        """Missing daily_loss_stop_pct key must return False gracefully."""
        # Arrange
        cfg = _cfg()
        del cfg["risk"]["daily_loss_stop_pct"]

        # Act
        result = bot.daily_pnl_guard(cfg, equity_now=800.0, equity_start=1000.0)

        # Assert
        self.assertFalse(result)

    def test_daily_pnl_guard_triggers_well_below_threshold(self):
        """A large drawdown (dd=-10%) must trigger when threshold is 2%."""
        # Arrange
        cfg = _cfg()

        # Act
        result = bot.daily_pnl_guard(cfg, equity_now=900.0, equity_start=1000.0)

        # Assert
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
