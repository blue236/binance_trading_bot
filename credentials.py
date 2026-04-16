#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import getpass
from typing import Dict

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

CREDENTIALS_PATH = ".credentials.json"          # legacy plaintext (read-only migration)
LEGACY_PATH = "credentials.json"                # older plaintext name (read-only migration)
ENCRYPTED_PATH = ".credentials.enc.json"        # new encrypted store

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


def _resolve_plain_path(path=None):
    if path:
        return path
    if os.path.exists(LEGACY_PATH):
        return LEGACY_PATH
    return CREDENTIALS_PATH


def _empty() -> Dict[str, str]:
    return {k: "" for k, _ in _FIELDS}


def _env_overrides(data: Dict[str, str]) -> Dict[str, str]:
    out = dict(data)
    for key, env_name in _FIELDS:
        v = os.getenv(env_name)
        if v:
            out[key] = v
    return out


def _passphrase() -> str | None:
    # Required for encrypted credential storage/read.
    return (os.getenv("BTB_CREDENTIALS_PASSPHRASE") or "").strip() or None


def _fernet_from_passphrase(passphrase: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return Fernet(key)


def _read_encrypted(path=ENCRYPTED_PATH) -> Dict[str, str] | None:
    if not os.path.exists(path):
        return None
    pw = _passphrase()
    if not pw:
        # DEV-03: Fail loudly instead of silently returning empty credentials.
        # A missing passphrase with an existing encrypted file is always a
        # configuration mistake — the bot would start with no API keys.
        raise RuntimeError(
            f"Encrypted credential store '{path}' exists but "
            "BTB_CREDENTIALS_PASSPHRASE is not set. "
            "Export the passphrase as an environment variable and retry. "
            "To skip the encrypted store, delete or rename the file."
        )
    try:
        payload = json.loads(open(path, "r").read())
        salt = base64.b64decode(payload["salt"])
        token = payload["token"].encode("utf-8")
        f = _fernet_from_passphrase(pw, salt)
        raw = f.decrypt(token)
        data = json.loads(raw.decode("utf-8"))
        return {k: str(data.get(k, "")) for k, _ in _FIELDS}
    except RuntimeError:
        raise
    except Exception:
        return None


def _write_encrypted(data: Dict[str, str], path=ENCRYPTED_PATH) -> bool:
    pw = _passphrase()
    if not pw:
        return False
    salt = os.urandom(16)
    f = _fernet_from_passphrase(pw, salt)
    token = f.encrypt(json.dumps({k: data.get(k, "") for k, _ in _FIELDS}, sort_keys=True).encode("utf-8"))
    payload = {
        "v": 1,
        "kdf": "PBKDF2-HMAC-SHA256",
        "iter": 390000,
        "salt": base64.b64encode(salt).decode("utf-8"),
        "token": token.decode("utf-8"),
    }
    with open(path, "w") as fobj:
        json.dump(payload, fobj, indent=2, sort_keys=True)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return True


def load_credentials(path=None) -> Dict[str, str]:
    """Load credentials without interactive prompts.

    Priority:
    1) Encrypted store (.credentials.enc.json) + BTB_CREDENTIALS_PASSPHRASE
    2) Plaintext migration files (.credentials.json / credentials.json)
    3) Empty defaults
    Then env vars override.
    """
    import logging as _logging
    data = _read_encrypted() or None
    if data is None:
        plain_path = _resolve_plain_path(path)
        if os.path.exists(plain_path):
            # DEV-03: Warn visibly when falling back to plaintext credentials.
            # These files should be migrated to the encrypted store.
            _logging.getLogger("bot").warning(
                "Loading credentials from plaintext file '%s'. "
                "Migrate to encrypted storage: set BTB_CREDENTIALS_PASSPHRASE "
                "and run `python credentials.py` to re-encrypt.",
                plain_path,
            )
            try:
                raw = json.load(open(plain_path, "r")) or {}
                data = {k: str(raw.get(k, "")) for k, _ in _FIELDS}
            except Exception:
                data = _empty()
        else:
            data = _empty()
    return _env_overrides(data)


def save_credentials(data: Dict[str, str], path=None) -> None:
    """Save credentials securely.

    Requires BTB_CREDENTIALS_PASSPHRASE. If missing, raises ValueError.
    """
    clean = {k: str((data or {}).get(k, "")) for k, _ in _FIELDS}
    ok = _write_encrypted(clean)
    if not ok:
        raise ValueError("BTB_CREDENTIALS_PASSPHRASE is required to save credentials securely")


def load_or_prompt_credentials(path=None):
    """Backward-compatible flow used by main.py.

    Loads existing credentials (encrypted preferred). If none, prompts user and saves encrypted.
    """
    data = load_credentials(path)
    if any(data.values()):
        return data

    prompted = {
        "api_key": _prompt_value("BINANCE API key", "BINANCE_API_KEY", secret=True),
        "api_secret": _prompt_value("BINANCE API secret", "BINANCE_API_SECRET", secret=True),
        "telegram_bot_token": _prompt_value("Telegram bot token", "TELEGRAM_BOT_TOKEN", secret=True),
        "telegram_chat_id": _prompt_value("Telegram chat id", "TELEGRAM_CHAT_ID", secret=False),
    }
    save_credentials(prompted, path)
    return _env_overrides(prompted)


if __name__ == "__main__":
    creds = load_or_prompt_credentials()
    print("Credentials ready:", ", ".join([k for k, v in creds.items() if v]))
