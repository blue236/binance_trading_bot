import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import logging

import main as mainmod
import webapp.app as appmod


class DummyExchange:
    def __init__(self):
        self.markets = {
            "BTC/USDC": {"spot": True},
            "ETH/USDC": {"spot": True},
            "BTC/USDC:USDC": {"spot": False, "swap": True},
        }


class TestLogLevelPropagation(unittest.TestCase):
    def test_setup_logger_respects_configured_level_and_updates_existing_handlers(self):
        logger = logging.getLogger("bot")
        for h in list(logger.handlers):
            logger.removeHandler(h)

        with tempfile.TemporaryDirectory() as td:
            lg = mainmod.setup_logger(td, level="DEBUG")
            self.assertEqual(lg.level, logging.DEBUG)
            self.assertTrue(all(h.level == logging.DEBUG for h in lg.handlers))

            # Reconfigure same logger to INFO and ensure handler levels follow.
            lg2 = mainmod.setup_logger(td, level="INFO")
            self.assertIs(lg, lg2)
            self.assertEqual(lg2.level, logging.INFO)
            self.assertTrue(all(h.level == logging.INFO for h in lg2.handlers))


class TestEquityFetchRegression(unittest.TestCase):
    def test_fetch_equity_ignores_non_spot_and_does_not_raise_on_mixed_market_types(self):
        exchange = DummyExchange()
        balances = {"USDC": 100.0, "BTC": 1.0}

        with patch.object(mainmod, "safe_fetch_tickers", return_value={"BTC/USDC": {"last": 2.0}}) as fetch_mock:
            eq = mainmod.fetch_equity_usdt(exchange, base_ccy="USDC", balances=balances, tickers=None)

        self.assertEqual(eq, 102.0)
        fetch_mock.assert_called_once()
        requested_symbols = fetch_mock.call_args.args[1]
        self.assertEqual(requested_symbols, ["BTC/USDC"])


class TestAILogTailVisibility(unittest.TestCase):
    def test_tail_text_reflects_newly_appended_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bot.log"
            p.write_text("line1\nline2\n", encoding="utf-8")
            self.assertIn("line2", appmod._tail_text(p, 10))

            with p.open("a", encoding="utf-8") as fp:
                fp.write("State loaded: positions=0 cooldowns=0\n")
                fp.write("Bot started. dry_run=True\n")

            tail = appmod._tail_text(p, 10)
            self.assertIn("State loaded: positions=0 cooldowns=0", tail)
            self.assertIn("Bot started. dry_run=True", tail)

    def test_ai_logs_endpoint_returns_latest_tail(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bot.log"
            p.write_text("a\nb\n", encoding="utf-8")
            with patch.object(appmod, "_ai_log_path", return_value=p):
                body1 = appmod.ai_logs(lines=5)
                self.assertIn("b", body1["tail"])

                with p.open("a", encoding="utf-8") as fp:
                    fp.write("c\n")

                body2 = appmod.ai_logs(lines=5)
                self.assertIn("c", body2["tail"])


if __name__ == "__main__":
    unittest.main()
