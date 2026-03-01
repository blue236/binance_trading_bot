import unittest
from unittest.mock import patch

import webapp.app as appmod


class _DummyCfg:
    symbols = ["BTC/USDT"]
    timeframe = "1h"
    history_limit = 300
    starting_capital = 10000.0
    fee_rate = 0.001


class TestChartApiFeatures(unittest.TestCase):
    def test_bollinger_output_lengths_and_nones(self):
        values = [float(i) for i in range(1, 31)]
        mid, up, low = appmod._bollinger_bands(values, window=20, sigma=2.0)
        self.assertEqual(len(mid), len(values))
        self.assertEqual(len(up), len(values))
        self.assertEqual(len(low), len(values))
        self.assertTrue(all(v is None for v in up[:19]))
        self.assertIsNotNone(up[20])

    def test_get_chart_supports_timeframe_and_indicators(self):
        rows = []
        base_ts = 1700000000000
        for i in range(120):
            close = 100.0 + i * 0.5
            rows.append((base_ts + i * 3600_000, close - 1, close + 1, close - 2, close, 10_000 + i))

        with patch.object(appmod.config_mgr, "load", return_value=_DummyCfg()), \
             patch.object(appmod, "_load_main_cfg", return_value={"strategy": {"ema_fast": 12, "ema_slow": 26}, "general": {"aggressive_mode": False}}), \
             patch.object(appmod.storage, "fetch_ohlcv", return_value=rows):
            out = appmod.get_chart("BTC/USDT", timeframe="4h", limit=100)

        self.assertEqual(out["timeframe"], "4h")
        self.assertIn("volumes", out)
        self.assertIn("highs", out)
        self.assertIn("lows", out)
        self.assertIn("indicators", out)
        self.assertIn("ema_fast", out["indicators"])
        self.assertIn("bb_upper", out["indicators"])
        self.assertEqual(len(out["labels"]), 100)
        self.assertEqual(len(out["values"]), 100)
        self.assertEqual(len(out["volumes"]), 100)


if __name__ == "__main__":
    unittest.main()
