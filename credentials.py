#!/usr/bin/env python3
import json
import os
import getpass

CREDENTIALS_PATH = ".credentials.json"
LEGACY_PATH = "credentials.json"

_FIELDS = [
    ("api_key", "BINANCE_API_KEY"),
    ("api_secret", "BINANCE_API_SECRET"),
    ("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
    ("telegram_chat_id", "TELEGRAM_CHAT_ID"),
]


def _prompt_value(label, env_name, secret=False):
    env_val = os.getenv(env_name)
    if env_val:
        return env_val
    if secret:
        return getpass.getpass(f"{label} ({env_name}): ").strip()
    return input(f"{label} ({env_name}): ").strip()


def _resolve_path(path=None):
    if path:
        return path
    if os.path.exists(LEGACY_PATH):
        return LEGACY_PATH
    return CREDENTIALS_PATH

def load_or_prompt_credentials(path=None):
    """
    If credentials file exists, read it. Otherwise prompt and save.
    Returns a dict with keys: api_key, api_secret, telegram_bot_token, telegram_chat_id.
    """
    path = _resolve_path(path)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f) or {}
        return {k: data.get(k, "") for k, _ in _FIELDS}

    data = {
        "api_key": _prompt_value("BINANCE API key", "BINANCE_API_KEY", secret=True),
        "api_secret": _prompt_value("BINANCE API secret", "BINANCE_API_SECRET", secret=True),
        "telegram_bot_token": _prompt_value("Telegram bot token", "TELEGRAM_BOT_TOKEN", secret=True),
        "telegram_chat_id": _prompt_value("Telegram chat id", "TELEGRAM_CHAT_ID", secret=False),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return data


if __name__ == "__main__":
    creds = load_or_prompt_credentials()
    print("Credentials ready:", ", ".join([k for k, v in creds.items() if v]))
