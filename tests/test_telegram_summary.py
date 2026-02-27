import sys
import types
import unittest
from datetime import datetime, timezone

# Stub heavy optional dependencies so we can import main.py in a lightweight test env.
for name in ["pandas", "numpy", "yaml", "ccxt", "credentials", "telegram"]:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

# Minimal attributes used by main.py imports.
sys.modules["credentials"].load_or_prompt_credentials = lambda: {}
sys.modules["ta"] = types.ModuleType("ta")
sys.modules["ta.volatility"] = types.ModuleType("ta.volatility")
sys.modules["ta.trend"] = types.ModuleType("ta.trend")
sys.modules["ta.momentum"] = types.ModuleType("ta.momentum")
sys.modules["ta.volatility"].AverageTrueRange = object
sys.modules["ta.volatility"].BollingerBands = object
sys.modules["ta.trend"].EMAIndicator = object
sys.modules["ta.trend"].ADXIndicator = object
sys.modules["ta.momentum"].RSIIndicator = object

import main


class TestTelegramSummaryText(unittest.TestCase):
    def test_summary_contains_operational_sections_and_requested_fields(self):
        cfg = {
            "general": {"aggressive_mode": False, "dry_run": True},
            "risk": {
                "per_trade_risk_pct": 0.4,
                "daily_loss_stop_pct": 2.5,
                "max_concurrent_positions": 3,
                "cooldown_hours": 8,
            },
            "alerts": {"enable_trade_approval": True},
            "logging": {"tz": "UTC"},
        }
        state = {
            "runtime_mode": "normal",
            "bot_paused": False,
            "positions": {"BTC/USDT": {"qty": 0.01}},
            "cooldowns": {
                "ETH/USDT": "2026-02-27T06:00:00+00:00",
                "XRP/USDT": "2026-02-26T12:00:00+00:00",
            },
            "pending_change": {"cmd": "setrisk"},
            "session": {"date": "2026-02-27", "equity_start": 1000.0},
            "runtime_health": {
                "network": {"last_label": "ok", "consecutive_failures": 0},
                "last_loop_at": "2026-02-27T06:59:55+00:00",
            },
        }

        txt = main._summary_text(
            cfg,
            state,
            equity_now=1050.0,
            base_ccy="USDT",
            now_ts=datetime(2026, 2, 27, 7, 0, 0, tzinfo=timezone.utc),
        )

        self.assertIn("Mode:", txt)
        self.assertIn("Equity:", txt)
        self.assertIn("Profit-rate:", txt)
        self.assertIn("session PnL: +50.00 USDT (+5.00%)", txt)
        self.assertIn("Positions: 1/3", txt)
        self.assertIn("cooldown_active=1", txt)
        self.assertIn("Risk gate:", txt)
        self.assertIn("pending_change=YES", txt)
        self.assertIn("Health:", txt)
        self.assertIn("last_loop=2026-02-27T06:59:55+00:00", txt)

    def test_summary_handles_missing_session_data(self):
        cfg = {
            "general": {"aggressive_mode": True, "dry_run": False},
            "risk": {"daily_loss_stop_pct": 3.0, "max_concurrent_positions": 0, "cooldown_hours": 0},
            "alerts": {"enable_trade_approval": False},
            "logging": {"tz": "UTC"},
        }
        state = {"positions": {}, "runtime_health": {"network": {}}}

        txt = main._summary_text(
            cfg,
            state,
            equity_now=999.5,
            base_ccy="USDT",
            now_ts=datetime(2026, 2, 27, 7, 0, 0, tzinfo=timezone.utc),
        )

        self.assertIn("RUNNING / aggressive", txt)
        self.assertIn("approval=OFF", txt)
        self.assertIn("Profit-rate: session PnL: n/a", txt)
        self.assertIn("Positions: 0/-", txt)


if __name__ == "__main__":
    unittest.main()
