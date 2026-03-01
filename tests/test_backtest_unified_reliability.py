import time
import unittest
from unittest.mock import patch

import webapp.app as appmod


class _DummyCfg:
    symbols = ["BTC/USDT"]
    timeframe = "1d"
    starting_capital = 10000.0
    fee_rate = 0.001


class TestBacktestUnifiedReliability(unittest.TestCase):
    def test_quick_no_data_returns_structured_result(self):
        with patch.object(appmod.config_mgr, "load", return_value=_DummyCfg()), \
             patch.object(appmod, "_load_main_cfg", return_value={"strategy": {"ema_fast": 20, "ema_slow": 60}}), \
             patch.object(appmod.backtest_service, "run_sma_crossover", side_effect=ValueError("Not enough OHLCV data")):
            body = appmod.run_backtest_unified({"mode": "quick", "symbol": "BTC/USDT", "timeframe": "1d"})

        self.assertIsInstance(body, dict)
        self.assertFalse(body["ok"])
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["engine"], "quick")
        self.assertEqual(body["results"][0]["summary"]["status"], "error")
        self.assertIn("Not enough OHLCV data", body["results"][0]["summary"].get("note", ""))

    def test_legacy_timeout_returns_structured_result(self):
        with patch.object(appmod.config_mgr, "load", return_value=_DummyCfg()), \
             patch.object(appmod, "_load_main_cfg", return_value={"strategy": {"ema_fast": 20, "ema_slow": 60}}), \
             patch.object(appmod, "_run_legacy_backtest", return_value={
                 "ok": False,
                 "returncode": 124,
                 "output": "Legacy backtest timed out after 15s",
                 "plots": [],
                 "error": "timeout",
             }):
            body = appmod.run_backtest_unified({"mode": "legacy", "symbol": "BTC/USDT", "legacy_timeout_sec": 15})

        self.assertIsInstance(body, dict)
        self.assertFalse(body["ok"])
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["engine"], "legacy")
        self.assertEqual(body["results"][0]["summary"]["status"], "error")
        self.assertIn("legacy timeout", body["results"][0]["summary"].get("note", ""))

    def test_quick_path_is_bounded_by_timeout(self):
        def very_slow(*_args, **_kwargs):
            time.sleep(5.0)
            return {"roi_pct": 1.0, "final_equity": 10100.0, "max_drawdown_pct": -1.0, "trades": 1, "equity_curve": {"labels": [], "values": []}, "markers": []}

        with patch.object(appmod.config_mgr, "load", return_value=_DummyCfg()), \
             patch.object(appmod, "_load_main_cfg", return_value={"strategy": {"ema_fast": 20, "ema_slow": 60}}), \
             patch.object(appmod.backtest_service, "run_sma_crossover", side_effect=very_slow):
            start = time.time()
            body = appmod.run_backtest_unified({
                "mode": "quick",
                "symbol": "BTC/USDT",
                "quick_timeout_sec": 1,
            })
            elapsed = time.time() - start

        self.assertLess(elapsed, 3.8)
        self.assertFalse(body["ok"])
        self.assertEqual(body["results"][0]["summary"]["status"], "error")
        self.assertIn("timeout", body["results"][0]["summary"].get("note", ""))


if __name__ == "__main__":
    unittest.main()
