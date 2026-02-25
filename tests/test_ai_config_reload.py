import base64
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import webapp.app as appmod


class DummyRequest:
    def __init__(self, headers):
        self.headers = headers


class TestAIConfigSaveRuntimeReload(unittest.TestCase):
    def test_ai_config_save_applies_runtime_reload(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yaml"
            with patch.object(appmod, "AI_CONFIG_PATH", cfg_path), \
                 patch.object(appmod, "_apply_ai_config_runtime_reload", return_value={"applied": True, "running": True, "message": "bot_restarted"}) as reload_mock:
                payload = {
                    "text": yaml.safe_dump({
                        "general": {"symbols": ["BTC/USDT"]},
                        "credentials": {"api_key": "abc", "api_secret": "def"},
                        "alerts": {"telegram_bot_token": "t", "telegram_chat_id": "1"},
                    }, sort_keys=False)
                }
                body = appmod.ai_config_save(payload)
                self.assertTrue(body["ok"])
                self.assertEqual(body["runtime_reload"]["message"], "bot_restarted")
                reload_mock.assert_called_once()

                saved = yaml.safe_load(cfg_path.read_text())
                self.assertEqual(saved["credentials"]["api_key"], "")
                self.assertEqual(saved["credentials"]["api_secret"], "")
                self.assertEqual(saved["alerts"]["telegram_bot_token"], "")
                self.assertEqual(saved["alerts"]["telegram_chat_id"], "")

    def test_ai_config_save2_applies_runtime_reload(self):
        encoded = base64.b64encode(yaml.safe_dump({"general": {"dry_run": True}}).encode("utf-8")).decode("utf-8")
        with patch.object(appmod, "_save_ai_config_text") as save_mock, \
             patch.object(appmod, "_apply_ai_config_runtime_reload", return_value={"applied": False, "running": False, "message": "bot_not_running"}) as reload_mock:
            req = DummyRequest({"x-btb-ai-config": encoded})
            body = asyncio.run(appmod.ai_config_save_v2(req))
            self.assertTrue(body["ok"])
            self.assertEqual(body["runtime_reload"]["message"], "bot_not_running")
            save_mock.assert_called_once()
            reload_mock.assert_called_once()


class TestApplyAIConfigRuntimeReload(unittest.TestCase):
    def test_runtime_reload_restart_when_running(self):
        with patch.object(appmod, "_is_ai_running", side_effect=[True, True]), \
             patch.object(appmod, "_stop_ai_bot") as stop_mock, \
             patch.object(appmod, "_wait_ai_stopped", return_value=True) as wait_mock, \
             patch.object(appmod, "_start_ai_bot") as start_mock:
            result = appmod._apply_ai_config_runtime_reload()

        self.assertTrue(result["applied"])
        stop_mock.assert_called_once()
        wait_mock.assert_called_once_with(6.0)
        start_mock.assert_called_once()

    def test_runtime_reload_noop_when_not_running(self):
        with patch.object(appmod, "_is_ai_running", return_value=False), \
             patch.object(appmod, "_stop_ai_bot") as stop_mock, \
             patch.object(appmod, "_start_ai_bot") as start_mock:
            result = appmod._apply_ai_config_runtime_reload()

        self.assertFalse(result["applied"])
        self.assertEqual(result["message"], "bot_not_running")
        stop_mock.assert_not_called()
        start_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
