import threading
import time
import unittest
from unittest.mock import patch

import webapp.app as appmod


class TestAIStatusMapping(unittest.TestCase):
    def test_ai_status_includes_button_guards_when_running(self):
        with patch.object(appmod, "_is_ai_running", return_value=True), \
             patch.object(appmod, "_tail_text", return_value=""), \
             patch.object(appmod, "_ai_network_health", return_value={"label": "ok"}):
            body = appmod.ai_status()

        self.assertTrue(body["running"])
        self.assertFalse(body["can_start"])
        self.assertTrue(body["can_stop"])

    def test_ai_status_includes_button_guards_when_stopped(self):
        with patch.object(appmod, "_is_ai_running", return_value=False), \
             patch.object(appmod, "_tail_text", return_value=""), \
             patch.object(appmod, "_ai_network_health", return_value={"label": "ok"}):
            body = appmod.ai_status()

        self.assertFalse(body["running"])
        self.assertTrue(body["can_start"])
        self.assertFalse(body["can_stop"])


class TestAIStartStopIdempotency(unittest.TestCase):
    def test_start_noop_when_already_running_has_no_notification(self):
        with patch.object(appmod, "_is_ai_running", return_value=True), \
             patch.object(appmod, "_start_ai_bot") as start_mock, \
             patch.object(appmod, "_notify_telegram") as notify_mock:
            result = appmod._set_ai_running(True, "Web UI")

        self.assertTrue(result["ok"])
        self.assertTrue(result["noop"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["message"], "already_running")
        start_mock.assert_not_called()
        notify_mock.assert_not_called()

    def test_stop_noop_when_already_stopped_has_no_notification(self):
        with patch.object(appmod, "_is_ai_running", return_value=False), \
             patch.object(appmod, "_stop_ai_bot") as stop_mock, \
             patch.object(appmod, "_notify_telegram") as notify_mock:
            result = appmod._set_ai_running(False, "Web UI")

        self.assertTrue(result["ok"])
        self.assertTrue(result["noop"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["message"], "already_stopped")
        stop_mock.assert_not_called()
        notify_mock.assert_not_called()

    def test_start_endpoint_noop_no_duplicate_notification(self):
        with patch.object(appmod, "_set_ai_running", return_value={
            "ok": True,
            "running": True,
            "changed": False,
            "noop": True,
            "message": "already_running",
            "can_start": False,
            "can_stop": True,
        }), patch.object(appmod, "_tail_text", return_value=""):
            body = appmod.ai_start()

        self.assertTrue(body["noop"])
        self.assertEqual(body["message"], "already_running")

    def test_concurrent_start_requests_are_race_safe_single_notification(self):
        state = {"running": False}

        def fake_is_running():
            return state["running"]

        def fake_start():
            # Emulate slow process startup so concurrent calls overlap.
            time.sleep(0.05)
            state["running"] = True

        notifications = []

        def fake_notify(msg):
            notifications.append(msg)

        with patch.object(appmod, "_is_ai_running", side_effect=fake_is_running), \
             patch.object(appmod, "_start_ai_bot", side_effect=fake_start), \
             patch.object(appmod, "_notify_telegram", side_effect=fake_notify):
            results = []

            def worker():
                results.append(appmod._set_ai_running(True, "Web UI"))

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(len(results), 2)
        changed = [r for r in results if r["changed"]]
        noop = [r for r in results if r["noop"]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(len(noop), 1)
        self.assertEqual(len(notifications), 1)


if __name__ == "__main__":
    unittest.main()
