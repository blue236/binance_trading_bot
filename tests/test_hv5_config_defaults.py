import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class TestHV5ConfigDefaults(unittest.TestCase):
    def test_config_aggressive_keeps_hv5_defaults(self):
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        st = (cfg.get("strategy") or {})
        ag_st = ((cfg.get("aggressive") or {}).get("strategy") or {})

        self.assertEqual(st.get("mode"), "h_v5_b_plus_breakeven_ema100")
        self.assertEqual(ag_st.get("mode"), "h_v5_b_plus_breakeven_ema100")
        self.assertEqual(int(st.get("donchian_len")), 40)
        self.assertEqual(int(ag_st.get("donchian_len")), 40)
        self.assertEqual(float(ag_st.get("atr_trail_mult")), 8.0)

    def test_structural_exit_timeframe_defaults_to_daily(self):
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        tmpl = yaml.safe_load((ROOT / "config.template.yaml").read_text(encoding="utf-8"))

        self.assertEqual((cfg.get("strategy") or {}).get("structural_exit_timeframe"), "1d")
        self.assertEqual((tmpl.get("strategy") or {}).get("structural_exit_timeframe"), "1d")


if __name__ == "__main__":
    unittest.main()
