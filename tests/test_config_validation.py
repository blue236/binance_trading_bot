"""TEST-03: Tests for deep_merge(), apply_aggressive_overrides(), and validate_config()."""
import logging
import sys
import unittest
from pathlib import Path

# Ensure project root is on the path so `import main` works when running
# pytest from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as bot


class TestDeepMerge(unittest.TestCase):
    def test_deep_merge_nested_override_preserves_siblings(self):
        # Arrange
        base = {"risk": {"per_trade": 0.5, "daily": 2.0}, "other": "x"}

        # Act
        bot.deep_merge(base, {"risk": {"per_trade": 0.9}})

        # Assert
        self.assertEqual(base["risk"]["per_trade"], 0.9)
        self.assertEqual(base["risk"]["daily"], 2.0)   # untouched sibling
        self.assertEqual(base["other"], "x")            # untouched top-level key

    def test_deep_merge_scalar_overwrite(self):
        # Arrange
        base = {"a": 1}

        # Act
        bot.deep_merge(base, {"a": 2})

        # Assert
        self.assertEqual(base["a"], 2)

    def test_deep_merge_adds_new_key(self):
        # Arrange
        base = {"a": 1}

        # Act
        bot.deep_merge(base, {"b": 2})

        # Assert
        self.assertEqual(base["a"], 1)
        self.assertEqual(base["b"], 2)

    def test_deep_merge_empty_updates_is_noop(self):
        # Arrange
        base = {"a": 1}

        # Act
        bot.deep_merge(base, {})

        # Assert
        self.assertEqual(base, {"a": 1})

    def test_deep_merge_none_updates_is_noop(self):
        # Arrange
        base = {"a": 1}

        # Act
        bot.deep_merge(base, None)

        # Assert
        self.assertEqual(base, {"a": 1})

    def test_deep_merge_returns_base(self):
        # Arrange
        base = {"a": 1}

        # Act
        result = bot.deep_merge(base, {"a": 2})

        # Assert — return value is the same object as base
        self.assertIs(result, base)

    def test_deep_merge_overwrites_list_not_merged(self):
        # Non-dict values (including lists) must be overwritten, not merged.
        base = {"items": [1, 2, 3]}
        bot.deep_merge(base, {"items": [4, 5]})
        self.assertEqual(base["items"], [4, 5])

    def test_deep_merge_multiple_levels_deep(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        bot.deep_merge(base, {"a": {"b": {"c": 99}}})
        self.assertEqual(base["a"]["b"]["c"], 99)
        self.assertEqual(base["a"]["b"]["d"], 2)


class TestApplyAggressiveOverrides(unittest.TestCase):
    def _make_cfg(self, dry_run, aggressive_mode, base_pct=0.5, override_pct=0.9):
        return {
            "general": {"dry_run": dry_run, "aggressive_mode": aggressive_mode},
            "risk": {"per_trade_risk_pct": base_pct},
            "aggressive": {"risk": {"per_trade_risk_pct": override_pct}},
        }

    def test_aggressive_overrides_apply_when_both_true(self):
        # Arrange
        cfg = self._make_cfg(dry_run=True, aggressive_mode=True)

        # Act
        result = bot.apply_aggressive_overrides(cfg)

        # Assert
        self.assertEqual(result["risk"]["per_trade_risk_pct"], 0.9)

    def test_aggressive_overrides_skipped_when_not_dry_run(self):
        # Arrange
        cfg = self._make_cfg(dry_run=False, aggressive_mode=True)

        # Act
        result = bot.apply_aggressive_overrides(cfg)

        # Assert — base value must be unchanged
        self.assertEqual(result["risk"]["per_trade_risk_pct"], 0.5)

    def test_aggressive_overrides_skipped_when_mode_false(self):
        # Arrange
        cfg = self._make_cfg(dry_run=True, aggressive_mode=False)

        # Act
        result = bot.apply_aggressive_overrides(cfg)

        # Assert
        self.assertEqual(result["risk"]["per_trade_risk_pct"], 0.5)

    def test_aggressive_overrides_skipped_when_both_false(self):
        # Arrange
        cfg = self._make_cfg(dry_run=False, aggressive_mode=False)

        # Act
        result = bot.apply_aggressive_overrides(cfg)

        # Assert
        self.assertEqual(result["risk"]["per_trade_risk_pct"], 0.5)

    def test_aggressive_overrides_returns_cfg(self):
        # The function must return cfg in both branches.
        cfg = self._make_cfg(dry_run=True, aggressive_mode=True)
        result = bot.apply_aggressive_overrides(cfg)
        self.assertIs(result, cfg)

    def test_aggressive_overrides_no_aggressive_key_is_noop(self):
        # If cfg has no "aggressive" key the call must not raise.
        cfg = {
            "general": {"dry_run": True, "aggressive_mode": True},
            "risk": {"per_trade_risk_pct": 0.5},
        }
        result = bot.apply_aggressive_overrides(cfg)
        self.assertEqual(result["risk"]["per_trade_risk_pct"], 0.5)


class TestValidateConfig(unittest.TestCase):
    # ── raises ──────────────────────────────────────────────────────────────

    def test_validate_config_negative_daily_stop_raises(self):
        cfg = {"risk": {"daily_loss_stop_pct": -2.0, "per_trade_risk_pct": 0.5}}
        with self.assertRaises(ValueError):
            bot.validate_config(cfg)

    def test_validate_config_zero_per_trade_raises(self):
        cfg = {"risk": {"daily_loss_stop_pct": 2.0, "per_trade_risk_pct": 0.0}}
        with self.assertRaises(ValueError):
            bot.validate_config(cfg)

    def test_validate_config_negative_per_trade_raises(self):
        cfg = {"risk": {"daily_loss_stop_pct": 2.0, "per_trade_risk_pct": -1.0}}
        with self.assertRaises(ValueError):
            bot.validate_config(cfg)

    def test_validate_config_non_numeric_daily_stop_raises(self):
        cfg = {"risk": {"daily_loss_stop_pct": "bad", "per_trade_risk_pct": 0.5}}
        with self.assertRaises(ValueError):
            bot.validate_config(cfg)

    def test_validate_config_non_numeric_per_trade_raises(self):
        cfg = {"risk": {"daily_loss_stop_pct": 2.0, "per_trade_risk_pct": "bad"}}
        with self.assertRaises(ValueError):
            bot.validate_config(cfg)

    # ── no raise ────────────────────────────────────────────────────────────

    def test_validate_config_missing_risk_keys_ok(self):
        # Neither key present — must not raise.
        cfg = {"risk": {}}
        bot.validate_config(cfg)  # should not raise

    def test_validate_config_missing_risk_section_ok(self):
        cfg = {}
        bot.validate_config(cfg)  # should not raise

    def test_validate_config_valid_values_ok(self):
        cfg = {"risk": {"daily_loss_stop_pct": 2.0, "per_trade_risk_pct": 0.5}}
        bot.validate_config(cfg)  # should not raise

    def test_validate_config_zero_daily_stop_ok(self):
        # Zero daily stop is non-negative so it should not raise.
        cfg = {"risk": {"daily_loss_stop_pct": 0.0, "per_trade_risk_pct": 0.5}}
        bot.validate_config(cfg)  # should not raise

    def test_validate_config_numeric_string_coercion(self):
        # Numeric strings must be coerced and validated rather than raising.
        cfg = {"risk": {"daily_loss_stop_pct": "2.0", "per_trade_risk_pct": "0.5"}}
        bot.validate_config(cfg)  # should not raise

    # ── warnings ────────────────────────────────────────────────────────────

    def test_validate_config_high_values_warn_not_raise(self):
        cfg = {"risk": {"daily_loss_stop_pct": 15.0, "per_trade_risk_pct": 6.0}}
        with self.assertLogs("bot", level="WARNING") as log_ctx:
            bot.validate_config(cfg)
        # Both high-value warnings should be present.
        combined = "\n".join(log_ctx.output)
        self.assertIn("daily_loss_stop_pct", combined)
        self.assertIn("per_trade_risk_pct", combined)

    def test_validate_config_high_daily_stop_only_warns(self):
        cfg = {"risk": {"daily_loss_stop_pct": 11.0}}
        with self.assertLogs("bot", level="WARNING") as log_ctx:
            bot.validate_config(cfg)
        self.assertTrue(
            any("daily_loss_stop_pct" in line for line in log_ctx.output)
        )

    def test_validate_config_high_per_trade_only_warns(self):
        cfg = {"risk": {"per_trade_risk_pct": 5.5}}
        with self.assertLogs("bot", level="WARNING") as log_ctx:
            bot.validate_config(cfg)
        self.assertTrue(
            any("per_trade_risk_pct" in line for line in log_ctx.output)
        )

    def test_validate_config_boundary_values_no_warn(self):
        # Exactly at the threshold boundaries must not produce warnings.
        cfg = {"risk": {"daily_loss_stop_pct": 10.0, "per_trade_risk_pct": 5.0}}
        # assertLogs raises AssertionError if NO logs are emitted, which is
        # what we want here: no warnings should be logged.
        with self.assertRaises(AssertionError):
            with self.assertLogs("bot", level="WARNING"):
                bot.validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
