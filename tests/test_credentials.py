"""Tests for credentials.py — load/save/fallback chain.

TEST-05: credential loading fallback chain
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import credentials as credentials_module
from credentials import (
    _read_encrypted,
    _write_encrypted,
    load_credentials,
    save_credentials,
)

# Env var names used by the module
_CRED_ENV_VARS = {
    "BINANCE_API_KEY": "",
    "BINANCE_API_SECRET": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "BTB_CREDENTIALS_PASSPHRASE": "",
}

_SAMPLE_DATA = {
    "api_key": "test_api_key",
    "api_secret": "test_api_secret",
    "telegram_bot_token": "test_tg_token",
    "telegram_chat_id": "test_tg_chat",
}

_SAMPLE_PASSPHRASE = "s3cr3t_passphrase_for_tests"


class TestCredentialsLoadFallback(unittest.TestCase):
    """Unit tests for the credential loading fallback chain."""

    # ------------------------------------------------------------------
    # Test 1: Env vars override file credentials
    # ------------------------------------------------------------------
    def test_env_var_overrides_file_credentials(self):
        """Arrange: plaintext file with api_key="from_file".
        Act: set BINANCE_API_KEY="from_env" in os.environ.
        Assert: load_credentials() returns api_key="from_env".
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            plain_path = os.path.join(tmpdir, ".credentials.json")
            with open(plain_path, "w") as fh:
                json.dump({"api_key": "from_file", "api_secret": "", "telegram_bot_token": "", "telegram_chat_id": ""}, fh)

            nonexistent_enc = os.path.join(tmpdir, ".credentials.enc.json")

            env_overrides = {
                "BINANCE_API_KEY": "from_env",
                "BINANCE_API_SECRET": "",
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_CHAT_ID": "",
                "BTB_CREDENTIALS_PASSPHRASE": "",
            }

            with patch.object(credentials_module, "ENCRYPTED_PATH", nonexistent_enc):
                with patch.dict(os.environ, env_overrides, clear=False):
                    # Remove empty-string overrides so they don't shadow real file values
                    # (only BINANCE_API_KEY should override, others from file)
                    result = load_credentials(path=plain_path)

            self.assertEqual(result["api_key"], "from_env")

    # ------------------------------------------------------------------
    # Test 2: Encrypted file + missing passphrase raises RuntimeError
    # ------------------------------------------------------------------
    def test_encrypted_file_without_passphrase_raises(self):
        """Arrange: write a valid encrypted file using a temp passphrase.
        Act: unset BTB_CREDENTIALS_PASSPHRASE, call _read_encrypted().
        Assert: RuntimeError is raised.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, ".credentials.enc.json")

            # Write encrypted file using a temp passphrase
            write_env = dict(_CRED_ENV_VARS)
            write_env["BTB_CREDENTIALS_PASSPHRASE"] = _SAMPLE_PASSPHRASE
            with patch.dict(os.environ, write_env, clear=False):
                result = _write_encrypted(_SAMPLE_DATA, path=enc_path)
            self.assertTrue(result, "_write_encrypted should return True with a passphrase set")
            self.assertTrue(os.path.exists(enc_path), "Encrypted file should have been created")

            # Now try to read without passphrase
            no_passphrase_env = dict(_CRED_ENV_VARS)
            no_passphrase_env["BTB_CREDENTIALS_PASSPHRASE"] = ""
            with patch.dict(os.environ, no_passphrase_env, clear=False):
                # Remove the key entirely so _passphrase() returns None
                saved = os.environ.pop("BTB_CREDENTIALS_PASSPHRASE", None)
                try:
                    with self.assertRaises(RuntimeError):
                        _read_encrypted(path=enc_path)
                finally:
                    if saved is not None:
                        os.environ["BTB_CREDENTIALS_PASSPHRASE"] = saved

    # ------------------------------------------------------------------
    # Test 3: Encrypted file + correct passphrase loads correctly
    # ------------------------------------------------------------------
    def test_encrypted_file_with_correct_passphrase_loads(self):
        """Arrange: write encrypted file with known data, set correct passphrase.
        Act: call _read_encrypted(path=enc_path) directly.
        Assert: all four credential fields match the saved values.

        Note: patch.object on ENCRYPTED_PATH cannot redirect _read_encrypted()'s
        default argument because Python binds default args at definition time.
        Testing _read_encrypted(path=…) directly is the correct approach.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, ".credentials.enc.json")
            env = {**_CRED_ENV_VARS, "BTB_CREDENTIALS_PASSPHRASE": _SAMPLE_PASSPHRASE}

            with patch.dict(os.environ, env, clear=False):
                _write_encrypted(_SAMPLE_DATA, path=enc_path)
                result = _read_encrypted(path=enc_path)

        self.assertIsNotNone(result)
        self.assertEqual(result["api_key"], _SAMPLE_DATA["api_key"])
        self.assertEqual(result["api_secret"], _SAMPLE_DATA["api_secret"])
        self.assertEqual(result["telegram_bot_token"], _SAMPLE_DATA["telegram_bot_token"])
        self.assertEqual(result["telegram_chat_id"], _SAMPLE_DATA["telegram_chat_id"])

    # ------------------------------------------------------------------
    # Test 4: No files, no env vars → returns empty strings
    # ------------------------------------------------------------------
    def test_missing_all_files_returns_empty_strings(self):
        """Arrange: no encrypted file, no plaintext file, no env vars.
        Act: call load_credentials() with a nonexistent path.
        Assert: all four credential fields are empty strings, not None.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent_plain = os.path.join(tmpdir, "no_such_file.json")
            nonexistent_enc = os.path.join(tmpdir, ".credentials.enc.json")

            # Clear all cred env vars so they cannot contaminate the result
            clean_env = {
                "BINANCE_API_KEY": "",
                "BINANCE_API_SECRET": "",
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_CHAT_ID": "",
                "BTB_CREDENTIALS_PASSPHRASE": "",
            }

            with patch.object(credentials_module, "ENCRYPTED_PATH", nonexistent_enc):
                with patch.dict(os.environ, {}, clear=False):
                    # Pop each cred key from the live env for the duration of the test
                    saved_vals: dict[str, str | None] = {}
                    for key in clean_env:
                        saved_vals[key] = os.environ.pop(key, None)
                    try:
                        result = load_credentials(path=nonexistent_plain)
                    finally:
                        for key, val in saved_vals.items():
                            if val is not None:
                                os.environ[key] = val

            self.assertEqual(result["api_key"], "")
            self.assertEqual(result["api_secret"], "")
            self.assertEqual(result["telegram_bot_token"], "")
            self.assertEqual(result["telegram_chat_id"], "")
            # Verify all values are empty strings (not None)
            for field, value in result.items():
                self.assertIsNotNone(value, f"Field '{field}' should not be None")
                self.assertIsInstance(value, str, f"Field '{field}' should be a string")

    # ------------------------------------------------------------------
    # Test 5: Plaintext fallback loads credentials
    # ------------------------------------------------------------------
    def test_plaintext_fallback_loads_credentials(self):
        """Arrange: write a .credentials.json with known values.
        Act: call load_credentials(path=that_file).
        Assert: returned values match what was written.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            plain_path = os.path.join(tmpdir, ".credentials.json")
            plain_data = {
                "api_key": "plain_api_key",
                "api_secret": "plain_api_secret",
                "telegram_bot_token": "plain_tg_token",
                "telegram_chat_id": "plain_tg_chat",
            }
            with open(plain_path, "w") as fh:
                json.dump(plain_data, fh)

            nonexistent_enc = os.path.join(tmpdir, ".credentials.enc.json")

            # Clear cred env vars so they don't override the file values
            with patch.object(credentials_module, "ENCRYPTED_PATH", nonexistent_enc):
                with patch.dict(os.environ, {}, clear=False):
                    saved_vals: dict[str, str | None] = {}
                    cred_keys = [
                        "BINANCE_API_KEY",
                        "BINANCE_API_SECRET",
                        "TELEGRAM_BOT_TOKEN",
                        "TELEGRAM_CHAT_ID",
                        "BTB_CREDENTIALS_PASSPHRASE",
                    ]
                    for key in cred_keys:
                        saved_vals[key] = os.environ.pop(key, None)
                    try:
                        result = load_credentials(path=plain_path)
                    finally:
                        for key, val in saved_vals.items():
                            if val is not None:
                                os.environ[key] = val

            self.assertEqual(result["api_key"], plain_data["api_key"])
            self.assertEqual(result["api_secret"], plain_data["api_secret"])
            self.assertEqual(result["telegram_bot_token"], plain_data["telegram_bot_token"])
            self.assertEqual(result["telegram_chat_id"], plain_data["telegram_chat_id"])

    # ------------------------------------------------------------------
    # Test 6: save_credentials raises ValueError when passphrase missing
    # ------------------------------------------------------------------
    def test_save_credentials_without_passphrase_raises(self):
        """Arrange: BTB_CREDENTIALS_PASSPHRASE is not set.
        Act: call save_credentials({...}).
        Assert: ValueError is raised.
        """
        with patch.dict(os.environ, {}, clear=False):
            saved = os.environ.pop("BTB_CREDENTIALS_PASSPHRASE", None)
            try:
                with self.assertRaises(ValueError):
                    save_credentials(_SAMPLE_DATA)
            finally:
                if saved is not None:
                    os.environ["BTB_CREDENTIALS_PASSPHRASE"] = saved

    # ------------------------------------------------------------------
    # Additional edge-case tests
    # ------------------------------------------------------------------
    def test_write_encrypted_returns_false_without_passphrase(self):
        """_write_encrypted returns False and writes nothing when passphrase is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, ".credentials.enc.json")
            with patch.dict(os.environ, {}, clear=False):
                saved = os.environ.pop("BTB_CREDENTIALS_PASSPHRASE", None)
                try:
                    result = _write_encrypted(_SAMPLE_DATA, path=enc_path)
                finally:
                    if saved is not None:
                        os.environ["BTB_CREDENTIALS_PASSPHRASE"] = saved
            self.assertFalse(result)
            self.assertFalse(os.path.exists(enc_path))

    def test_load_credentials_returns_all_four_fields(self):
        """load_credentials() always returns all four expected fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent_plain = os.path.join(tmpdir, "no_such_file.json")
            nonexistent_enc = os.path.join(tmpdir, ".credentials.enc.json")
            with patch.object(credentials_module, "ENCRYPTED_PATH", nonexistent_enc):
                with patch.dict(os.environ, {}, clear=False):
                    saved_vals: dict[str, str | None] = {}
                    for key in ["BINANCE_API_KEY", "BINANCE_API_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "BTB_CREDENTIALS_PASSPHRASE"]:
                        saved_vals[key] = os.environ.pop(key, None)
                    try:
                        result = load_credentials(path=nonexistent_plain)
                    finally:
                        for key, val in saved_vals.items():
                            if val is not None:
                                os.environ[key] = val
            expected_fields = {"api_key", "api_secret", "telegram_bot_token", "telegram_chat_id"}
            self.assertEqual(set(result.keys()), expected_fields)

    def test_encrypted_roundtrip_all_fields(self):
        """_write_encrypted + _read_encrypted preserves all four fields correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, ".credentials.enc.json")
            env = dict(_CRED_ENV_VARS)
            env["BTB_CREDENTIALS_PASSPHRASE"] = _SAMPLE_PASSPHRASE

            with patch.dict(os.environ, env, clear=False):
                write_ok = _write_encrypted(_SAMPLE_DATA, path=enc_path)
                self.assertTrue(write_ok)
                loaded = _read_encrypted(path=enc_path)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["api_key"], _SAMPLE_DATA["api_key"])
            self.assertEqual(loaded["api_secret"], _SAMPLE_DATA["api_secret"])
            self.assertEqual(loaded["telegram_bot_token"], _SAMPLE_DATA["telegram_bot_token"])
            self.assertEqual(loaded["telegram_chat_id"], _SAMPLE_DATA["telegram_chat_id"])

    def test_multiple_env_vars_override_file_values(self):
        """All four env vars override their corresponding file values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plain_path = os.path.join(tmpdir, ".credentials.json")
            with open(plain_path, "w") as fh:
                json.dump({
                    "api_key": "file_key",
                    "api_secret": "file_secret",
                    "telegram_bot_token": "file_token",
                    "telegram_chat_id": "file_chat",
                }, fh)

            nonexistent_enc = os.path.join(tmpdir, ".credentials.enc.json")
            env_overrides = {
                "BINANCE_API_KEY": "env_key",
                "BINANCE_API_SECRET": "env_secret",
                "TELEGRAM_BOT_TOKEN": "env_token",
                "TELEGRAM_CHAT_ID": "env_chat",
                "BTB_CREDENTIALS_PASSPHRASE": "",
            }

            with patch.object(credentials_module, "ENCRYPTED_PATH", nonexistent_enc):
                with patch.dict(os.environ, env_overrides, clear=False):
                    saved_pp = os.environ.pop("BTB_CREDENTIALS_PASSPHRASE", None)
                    try:
                        result = load_credentials(path=plain_path)
                    finally:
                        if saved_pp is not None:
                            os.environ["BTB_CREDENTIALS_PASSPHRASE"] = saved_pp

            self.assertEqual(result["api_key"], "env_key")
            self.assertEqual(result["api_secret"], "env_secret")
            self.assertEqual(result["telegram_bot_token"], "env_token")
            self.assertEqual(result["telegram_chat_id"], "env_chat")


if __name__ == "__main__":
    unittest.main()
