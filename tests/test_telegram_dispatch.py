import unittest
from unittest.mock import patch

import webapp.app as appmod
from telegram_shared import HELP_TEXT


class TestTelegramDispatch(unittest.TestCase):
    def _base_cfg(self):
        return {
            "alerts": {"telegram_owner_user_id": "42", "telegram_polling_owner": "webapp"},
            "risk": {"per_trade_risk_pct": 0.5, "max_concurrent_positions": 2},
            "general": {"base_currency": "USDT", "dry_run": True},
            "logging": {"csv_dir": "./logs"},
        }

    def test_status_command_routes_and_responds(self):
        updates = [{
            "update_id": 100,
            "message": {
                "chat": {"id": "1"},
                "from": {"id": "42"},
                "text": "/status",
            },
        }]
        with patch.object(appmod, "_load_main_cfg", return_value=self._base_cfg()), \
             patch.object(appmod, "_load_main_state", return_value={"positions": {}}), \
             patch.object(appmod, "_load_secrets", return_value={"telegram_bot_token": "t", "telegram_chat_id": "1"}), \
             patch.object(appmod, "_load_offset", return_value=None), \
             patch.object(appmod, "_telegram_get_updates", return_value=updates), \
             patch.object(appmod, "_server_status_text", return_value="status-ok"), \
             patch.object(appmod, "_notify_telegram") as notify_mock, \
             patch.object(appmod, "_save_offset") as save_offset_mock:
            appmod._poll_server_telegram_commands()

        notify_mock.assert_called_once_with("status-ok")
        save_offset_mock.assert_called_once_with(101)

    def test_owner_only_command_denied_for_non_owner(self):
        updates = [{
            "update_id": 200,
            "message": {
                "chat": {"id": "1"},
                "from": {"id": "999"},
                "text": "/start",
            },
        }]
        with patch.object(appmod, "_load_main_cfg", return_value=self._base_cfg()), \
             patch.object(appmod, "_load_main_state", return_value={}), \
             patch.object(appmod, "_load_secrets", return_value={"telegram_bot_token": "t", "telegram_chat_id": "1"}), \
             patch.object(appmod, "_load_offset", return_value=None), \
             patch.object(appmod, "_telegram_get_updates", return_value=updates), \
             patch.object(appmod, "_set_ai_running") as set_running_mock, \
             patch.object(appmod, "_notify_telegram") as notify_mock:
            appmod._poll_server_telegram_commands()

        set_running_mock.assert_not_called()
        notify_mock.assert_called_once_with("Owner-only command.")

    def test_duplicate_handler_conflict_prevented_by_poll_owner(self):
        cfg = self._base_cfg()
        cfg["alerts"]["telegram_polling_owner"] = "bot"
        with patch.object(appmod, "_load_main_cfg", return_value=cfg), \
             patch.object(appmod, "_telegram_get_updates") as updates_mock:
            appmod._poll_server_telegram_commands()
        updates_mock.assert_not_called()

    def test_help_command_uses_unified_text(self):
        updates = [{
            "update_id": 300,
            "message": {
                "chat": {"id": "1"},
                "from": {"id": "42"},
                "text": "/help",
            },
        }]
        with patch.object(appmod, "_load_main_cfg", return_value=self._base_cfg()), \
             patch.object(appmod, "_load_main_state", return_value={}), \
             patch.object(appmod, "_load_secrets", return_value={"telegram_bot_token": "t", "telegram_chat_id": "1"}), \
             patch.object(appmod, "_load_offset", return_value=None), \
             patch.object(appmod, "_telegram_get_updates", return_value=updates), \
             patch.object(appmod, "_notify_telegram") as notify_mock:
            appmod._poll_server_telegram_commands()

        notify_mock.assert_called_once_with(HELP_TEXT)


if __name__ == "__main__":
    unittest.main()
