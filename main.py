#!/usr/bin/env python3
import os, sys, time, json, math, traceback, logging, secrets
import urllib.parse
import urllib.request
import pandas as pd
import numpy as np
import yaml
from datetime import datetime, timedelta, timezone
from dateutil import tz

import ccxt
from ta.volatility import AverageTrueRange, BollingerBands
from ta.trend import EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from telegram_shared import build_summary_text, HELP_TEXT
from credentials import load_or_prompt_credentials

# Optional Telegram
try:
    from telegram import Bot
except Exception:
    Bot = None

def apply_credentials(cfg, creds):
    cfg.setdefault("credentials", {})
    cfg.setdefault("alerts", {})
    if not cfg["credentials"].get("api_key"):
        cfg["credentials"]["api_key"] = creds.get("api_key", "")
    if not cfg["credentials"].get("api_secret"):
        cfg["credentials"]["api_secret"] = creds.get("api_secret", "")
    if not cfg["alerts"].get("telegram_bot_token"):
        cfg["alerts"]["telegram_bot_token"] = creds.get("telegram_bot_token", "")
    if not cfg["alerts"].get("telegram_chat_id"):
        cfg["alerts"]["telegram_chat_id"] = creds.get("telegram_chat_id", "")
    return cfg

def load_config(path="config.yaml"):
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    creds = load_or_prompt_credentials()
    cfg = apply_credentials(cfg, creds)
    return cfg

def apply_env_overrides(cfg):
    cfg.setdefault("credentials", {})
    cfg.setdefault("alerts", {})
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    if api_key:
        cfg["credentials"]["api_key"] = api_key
    if api_secret:
        cfg["credentials"]["api_secret"] = api_secret
    if tg_token:
        cfg["alerts"]["telegram_bot_token"] = tg_token
    if tg_chat:
        cfg["alerts"]["telegram_chat_id"] = tg_chat
    return cfg

def deep_merge(base, updates):
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base

def apply_aggressive_overrides(cfg):
    gen = cfg.get("general", {})
    if not (gen.get("dry_run") and gen.get("aggressive_mode")):
        return cfg
    return deep_merge(cfg, cfg.get("aggressive", {}))

def now_tz(tz_name):
    return datetime.now(tz.gettz(tz_name))

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def setup_logger(log_dir, name="bot.log", level="INFO"):
    ensure_dir(log_dir)
    logger = logging.getLogger("bot")
    log_level = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(log_level)

    if logger.handlers:
        for h in logger.handlers:
            h.setLevel(log_level)
        return logger

    fpath = os.path.join(log_dir, name)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(fpath, encoding="utf-8")
    fh.setLevel(log_level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(log_level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger

def read_state(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"positions": {}, "cooldowns": {}, "daily": {}}

def write_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)

def log_csv(csv_dir, name, row, fieldnames=None):
    ensure_dir(csv_dir)
    fpath = os.path.join(csv_dir, f"{name}.csv")
    exists = os.path.exists(fpath)
    import csv
    with open(fpath, "a", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames or list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def audit_log(csv_dir, event, payload):
    ensure_dir(csv_dir)
    fpath = os.path.join(csv_dir, "audit.log")
    rec = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event,
        **(payload or {}),
    }
    with open(fpath, "a") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")

def connect_exchange(cfg):
    ex_id = cfg["general"]["exchange"]
    creds = cfg["credentials"]
    exchange_class = getattr(ccxt, ex_id)
    exchange = exchange_class({
        "apiKey": creds["api_key"],
        "secret": creds["api_secret"],
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,  # auto-sync with Binance server time
        },
    })

    # Extra startup stabilization: re-check server time delta several times
    for _ in range(3):
        # Pre-calculate server time difference (optional but recommended)
        try:
            exchange.load_time_difference()
            break
        except Exception as e:
            print("Warning: load_time_difference() failed:", e)
            time.sleep(1)

    exchange.load_markets()
    return exchange

def safe_fetch_balance(exchange, retries=5, backoff_sec=1.0, logger=None):
    for i in range(retries):
        try:
            return exchange.fetch_balance()
        except ccxt.InvalidNonce:
            # Handle Binance -1021 time-sync nonce errors
            try:
                exchange.load_time_difference()
            except Exception:
                pass
            time.sleep(1 + i)   # progressive backoff
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            NETWORK_HEALTH["consecutive_failures"] = int(NETWORK_HEALTH.get("consecutive_failures", 0)) + 1
            NETWORK_HEALTH["last_error"] = str(e)
            NETWORK_HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            NETWORK_HEALTH["last_label"] = "fetch_balance"
            if i < retries - 1:
                if logger:
                    logger.warning("fetch_balance transient failure (%d/%d): %s", i + 1, retries, str(e))
                time.sleep(backoff_sec * (i + 1))
                continue
            if logger:
                logger.error("fetch_balance failed after %d retries: %s", retries, str(e))
            raise
    # If all retries fail, raise the last exception
    return exchange.fetch_balance()


def _network_cfg(cfg):
    n = (cfg or {}).get("network", {}) if isinstance(cfg, dict) else {}
    retry_count = int(n.get("retry_count", 3) or 3)
    backoff_sec = float(n.get("retry_backoff_sec", 1.0) or 1.0)
    return max(1, retry_count), max(0.1, backoff_sec)


NETWORK_HEALTH = {
    "consecutive_failures": 0,
    "last_error": "",
    "last_error_at": "",
    "last_ok_at": "",
    "last_label": "",
}


def call_with_retry(fn, *, retries=3, backoff_sec=1.0, logger=None, label="network_call"):
    last_err = None
    for attempt in range(1, int(retries) + 1):
        try:
            out = fn()
            NETWORK_HEALTH["consecutive_failures"] = 0
            NETWORK_HEALTH["last_ok_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            NETWORK_HEALTH["last_label"] = label
            return out
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            last_err = e
            NETWORK_HEALTH["consecutive_failures"] = int(NETWORK_HEALTH.get("consecutive_failures", 0)) + 1
            NETWORK_HEALTH["last_error"] = str(e)
            NETWORK_HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            NETWORK_HEALTH["last_label"] = label
            if attempt < retries:
                if logger:
                    logger.debug("%s transient failure (%d/%d): %s", label, attempt, retries, str(e))
                # incremental backoff
                time.sleep(backoff_sec * attempt)
                continue
            break
    if logger:
        logger.error("%s failed after %d retries: %s", label, retries, str(last_err))
    raise last_err


def safe_fetch_tickers(exchange, symbols, cfg, logger=None):
    retries, backoff = _network_cfg(cfg)
    symbols = list(symbols or [])
    if len(symbols) == 1:
        s = symbols[0]
        return {s: safe_fetch_ticker(exchange, s, cfg, logger=logger)}
    return call_with_retry(
        lambda: exchange.fetch_tickers(symbols),
        retries=retries,
        backoff_sec=backoff,
        logger=logger,
        label=f"fetch_tickers[{len(symbols)}]",
    )


def safe_fetch_ticker(exchange, symbol, cfg, logger=None):
    retries, backoff = _network_cfg(cfg)
    try:
        return call_with_retry(
            lambda: exchange.fetch_ticker(symbol),
            retries=retries,
            backoff_sec=backoff,
            logger=logger,
            label=f"fetch_ticker[{symbol}]",
        )
    except Exception as e:
        # Fallback path: some environments intermittently fail on ticker/24hr endpoint.
        # Try latest OHLCV close so the loop can continue.
        if logger:
            logger.warning("fetch_ticker fallback to ohlcv for %s after error: %s", symbol, str(e))
        tf = ((cfg or {}).get("general", {}) or {}).get("timeframe_signal", "1h")
        rows = call_with_retry(
            lambda: exchange.fetch_ohlcv(symbol, timeframe=tf, limit=2),
            retries=retries,
            backoff_sec=backoff,
            logger=logger,
            label=f"fetch_ohlcv_fallback[{symbol},{tf}]",
        )
        if not rows:
            raise
        last_close = float(rows[-1][4])
        return {"symbol": symbol, "last": last_close}

def connect_telegram(cfg):
    alerts = cfg.get("alerts", {})
    if not alerts.get("enable_telegram"):
        return None
    token = (alerts.get("telegram_bot_token") or "").strip()
    chat_id = str((alerts.get("telegram_chat_id") or "").strip())
    if not token or not chat_id:
        return None
    return {"token": token, "chat_id": chat_id}


def _tg_post(token: str, method: str, params: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def send_telegram(tg, text):
    if tg is None:
        return
    try:
        _tg_post(tg["token"], "sendMessage", {"chat_id": tg["chat_id"], "text": (text or "")[:4000]})
    except Exception as e:
        print("Telegram send failed:", e, file=sys.stderr)


def _inbox_path():
    return os.path.join(os.getcwd(), ".telegram_inbox.jsonl")


def _approval_from_inbox(token: str, since_ts: float):
    p = _inbox_path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-500:]
        approve_cmds = {f"approve {token}", f"/approve {token}"}
        deny_cmds = {f"deny {token}", f"/deny {token}"}
        for ln in reversed(lines):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            ts = float(rec.get("ts", 0.0) or 0.0)
            if ts + 1 < since_ts:
                continue
            txt = str(rec.get("text", "")).strip().lower()
            if txt in approve_cmds:
                return True
            if txt in deny_cmds:
                return False
    except Exception:
        return None
    return None


def request_trade_approval(tg, action, symbol, qty, price, timeout_sec, offset=None):
    """Request manual approval via Telegram (handled by unified server inbox)."""
    if tg is None:
        return False, offset

    chat_id = tg["chat_id"]
    token = str(int(time.time() * 1000))[-9:]
    prompt = (
        f"🟡 APPROVAL REQUIRED\n"
        f"Action: {action}\n"
        f"Symbol: {symbol}\n"
        f"Qty: {qty}\n"
        f"Price: {price:.6f}\n\n"
        f"Reply with:\n"
        f"APPROVE {token}\n"
        f"or\n"
        f"DENY {token}\n"
        f"Timeout: {timeout_sec}s"
    )
    sent_at = time.time()
    try:
        _tg_post(tg["token"], "sendMessage", {"chat_id": chat_id, "text": prompt[:4000]})
    except Exception:
        return False, offset

    deadline = time.time() + max(5, int(timeout_sec))
    while time.time() < deadline:
        decision = _approval_from_inbox(token, sent_at)
        if decision is True:
            send_telegram(tg, f"✅ Approved: {action} {symbol}")
            return True, offset
        if decision is False:
            send_telegram(tg, f"❌ Denied: {action} {symbol}")
            return False, offset
        time.sleep(1)

    send_telegram(tg, f"⌛ Approval timeout: {action} {symbol}")
    return False, offset


def _status_text(cfg, state, equity_now, base_ccy):
    positions = state.get("positions", {}) if isinstance(state, dict) else {}
    syms = cfg.get("general", {}).get("symbols", [])
    paused = bool(state.get("bot_paused", False)) if isinstance(state, dict) else False
    runtime_mode = (state.get("runtime_mode") if isinstance(state, dict) else None) or (
        "aggressive" if cfg.get("general", {}).get("aggressive_mode") else "normal"
    )
    return (
        "📊 Current status\n"
        f"mode: {'PAUSED' if paused else 'RUNNING'} / strategy_mode: {runtime_mode}\n"
        f"dry_run: {cfg.get('general', {}).get('dry_run')}\n"
        f"equity: {float(equity_now):.2f} {base_ccy}\n"
        f"open_positions: {len(positions)}\n"
        f"symbols: {', '.join(syms)}"
    )


def _risk_text(cfg):
    r = cfg.get("risk", {})
    return (
        "🛡 Risk config\n"
        f"per_trade_risk_pct: {r.get('per_trade_risk_pct')}\n"
        f"daily_loss_stop_pct: {r.get('daily_loss_stop_pct')}\n"
        f"max_concurrent_positions: {r.get('max_concurrent_positions')}\n"
        f"cooldown_hours: {r.get('cooldown_hours')}"
    )


def _summary_text(cfg, state, equity_now, base_ccy, now_ts=None):
    return build_summary_text(
        cfg,
        state,
        equity_now=equity_now,
        base_ccy=base_ccy,
        now_ts=(now_ts or now_tz(cfg.get("logging", {}).get("tz", "UTC"))),
        running=True,
    )


def _telegram_owner_id(cfg):
    return str((cfg.get("alerts", {}) or {}).get("telegram_owner_user_id", "")).strip()


def _is_owner_message(cfg, msg):
    owner = _telegram_owner_id(cfg)
    uid = str((msg.get("from") or {}).get("id", "")).strip()
    if not owner:
        return False
    return uid == owner


def _build_confirm_text(pending):
    if not pending:
        return "No pending change."
    token = str(pending.get("token") or "")
    ttl = int(pending.get("expires_at", 0) - time.time()) if pending.get("expires_at") else 0
    ttl = max(ttl, 0)
    return (
        f"Pending change: {pending.get('cmd')} -> {pending.get('value')}\n"
        f"Reply /confirm {token} to apply (expires in {ttl}s) or /cancel to discard."
    )


def poll_telegram_commands(tg, offset, cfg, state, equity_now, base_ccy, state_path):
    if tg is None:
        return offset
    try:
        params = {"timeout": 0}
        if offset is not None:
            params["offset"] = int(offset)
        data = _tg_post(tg["token"], "getUpdates", params)
        updates = data.get("result", []) if isinstance(data, dict) else []
    except Exception:
        return offset

    chat_id_str = str(tg["chat_id"])
    for upd in updates:
        offset = int(upd.get("update_id", 0)) + 1
        msg = upd.get("message") or {}
        c = str((msg.get("chat") or {}).get("id", ""))
        if c != chat_id_str:
            continue
        txt = str(msg.get("text") or "").strip()
        cmd = txt.lower()
        parts = txt.split()
        root = (parts[0].lower() if parts else "")
        owner_ok = _is_owner_message(cfg, msg)
        pending = state.get("pending_change") or {}
        if pending and int(time.time()) > int(pending.get("expires_at", 0)):
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_EXPIRED", {"cmd": pending.get("cmd"), "token": pending.get("token")})
            state.pop("pending_change", None)
            write_state(state_path, state)

        if cmd in ("/status", "status"):
            send_telegram(tg, _status_text(cfg, state, equity_now, base_ccy))
        elif cmd in ("/start", "start"):
            if not owner_ok:
                send_telegram(tg, "Owner-only command.")
                continue
            send_telegram(tg, "✅ AI bot already running.")
        elif cmd in ("/stop", "stop"):
            if not owner_ok:
                send_telegram(tg, "Owner-only command.")
                continue
            send_telegram(tg, "🛑 Stopping AI bot by Telegram command.")
            raise SystemExit(0)
        elif cmd in ("/positions", "positions"):
            positions = state.get("positions", {}) if isinstance(state, dict) else {}
            if not positions:
                send_telegram(tg, "No open positions.")
            else:
                lines = ["📌 Open positions"]
                for sym, pos in positions.items():
                    lines.append(f"- {sym}: qty={pos.get('qty')} entry={float(pos.get('entry_price', 0.0)):.4f} sl={float(pos.get('sl', 0.0)):.4f}")
                send_telegram(tg, "\n".join(lines)[:3900])
        elif cmd in ("/risk", "risk"):
            send_telegram(tg, _risk_text(cfg))
        elif cmd in ("/summary", "summary"):
            send_telegram(tg, _summary_text(cfg, state, equity_now, base_ccy))
        elif cmd in ("/health", "health"):
            owner = _telegram_owner_id(cfg)
            health_text = (
                "🩺 Health\n"
                f"loop_alive: yes\n"
                f"owner_configured: {'yes' if owner else 'no'}\n"
                f"pending_change: {bool(state.get('pending_change'))}\n"
                f"positions: {len(state.get('positions', {}))}\n"
                f"paused: {bool(state.get('bot_paused', False))}"
            )
            send_telegram(tg, health_text)
        elif root in ("/restart", "restart"):
            if not owner_ok:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"cmd": "restart", "reason": "owner_only", "user_id": str(msg.get("from", {}).get("id", ""))})
                send_telegram(tg, "Owner-only command.")
                continue
            token = secrets.token_hex(3)
            state["pending_change"] = {
                "cmd": "restart", "value": "now", "token": token,
                "requested_at": now_tz(cfg["logging"]["tz"]).isoformat(),
                "expires_at": int(time.time()) + 120,
                "user_id": str(msg.get("from", {}).get("id", "")),
                "username": str(msg.get("from", {}).get("username", "")),
                "chat_id": c,
            }
            write_state(state_path, state)
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_CONFIRM_ISSUED", {"cmd": "restart", "token": token, "user_id": state["pending_change"]["user_id"]})
            send_telegram(tg, _build_confirm_text(state["pending_change"]))
        elif root in ("/setrisk", "setrisk"):
            if not owner_ok:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"cmd": "setrisk", "reason": "owner_only", "user_id": str(msg.get("from", {}).get("id", ""))})
                send_telegram(tg, "Owner-only command.")
                continue
            if len(parts) < 2:
                send_telegram(tg, "Usage: /setrisk <percent> (e.g. /setrisk 0.4)")
                continue
            try:
                v = float(parts[1])
                if v < 0.05 or v > 5.0:
                    raise ValueError("range")
            except Exception:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_VALIDATION_FAILED", {"cmd": "setrisk", "args": txt})
                send_telegram(tg, "Invalid risk. Allowed range: 0.05 ~ 5.0")
                continue
            token = secrets.token_hex(3)
            state["pending_change"] = {
                "cmd": "setrisk", "value": v, "token": token,
                "requested_at": now_tz(cfg["logging"]["tz"]).isoformat(),
                "expires_at": int(time.time()) + 120,
                "user_id": str(msg.get("from", {}).get("id", "")),
                "username": str(msg.get("from", {}).get("username", "")),
                "chat_id": c,
            }
            write_state(state_path, state)
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_CONFIRM_ISSUED", {"cmd": "setrisk", "value": v, "token": token, "user_id": state["pending_change"]["user_id"]})
            send_telegram(tg, _build_confirm_text(state["pending_change"]))
        elif root in ("/setmaxpos", "setmaxpos"):
            if not owner_ok:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"cmd": "setmaxpos", "reason": "owner_only", "user_id": str(msg.get("from", {}).get("id", ""))})
                send_telegram(tg, "Owner-only command.")
                continue
            if len(parts) < 2:
                send_telegram(tg, "Usage: /setmaxpos <n> (e.g. /setmaxpos 2)")
                continue
            try:
                v = int(parts[1])
                if v < 1 or v > 20:
                    raise ValueError("range")
            except Exception:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_VALIDATION_FAILED", {"cmd": "setmaxpos", "args": txt})
                send_telegram(tg, "Invalid max positions. Allowed range: 1 ~ 20")
                continue
            token = secrets.token_hex(3)
            state["pending_change"] = {
                "cmd": "setmaxpos", "value": v, "token": token,
                "requested_at": now_tz(cfg["logging"]["tz"]).isoformat(),
                "expires_at": int(time.time()) + 120,
                "user_id": str(msg.get("from", {}).get("id", "")),
                "username": str(msg.get("from", {}).get("username", "")),
                "chat_id": c,
            }
            write_state(state_path, state)
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_CONFIRM_ISSUED", {"cmd": "setmaxpos", "value": v, "token": token, "user_id": state["pending_change"]["user_id"]})
            send_telegram(tg, _build_confirm_text(state["pending_change"]))
        elif root in ("/setcooldown", "setcooldown"):
            if not owner_ok:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"cmd": "setcooldown", "reason": "owner_only", "user_id": str(msg.get("from", {}).get("id", ""))})
                send_telegram(tg, "Owner-only command.")
                continue
            if len(parts) < 2:
                send_telegram(tg, "Usage: /setcooldown <hours> (e.g. /setcooldown 8)")
                continue
            try:
                v = int(parts[1])
                if v < 0 or v > 72:
                    raise ValueError("range")
            except Exception:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_VALIDATION_FAILED", {"cmd": "setcooldown", "args": txt})
                send_telegram(tg, "Invalid cooldown. Allowed range: 0 ~ 72")
                continue
            token = secrets.token_hex(3)
            state["pending_change"] = {
                "cmd": "setcooldown", "value": v, "token": token,
                "requested_at": now_tz(cfg["logging"]["tz"]).isoformat(),
                "expires_at": int(time.time()) + 120,
                "user_id": str(msg.get("from", {}).get("id", "")),
                "username": str(msg.get("from", {}).get("username", "")),
                "chat_id": c,
            }
            write_state(state_path, state)
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_CONFIRM_ISSUED", {"cmd": "setcooldown", "value": v, "token": token, "user_id": state["pending_change"]["user_id"]})
            send_telegram(tg, _build_confirm_text(state["pending_change"]))
        elif root in ("/mode", "mode"):
            if not owner_ok:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"cmd": "mode", "reason": "owner_only", "user_id": str(msg.get("from", {}).get("id", ""))})
                send_telegram(tg, "Owner-only command.")
                continue
            if len(parts) < 2:
                send_telegram(tg, "Usage: /mode <safe|normal|aggressive>")
                continue
            mode = str(parts[1]).strip().lower()
            if mode not in ("safe", "normal", "aggressive"):
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_VALIDATION_FAILED", {"cmd": "mode", "args": txt})
                send_telegram(tg, "Invalid mode. Allowed: safe | normal | aggressive")
                continue
            if "normal_defaults" not in state:
                state["normal_defaults"] = {
                    "risk": {
                        "per_trade_risk_pct": cfg.get("risk", {}).get("per_trade_risk_pct", 0.5),
                        "max_concurrent_positions": cfg.get("risk", {}).get("max_concurrent_positions", 2),
                        "cooldown_hours": cfg.get("risk", {}).get("cooldown_hours", 8),
                    },
                    "general": {
                        "aggressive_mode": bool(cfg.get("general", {}).get("aggressive_mode", False))
                    },
                    "alerts": {
                        "enable_trade_approval": bool(cfg.get("alerts", {}).get("enable_trade_approval", True))
                    }
                }
            token = secrets.token_hex(3)
            state["pending_change"] = {
                "cmd": "mode", "value": mode, "token": token,
                "requested_at": now_tz(cfg["logging"]["tz"]).isoformat(),
                "expires_at": int(time.time()) + 120,
                "user_id": str(msg.get("from", {}).get("id", "")),
                "username": str(msg.get("from", {}).get("username", "")),
                "chat_id": c,
            }
            write_state(state_path, state)
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_CONFIRM_ISSUED", {"cmd": "mode", "value": mode, "token": token, "user_id": state["pending_change"]["user_id"]})
            send_telegram(tg, _build_confirm_text(state["pending_change"]))
        elif root in ("/confirm", "confirm"):
            pending = state.get("pending_change") or {}
            pcmd = pending.get("cmd")
            pval = pending.get("value")
            token_in = parts[1].strip().lower() if len(parts) >= 2 else ""
            if not pcmd:
                send_telegram(tg, "No pending change.")
                continue
            if not token_in:
                send_telegram(tg, "Usage: /confirm <token>")
                continue
            if token_in != str(pending.get("token", "")).lower():
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"reason": "token_mismatch", "cmd": pcmd, "user_id": str(msg.get("from", {}).get("id", ""))})
                send_telegram(tg, "Invalid confirm token.")
                continue
            if int(time.time()) > int(pending.get("expires_at", 0)):
                state.pop("pending_change", None)
                write_state(state_path, state)
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_EXPIRED", {"cmd": pcmd, "token": token_in})
                send_telegram(tg, "Pending change expired. Please submit command again.")
                continue
            req_user = str(pending.get("user_id", ""))
            cur_user = str(msg.get("from", {}).get("id", ""))
            if req_user and cur_user != req_user:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"reason": "user_mismatch", "cmd": pcmd, "requested_user": req_user, "user_id": cur_user})
                send_telegram(tg, "Only the requester can confirm this change.")
                continue
            if pcmd == "setrisk":
                old = cfg["risk"].get("per_trade_risk_pct")
                cfg["risk"]["per_trade_risk_pct"] = float(pval)
                logging.getLogger("bot").info("CMD setrisk applied: %s -> %s", old, pval)
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_APPLIED", {"cmd": "setrisk", "before": old, "after": pval})
                send_telegram(tg, f"✅ per_trade_risk_pct updated: {old} -> {pval}")
            elif pcmd == "setmaxpos":
                old = cfg["risk"].get("max_concurrent_positions")
                cfg["risk"]["max_concurrent_positions"] = int(pval)
                logging.getLogger("bot").info("CMD setmaxpos applied: %s -> %s", old, pval)
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_APPLIED", {"cmd": "setmaxpos", "before": old, "after": pval})
                send_telegram(tg, f"✅ max_concurrent_positions updated: {old} -> {pval}")
            elif pcmd == "setcooldown":
                old = cfg["risk"].get("cooldown_hours")
                cfg["risk"]["cooldown_hours"] = int(pval)
                logging.getLogger("bot").info("CMD setcooldown applied: %s -> %s", old, pval)
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_APPLIED", {"cmd": "setcooldown", "before": old, "after": pval})
                send_telegram(tg, f"✅ cooldown_hours updated: {old} -> {pval}")
            elif pcmd == "mode":
                mode = str(pval)
                old_mode = state.get("runtime_mode", "aggressive" if cfg.get("general", {}).get("aggressive_mode") else "normal")
                defaults = state.get("normal_defaults", {})
                if mode == "safe":
                    cfg["general"]["aggressive_mode"] = False
                    cfg["alerts"]["enable_trade_approval"] = True
                    cfg["risk"]["per_trade_risk_pct"] = min(float(cfg["risk"].get("per_trade_risk_pct", 0.5)), 0.4)
                    cfg["risk"]["max_concurrent_positions"] = min(int(cfg["risk"].get("max_concurrent_positions", 2)), 1)
                    cfg["risk"]["cooldown_hours"] = max(int(cfg["risk"].get("cooldown_hours", 8)), 8)
                elif mode == "normal":
                    r0 = (defaults.get("risk") or {})
                    g0 = (defaults.get("general") or {})
                    a0 = (defaults.get("alerts") or {})
                    cfg["general"]["aggressive_mode"] = bool(g0.get("aggressive_mode", False))
                    cfg["alerts"]["enable_trade_approval"] = bool(a0.get("enable_trade_approval", True))
                    cfg["risk"]["per_trade_risk_pct"] = float(r0.get("per_trade_risk_pct", 0.5))
                    cfg["risk"]["max_concurrent_positions"] = int(r0.get("max_concurrent_positions", 2))
                    cfg["risk"]["cooldown_hours"] = int(r0.get("cooldown_hours", 8))
                elif mode == "aggressive":
                    cfg["general"]["aggressive_mode"] = True
                    a = cfg.get("aggressive", {})
                    ar = a.get("risk", {}) if isinstance(a, dict) else {}
                    if ar:
                        cfg["risk"]["per_trade_risk_pct"] = float(ar.get("per_trade_risk_pct", cfg["risk"].get("per_trade_risk_pct", 0.5)))
                        cfg["risk"]["max_concurrent_positions"] = int(ar.get("max_concurrent_positions", cfg["risk"].get("max_concurrent_positions", 2)))
                        cfg["risk"]["cooldown_hours"] = int(ar.get("cooldown_hours", cfg["risk"].get("cooldown_hours", 8)))
                state["runtime_mode"] = mode
                logging.getLogger("bot").info("CMD mode applied: %s -> %s", old_mode, mode)
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_APPLIED", {"cmd": "mode", "before": old_mode, "after": mode})
                send_telegram(tg, f"✅ mode updated: {old_mode} -> {mode}")
            elif pcmd == "restart":
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_APPLIED", {"cmd": "restart", "user_id": cur_user})
                send_telegram(tg, "♻️ Restarting bot process...")
                state.pop("pending_change", None)
                write_state(state_path, state)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            state.pop("pending_change", None)
            write_state(state_path, state)
        elif cmd in ("/cancel", "cancel"):
            pending = state.get("pending_change") or {}
            req_user = str(pending.get("user_id", ""))
            cur_user = str(msg.get("from", {}).get("id", ""))
            if pending and req_user and cur_user != req_user:
                audit_log(cfg["logging"]["csv_dir"], "COMMAND_DENIED", {"reason": "cancel_user_mismatch", "cmd": pending.get("cmd"), "requested_user": req_user, "user_id": cur_user})
                send_telegram(tg, "Only the requester can cancel this pending change.")
                continue
            state.pop("pending_change", None)
            write_state(state_path, state)
            audit_log(cfg["logging"]["csv_dir"], "COMMAND_CANCELLED", {"cmd": pending.get("cmd"), "user_id": cur_user})
            send_telegram(tg, "Cancelled pending change.")
        elif cmd in ("/pause", "pause"):
            if not owner_ok:
                send_telegram(tg, "Owner-only command.")
                continue
            if not state.get("bot_paused", False):
                state["bot_paused"] = True
                write_state(state_path, state)
            send_telegram(tg, "⏸ Bot is now PAUSED. Monitoring and Telegram commands stay active.")
        elif cmd in ("/resume", "resume"):
            if not owner_ok:
                send_telegram(tg, "Owner-only command.")
                continue
            if state.get("bot_paused", False):
                state["bot_paused"] = False
                write_state(state_path, state)
            send_telegram(tg, "▶️ Bot resumed.")
        elif cmd in ("/help", "help"):
            send_telegram(tg, HELP_TEXT)

    return offset


def responsive_wait(seconds, tg, offset, cfg, state, equity_now, base_ccy, state_path):
    end_ts = time.time() + max(0, int(seconds))
    while time.time() < end_ts:
        offset = poll_telegram_commands(tg, offset, cfg, state, equity_now, base_ccy, state_path)
        time.sleep(3)
    return offset

def fetch_ohlc(exchange, symbol, timeframe, limit=500, cfg=None, logger=None):
    retries, backoff = _network_cfg(cfg or {})
    o = call_with_retry(
        lambda: exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
        retries=retries,
        backoff_sec=backoff,
        logger=logger,
        label=f"fetch_ohlcv[{symbol},{timeframe}]",
    )
    df = pd.DataFrame(o, columns=["ts","o","h","l","c","v"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df

def regime_filter(exchange, symbol, cfg):
    logger = logging.getLogger("bot")
    tf = cfg["general"]["timeframe_regime"]
    st = cfg.get("strategy", {})
    mode = str(st.get("mode", "legacy_hybrid")).lower()

    d = fetch_ohlc(exchange, symbol, tf, 400, cfg=cfg, logger=logger)

    if mode == "h_v5_b_plus_breakeven_ema100":
        ema_slow_len = int(st.get("ema_slow", 200))
        regime_fast_len = int(st.get("regime_ema_fast", 50))
        regime_rsi_len = int(st.get("regime_rsi_len", 14))
        regime_rsi_min = float(st.get("regime_rsi_min", 55))

        ema200 = EMAIndicator(d["c"], ema_slow_len).ema_indicator()
        ema50 = EMAIndicator(d["c"], regime_fast_len).ema_indicator()
        rsi_d = RSIIndicator(d["c"], regime_rsi_len).rsi()
        slope_pos = (ema200.diff().iloc[-1] > 0)
        is_trend = bool((d["c"].iloc[-1] > ema200.iloc[-1]) and slope_pos and (ema50.iloc[-1] > ema200.iloc[-1]) and (rsi_d.iloc[-1] >= regime_rsi_min))
        logger.debug(
            "Regime[V5] %s: close=%.6f ema200=%.6f ema50=%.6f slope_pos=%s rsi_d=%.2f>=%.2f -> %s",
            symbol,
            float(d["c"].iloc[-1]),
            float(ema200.iloc[-1]),
            float(ema50.iloc[-1]),
            slope_pos,
            float(rsi_d.iloc[-1]),
            regime_rsi_min,
            "trend" if is_trend else "none",
        )
        return ("trend" if is_trend else "none"), float("nan")

    trend_adx_threshold = float(st.get("trend_adx_threshold", 20.0))
    ema200 = EMAIndicator(d["c"], st.get("ema_slow", 200)).ema_indicator()
    slope_pos = (ema200.diff().iloc[-1] > 0)
    adx_val = ADXIndicator(d["h"], d["l"], d["c"], st.get("adx_len", 14)).adx().iloc[-1]
    is_trend = slope_pos and adx_val > trend_adx_threshold
    is_range = adx_val <= trend_adx_threshold
    logger.debug(
        "Regime %s: slope_pos=%s adx=%.2f threshold=%.2f -> %s",
        symbol,
        slope_pos,
        float(adx_val),
        trend_adx_threshold,
        "trend" if is_trend else ("range" if is_range else "none"),
    )
    return ("trend" if is_trend else ("range" if is_range else "none")), float(adx_val)

def h1_signals(df, cfg, regime):
    logger = logging.getLogger("bot")
    st = cfg["strategy"]
    if len(df) < max(st["donchian_len"], st["bb_len"], st["ema_slow"]) + 2:
        logger.debug("Signal skip: insufficient bars=%d regime=%s", len(df), regime)
        return None, {}
    emaF = EMAIndicator(df["c"], st["ema_fast"]).ema_indicator()
    emaS = EMAIndicator(df["c"], st["ema_slow"]).ema_indicator()
    atr  = AverageTrueRange(df["h"], df["l"], df["c"], st["atr_len"]).average_true_range()
    rsi  = RSIIndicator(df["c"], st["rsi_len"]).rsi()
    adx  = ADXIndicator(df["h"], df["l"], df["c"], st["adx_len"]).adx()
    don_hi = df["h"].rolling(st["donchian_len"]).max()
    bb = BollingerBands(df["c"], st["bb_len"], st["bb_mult"])
    lower = bb.bollinger_lband()
    mid   = bb.bollinger_mavg()

    close = float(df["c"].iloc[-1])
    atr_v = float(atr.iloc[-1])
    emaFv, emaSv = float(emaF.iloc[-1]), float(emaS.iloc[-1])
    rsiv = float(rsi.iloc[-1])
    adxv = float(adx.iloc[-1])
    don_hi_prev = float(don_hi.iloc[-2]) if not math.isnan(don_hi.iloc[-2]) else None
    lower_v = float(lower.iloc[-1]) if not math.isnan(lower.iloc[-1]) else None
    mid_v   = float(mid.iloc[-1]) if not math.isnan(mid.iloc[-1]) else None

    signal = None
    params = {}
    mode = str(st.get("mode", "legacy_hybrid")).lower()

    if mode == "h_v5_b_plus_breakeven_ema100":
        if regime != "trend":
            logger.debug("V5 skip: regime=%s", regime)
        else:
            pull_ema_len = int(st.get("pullback_ema_len", st.get("ema_fast", 50)))
            pull_band_atr = float(st.get("pullback_band_atr", 0.8))
            pb_ema = EMAIndicator(df["c"], pull_ema_len).ema_indicator().iloc[-1]
            ema20 = EMAIndicator(df["c"], int(st.get("momentum_ema_len", 20))).ema_indicator().iloc[-1]
            breakout = (don_hi_prev is not None) and (close > don_hi_prev)
            mom_ok = float(ema20) > float(pb_ema)
            pullback = False
            if atr_v > 0:
                dist = abs(close - float(pb_ema))
                pullback = (dist <= pull_band_atr * atr_v) and (40 <= rsiv <= 70) and mom_ok
            breakout = bool(breakout and mom_ok and (close >= float(pb_ema)))
            cond = (rsiv < float(st.get("rsi_overheat", 75))) and (breakout or pullback)
            logger.debug(
                "V5 check: breakout=%s pullback=%s mom_ok=%s close=%.6f pb_ema=%.6f rsi=%.2f cond=%s",
                breakout,
                pullback,
                mom_ok,
                close,
                float(pb_ema),
                rsiv,
                cond,
            )
            if cond:
                signal = "T_LONG"
                params["sl"] = close - float(st.get("atr_sl_trend_mult", 2.5)) * atr_v
                params["trail_mult"] = float(st.get("atr_trail_mult", 8.0))
                params["entry_style"] = "BREAKOUT" if breakout else "PULLBACK"
    elif mode == "mean_reversion_bb_regime":
        adx_max = float(st.get("mr_adx_max", 24))
        rsi_entry = float(st.get("mr_rsi_entry", st.get("rsi_mr_threshold", 35)))
        cond = (lower_v is not None) and (close <= lower_v) and (rsiv <= rsi_entry) and (adxv <= adx_max)
        logger.debug(
            "MR-BB check: close=%.6f lower=%.6f rsi=%.2f<=%.2f adx=%.2f<=%.2f cond=%s",
            close,
            lower_v if lower_v is not None else float("nan"),
            rsiv,
            rsi_entry,
            adxv,
            adx_max,
            cond,
        )
        if cond:
            signal = "R_LONG"
            params["sl"] = close - float(st.get("mr_sl_atr_mult", st["atr_sl_mr_mult"])) * atr_v
            params["tp_mid"] = mid_v
    elif regime == "trend":
        cond = (don_hi_prev is not None) and (close > don_hi_prev) and (emaFv > emaSv) and (rsiv < st["rsi_overheat"])
        logger.debug(
            "Trend check: close=%.6f don_hi_prev=%s emaF=%.6f emaS=%.6f rsi=%.2f overheat=%s cond=%s",
            close,
            f"{don_hi_prev:.6f}" if don_hi_prev is not None else "None",
            emaFv,
            emaSv,
            rsiv,
            st["rsi_overheat"],
            cond,
        )
        if cond:
            signal = "T_LONG"
            params["sl"] = close - st["atr_sl_trend_mult"] * atr_v
            params["trail_mult"] = st["atr_trail_mult"]
    elif regime == "range":
        cond = (lower_v is not None) and (close < lower_v) and (rsiv <= st["rsi_mr_threshold"])
        logger.debug(
            "Range check: close=%.6f lower=%.6f rsi=%.2f threshold=%s cond=%s",
            close,
            lower_v if lower_v is not None else float("nan"),
            rsiv,
            st["rsi_mr_threshold"],
            cond,
        )
        if cond:
            signal = "R_LONG"
            params["sl"] = close - st["atr_sl_mr_mult"] * atr_v
            params["tp_mid"] = mid_v

    params["atr"] = atr_v
    params["close"] = close
    if signal:
        logger.debug(
            "Signal=%s close=%.6f atr=%.6f sl=%.6f regime=%s mode=%s",
            signal,
            close,
            atr_v,
            params.get("sl", 0.0),
            regime,
            mode,
        )
    return signal, params

def fetch_equity_usdt(exchange, base_ccy="USDT", balances=None, tickers=None):
    balances = balances or safe_fetch_balance(exchange)["total"]
    equity = float(balances.get(base_ccy, 0.0) or 0.0)

    # Build only relevant quote pairs to avoid mixed market-type errors
    # (e.g., binance spot + swap symbols in one fetch_tickers call).
    symbols = []
    for asset, amt in (balances or {}).items():
        if asset in [base_ccy, None] or not amt:
            continue
        sym = f"{asset}/{base_ccy}"
        market = (exchange.markets or {}).get(sym) or {}
        if not market:
            continue
        # Keep equity conversion on spot pairs only.
        if market.get("spot") is False:
            continue
        symbols.append(sym)

    if tickers is None:
        tickers = {}
        if symbols:
            try:
                tickers = safe_fetch_tickers(
                    exchange,
                    symbols,
                    {"network": {"retry_count": 2, "retry_backoff_sec": 0.5}},
                )
            except Exception as e:
                logging.getLogger("bot").warning(
                    "Equity conversion ticker fetch failed for %s quote=%s: %s",
                    len(symbols),
                    base_ccy,
                    e,
                )
                tickers = {}

    for asset, amt in balances.items():
        if asset in [base_ccy, None] or not amt:
            continue
        sym = f"{asset}/{base_ccy}"
        if sym in (exchange.markets or {}) and sym in (tickers or {}) and "last" in tickers[sym]:
            equity += float(amt) * float(tickers[sym]["last"])
    return float(equity)

def position_size(equity_usdt, price, atr, atr_mult, risk_pct):
    risk_usdt = equity_usdt * (risk_pct / 100.0)
    stop_dist = atr_mult * atr
    if stop_dist <= 0:
        return 0.0
    qty = max(risk_usdt / stop_dist, 0.0)
    logging.getLogger("bot").debug(
        "Position size: equity=%.2f price=%.6f atr=%.6f mult=%.2f risk_pct=%.2f qty=%.6f",
        equity_usdt,
        price,
        atr,
        atr_mult,
        risk_pct,
        qty,
    )
    return float(round(qty, 6))

def notional_ok(qty, price, min_notional):
    return (qty * price) >= min_notional

def symbol_limits(exchange, symbol):
    market = exchange.markets.get(symbol) or {}
    limits = market.get("limits") or {}
    return {
        "min_amount": (limits.get("amount") or {}).get("min"),
        "min_cost": (limits.get("cost") or {}).get("min"),
    }

def clamp_qty(exchange, symbol, qty):
    try:
        return float(exchange.amount_to_precision(symbol, qty))
    except Exception:
        return float(qty)

def order_constraints_ok(exchange, symbol, qty, price, min_notional):
    lim = symbol_limits(exchange, symbol)
    if lim["min_amount"] is not None and qty < lim["min_amount"]:
        return False
    if lim["min_cost"] is not None and (qty * price) < lim["min_cost"]:
        return False
    return notional_ok(qty, price, min_notional)

def get_last_price(tickers, symbol):
    t = tickers.get(symbol) or {}
    return t.get("last")

TRADE_FIELDS = [
    "ts", "event", "symbol", "side", "price", "qty", "sl", "signal", "regime",
    "equity", "adx_d", "reason", "note",
]
EQUITY_FIELDS = ["ts", "equity"]

def is_cooldown(state, symbol, cfg, now):
    cd = state["cooldowns"].get(symbol)
    if not cd:
        return False
    last = datetime.fromisoformat(cd)
    return (now - last) < timedelta(hours=cfg["risk"]["cooldown_hours"])

def set_cooldown(state, symbol, now):
    state["cooldowns"][symbol] = now.isoformat()

def daily_key(now):
    return now.strftime("%Y-%m-%d")

def sync_external_controls(state_path, state, cfg):
    """Merge externally-updated control flags from state file.

    This allows Telegram commands handled by web server to affect running bot.
    """
    try:
        disk = read_state(state_path)
    except Exception:
        return state

    if "bot_paused" in disk:
        state["bot_paused"] = bool(disk.get("bot_paused", False))

    ro = disk.get("runtime_overrides") or {}
    if isinstance(ro, dict):
        if "per_trade_risk_pct" in ro:
            try:
                cfg["risk"]["per_trade_risk_pct"] = float(ro["per_trade_risk_pct"])
            except Exception:
                pass
        if "max_concurrent_positions" in ro:
            try:
                cfg["risk"]["max_concurrent_positions"] = int(ro["max_concurrent_positions"])
            except Exception:
                pass
    return state


def _update_network_health_state(state):
    state.setdefault("runtime_health", {})
    state["runtime_health"]["network"] = {
        "consecutive_failures": int(NETWORK_HEALTH.get("consecutive_failures", 0)),
        "last_error": NETWORK_HEALTH.get("last_error", ""),
        "last_error_at": NETWORK_HEALTH.get("last_error_at", ""),
        "last_ok_at": NETWORK_HEALTH.get("last_ok_at", ""),
        "last_label": NETWORK_HEALTH.get("last_label", ""),
    }


def evaluate_pretrade_risk_gate(cfg, state, symbol, now, equity_now, equity_start):
    positions = state.get("positions", {}) if isinstance(state, dict) else {}
    max_pos = int(cfg.get("risk", {}).get("max_concurrent_positions", 0) or 0)
    if bool(state.get("bot_paused", False)):
        return False, "bot_paused"
    if max_pos > 0 and len(positions) >= max_pos:
        return False, "max_concurrent_positions"
    if daily_pnl_guard(cfg, equity_now, equity_start):
        return False, "daily_loss_stop"
    if symbol in positions:
        return False, "already_in_position"
    if is_cooldown(state, symbol, cfg, now):
        return False, "cooldown_active"
    return True, "ok"


def _record_risk_gate_reject(cfg, csv_dir, loop_ts, symbol, reason, logger=None):
    payload = {"scope": "pretrade", "symbol": symbol, "reason": reason}
    audit_log(cfg["logging"]["csv_dir"], "RISK_GATE_REJECT", payload)
    log_csv(csv_dir, "trades", {
        "ts": loop_ts.isoformat(),
        "event": "RISK_GATE_REJECT",
        "symbol": symbol,
        "side": "BUY",
        "reason": reason,
    }, fieldnames=TRADE_FIELDS)
    if logger:
        logger.info("Risk gate reject: symbol=%s reason=%s", symbol, reason)


def daily_pnl_guard(cfg, equity_now, equity_start):
    if equity_start <= 0:
        return False
    dd = (equity_now - equity_start) / equity_start * 100.0
    return dd <= -abs(cfg["risk"]["daily_loss_stop_pct"])

def place_order(exchange, symbol, side, qty, price=None, dry_run=True):
    if dry_run:
        return {"id": f"dry_{int(time.time()*1000)}", "symbol": symbol, "side": side, "qty": qty, "price": price}
    try:
        if side == "buy":
            order = exchange.create_order(symbol, type="market", side="buy", amount=qty)
        else:
            order = exchange.create_order(symbol, type="market", side="sell", amount=qty)
        return order
    except Exception as e:
        raise RuntimeError(f"Order failed: {e}")

def finalize_exit(cfg, state, state_path, csv_dir, tg, sym, reason, price, qty):
    row = {
        "ts": now_tz(cfg["logging"]["tz"]).isoformat(),
        "event": "EXIT",
        "symbol": sym,
        "side": "SELL",
        "reason": reason,
        "price": price,
        "qty": qty
    }
    log_csv(csv_dir, "trades", row, fieldnames=TRADE_FIELDS)
    if sym in state["positions"]:
        del state["positions"][sym]
    write_state(state_path, state)
    if tg: 
        send_telegram(tg, f"🔴 EXIT {sym} reason={reason} qty={qty} @ {price:.4f}")

def main():
    cfg = apply_env_overrides(load_config())
    cfg = apply_aggressive_overrides(cfg)
    tzname = cfg["logging"]["tz"]
    csv_dir = cfg["logging"]["csv_dir"]
    state_path = cfg["logging"]["state_file"]
    os.makedirs(csv_dir, exist_ok=True)

    logger = setup_logger(cfg["logging"]["csv_dir"], level=cfg.get("logging", {}).get("level", "INFO"))
    exchange = connect_exchange(cfg)
    tg = connect_telegram(cfg)
    tg_offset = None

    approval_enabled = bool(cfg.get("alerts", {}).get("enable_trade_approval", False))
    approval_timeout_sec = int(cfg.get("alerts", {}).get("approval_timeout_sec", 120) or 120)

    state = read_state(state_path)
    if "bot_paused" not in state:
        state["bot_paused"] = False
    logger.debug(
        "Config: dry_run=%s symbols=%s timeframe_signal=%s timeframe_regime=%s base_currency=%s",
        cfg["general"]["dry_run"],
        cfg["general"]["symbols"],
        cfg["general"]["timeframe_signal"],
        cfg["general"]["timeframe_regime"],
        cfg["general"]["base_currency"],
    )
    logger.debug("State loaded: positions=%d cooldowns=%d", len(state["positions"]), len(state["cooldowns"]))

    session_date = daily_key(now_tz(tzname))
    base_ccy = cfg["general"]["base_currency"]
    equity_start = fetch_equity_usdt(exchange, base_ccy)
    state["session"] = {"date": session_date, "equity_start": float(equity_start)}
    logger.info("Bot started. dry_run=%s", cfg["general"]["dry_run"])
    if tg:
        logger.info("Telegram alerts enabled.")
        send_telegram(tg, f"🚀 Bot started. Equity start: {equity_start:.2f} {cfg['general']['base_currency']} (dry_run={cfg['general']['dry_run']})")
    else:
        logger.info("Telegram alerts not enabled or failed to connect.")

    if approval_enabled:
        logger.info("Trade approval via Telegram is ENABLED (timeout=%ss)", approval_timeout_sec)

    while True:
        loop_ts = now_tz(tzname)
        try:
            logger.debug("Loop start: %s", loop_ts.isoformat())
            state = sync_external_controls(state_path, state, cfg)
            # daily rollover
            dk = daily_key(loop_ts)
            # print dk, session_date
            logger.debug("dk=%s session_date=%s", dk, session_date)
            if dk != session_date:
                session_date = dk
                equity_start = fetch_equity_usdt(exchange, cfg["general"]["base_currency"])
                state["session"] = {"date": session_date, "equity_start": float(equity_start)}
                logger.info("New session %s. Equity start %.2f %s", session_date, equity_start, base_ccy)
                if tg:
                    send_telegram(tg, f"📅 New session {session_date}. Equity start: {equity_start:.2f}")

            symbols = cfg["general"]["symbols"]
            pos_syms = list(state["positions"].keys())
            ticker_syms = sorted(set(symbols + pos_syms))
            tickers = safe_fetch_tickers(exchange, ticker_syms, cfg, logger=logger) if ticker_syms else {}
            n_retries, n_backoff = _network_cfg(cfg)
            balances = safe_fetch_balance(exchange, retries=n_retries, backoff_sec=n_backoff, logger=logger)
            balances_total = balances.get("total", {})
            balances_free = balances.get("free", {})
            equity_now = fetch_equity_usdt(exchange, base_ccy, balances_total, tickers)
            #logger.debug("Tickers: %s. Equity now: %.2f %s", tickers, equity_now, base_ccy)
            logger.debug(
                "Balances: free_%s=%.6f total_assets=%d tickers=%d",
                base_ccy,
                float(balances_free.get(base_ccy, 0.0)),
                len(balances_total),
                len(tickers),
            )

            # daily PnL guard
            if daily_pnl_guard(cfg, equity_now, equity_start):
                logger.warning("Daily loss stop reached. Equity %.2f vs start %.2f", equity_now, equity_start)
                if tg:
                    send_telegram(tg, f"⛔ Daily loss stop reached. Equity {equity_now:.2f} vs start {equity_start:.2f}. Cooling 60m.")
                time.sleep(60 * 60)
                continue

            if state.get("bot_paused", False):
                logger.info("Bot paused by Telegram command; skipping trade decisions this cycle.")
                sleep_sec = int(cfg["strategy"].get("loop_sleep_seconds", 60) or 60)
                time.sleep(sleep_sec)
                continue

            # entry checks (mandatory pre-trade risk gate)
            allow_entries = len(state["positions"]) < cfg["risk"]["max_concurrent_positions"]
            free_base = balances_free.get(base_ccy, 0.0)
            logger.debug("Entry check: allow_entries=%s free_base=%.6f", allow_entries, float(free_base))
            for symbol in symbols:
                gate_ok, gate_reason = evaluate_pretrade_risk_gate(cfg, state, symbol, loop_ts, equity_now, equity_start)
                if not gate_ok:
                    if gate_reason in ("max_concurrent_positions", "daily_loss_stop", "bot_paused"):
                        _record_risk_gate_reject(cfg, csv_dir, loop_ts, symbol, gate_reason, logger=logger)
                        break
                    _record_risk_gate_reject(cfg, csv_dir, loop_ts, symbol, gate_reason, logger=logger)
                    continue
                regime, adx_val = regime_filter(exchange, symbol, cfg)
                if regime == "none":
                    logger.debug("Skip %s: no regime (adx=%.2f)", symbol, adx_val)
                    continue
                df = fetch_ohlc(exchange, symbol, cfg["general"]["timeframe_signal"], 300, cfg=cfg, logger=logger)
                signal, params = h1_signals(df, cfg, regime)
                if not signal:
                    logger.debug("Skip %s: no signal (regime=%s)", symbol, regime)
                    continue

                price = params["close"]
                atr = params["atr"]
                if signal == "T_LONG":
                    sl = params["sl"]; atr_mult = cfg["strategy"]["atr_sl_trend_mult"]
                else:
                    sl = params["sl"]; atr_mult = cfg["strategy"]["atr_sl_mr_mult"]

                risk_qty = position_size(equity_now, price, atr, atr_mult, cfg["risk"]["per_trade_risk_pct"])
                max_affordable = free_base / price if price > 0 else 0.0
                qty = clamp_qty(exchange, symbol, min(risk_qty, max_affordable))
                logger.debug(
                    "Sizing %s: price=%.6f atr=%.6f risk_qty=%.6f max_affordable=%.6f qty=%.6f",
                    symbol,
                    price,
                    atr,
                    risk_qty,
                    max_affordable,
                    qty,
                )
                if qty > 0 and order_constraints_ok(exchange, symbol, qty, price, cfg["general"]["min_notional_usdc"]):
                    if approval_enabled:
                        approved, tg_offset = request_trade_approval(
                            tg,
                            action="BUY",
                            symbol=symbol,
                            qty=qty,
                            price=price,
                            timeout_sec=approval_timeout_sec,
                            offset=tg_offset,
                        )
                        if not approved:
                            logger.info("Entry skipped (approval not granted): %s qty=%s", symbol, qty)
                            log_csv(csv_dir, "trades", {
                                "ts": loop_ts.isoformat(), "event": "APPROVAL_DENIED", "symbol": symbol, "side": "BUY",
                                "price": price, "qty": qty, "signal": signal, "regime": regime, "reason": "telegram_approval"
                            }, fieldnames=TRADE_FIELDS)
                            set_cooldown(state, symbol, loop_ts)
                            continue
                    place_order(exchange, symbol, "buy", qty, price=price, dry_run=cfg["general"]["dry_run"])
                    free_base = max(0.0, free_base - (qty * price))
                    set_cooldown(state, symbol, loop_ts)
                    pos = {"symbol":symbol, "entry_time": loop_ts.isoformat(), "entry_price": price, "qty": qty, "sl": sl, "signal": signal, "regime": regime}
                    if signal == "R_LONG" and "tp_mid" in params and params["tp_mid"]:
                        pos["tp_mid"] = params["tp_mid"]
                    if signal == "T_LONG" and "trail_mult" in params:
                        pos["trail_mult"] = params["trail_mult"]; pos["trail_ref"] = price
                    if signal == "T_LONG" and str(cfg.get("strategy", {}).get("mode", "")).lower() == "h_v5_b_plus_breakeven_ema100":
                        pos["init_r"] = max(float(price - sl), 1e-9)
                        pos["breakeven_r"] = float(cfg.get("strategy", {}).get("breakeven_r", 1.0))
                    state["positions"][symbol] = pos
                    write_state(state_path, state)
                    allow_entries = len(state["positions"]) < cfg["risk"]["max_concurrent_positions"]
                    log_csv(csv_dir, "trades", {
                        "ts": loop_ts.isoformat(), "event":"ENTER", "symbol":symbol, "side":"LONG",
                        "price": price, "qty": qty, "sl": sl, "signal": signal, "regime": regime, "equity": equity_now, "adx_d": adx_val
                    }, fieldnames=TRADE_FIELDS)
                    logger.debug("ENTER %s %s qty=%s price=%.4f sl=%.4f regime=%s", symbol, signal, qty, price, sl, regime)
                    if tg:
                        send_telegram(tg, f"🟢 ENTER {symbol} {signal} qty={qty} @ {price:.4f} SL={sl:.4f} regime={regime}")
                else:
                    logger.debug("Skip %s: qty=%.6f or constraints not met", symbol, qty)

            # exits
            positions = dict(state["positions"])
            for sym, pos in positions.items():
                last = get_last_price(tickers, sym)
                if last is None:
                    last = safe_fetch_ticker(exchange, sym, cfg, logger=logger)["last"]
                qty = pos["qty"]
                sl = pos["sl"]
                changed = False
                logger.debug("Exit check %s: last=%.6f sl=%.6f signal=%s", sym, float(last), float(sl), pos["signal"])

                # trend trailing
                if pos["signal"] == "T_LONG" and "trail_mult" in pos:
                    df = fetch_ohlc(exchange, sym, cfg["general"]["timeframe_signal"], 200, cfg=cfg, logger=logger)
                    atr = AverageTrueRange(df["h"], df["l"], df["c"], cfg["strategy"]["atr_len"]).average_true_range().iloc[-1]
                    hi_since = df["c"].iloc[-200:].max()
                    trail = hi_since - pos["trail_mult"] * atr
                    if trail > sl:
                        sl = trail; pos["sl"] = float(sl); changed = True

                # H_V5 breakeven + structural daily EMA exit (NO LOOKAHEAD)
                strategy_mode = str(cfg.get("strategy", {}).get("mode", "")).lower()
                if strategy_mode == "h_v5_b_plus_breakeven_ema100" and pos.get("signal") == "T_LONG":
                    init_r = float(pos.get("init_r", max(float(pos.get("entry_price", last)) - float(pos.get("sl", sl)), 1e-9)))
                    be_r = float(pos.get("breakeven_r", cfg.get("strategy", {}).get("breakeven_r", 1.0)))
                    entry_px = float(pos.get("entry_price", last))
                    if float(last) >= (entry_px + be_r * init_r):
                        be_sl = max(float(pos.get("sl", sl)), entry_px)
                        if be_sl > sl:
                            sl = be_sl
                            pos["sl"] = float(sl)
                            changed = True

                    if bool(cfg.get("strategy", {}).get("use_structural_exit", True)):
                        structural_tf = str(cfg.get("strategy", {}).get("structural_exit_timeframe", "1d") or "1d")
                        d_df = fetch_ohlc(exchange, sym, structural_tf, 220, cfg=cfg, logger=logger)
                        d_ema_len = int(cfg.get("strategy", {}).get("structural_exit_daily_ema_len", 100))
                        need = int(cfg.get("strategy", {}).get("structural_exit_confirm_days", 2))

                        # Avoid lookahead: drop current (possibly still-forming) candle
                        # and compare previous close vs previous EMA.
                        d_df_closed = d_df.iloc[:-1].copy()
                        if len(d_df_closed) >= (d_ema_len + need + 5):
                            d_ema = EMAIndicator(d_df_closed["c"], d_ema_len).ema_indicator()
                            prev_close = d_df_closed["c"].shift(1)
                            prev_ema = d_ema.shift(1)
                            below = (prev_close < prev_ema).tail(max(need, 1))
                        else:
                            below = pd.Series([False] * max(need, 1))

                        if len(below) >= need and bool(below.all()):
                            if approval_enabled:
                                approved, tg_offset = request_trade_approval(
                                    tg, action="SELL", symbol=sym, qty=qty, price=last,
                                    timeout_sec=approval_timeout_sec, offset=tg_offset,
                                )
                                if not approved:
                                    logger.info("Structural exit skipped (approval not granted): %s qty=%s", sym, qty)
                                else:
                                    place_order(exchange, sym, "sell", qty, price=last, dry_run=cfg["general"]["dry_run"])
                                    logger.info("EXIT %s reason=STRUCTURAL_EXIT qty=%s price=%.4f", sym, qty, last)
                                    finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "STRUCTURAL_EXIT", last, qty)
                                    continue
                            else:
                                place_order(exchange, sym, "sell", qty, price=last, dry_run=cfg["general"]["dry_run"])
                                logger.info("EXIT %s reason=STRUCTURAL_EXIT qty=%s price=%.4f", sym, qty, last)
                                finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "STRUCTURAL_EXIT", last, qty)
                                continue

                # MR time stop or mid-band partial
                if pos["signal"] == "R_LONG":
                    et = datetime.fromisoformat(pos["entry_time"])
                    if loop_ts - et >= timedelta(hours=cfg["strategy"]["mean_reversion_time_stop_hours"]):
                        if approval_enabled:
                            approved, tg_offset = request_trade_approval(
                                tg, action="SELL", symbol=sym, qty=qty, price=last,
                                timeout_sec=approval_timeout_sec, offset=tg_offset,
                            )
                            if not approved:
                                logger.info("Exit skipped (approval not granted): %s qty=%s", sym, qty)
                                continue
                        place_order(exchange, sym, "sell", qty, price=last, dry_run=cfg["general"]["dry_run"])
                        logger.info("EXIT %s reason=TIME_STOP qty=%s price=%.4f", sym, qty, last)
                        finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "TIME_STOP", last, qty)
                        continue
                    if "tp_mid" in pos and last >= pos["tp_mid"]:
                        sell_qty = clamp_qty(exchange, sym, qty * 0.5)
                        if order_constraints_ok(exchange, sym, sell_qty, last, cfg["general"]["min_notional_usdc"]):
                            if approval_enabled:
                                approved, tg_offset = request_trade_approval(
                                    tg, action="SELL", symbol=sym, qty=sell_qty, price=last,
                                    timeout_sec=approval_timeout_sec, offset=tg_offset,
                                )
                                if not approved:
                                    logger.info("Partial TP skipped (approval not granted): %s qty=%s", sym, sell_qty)
                                    continue
                            place_order(exchange, sym, "sell", sell_qty, price=last, dry_run=cfg["general"]["dry_run"])
                            pos["qty"] = float(qty - sell_qty); del pos["tp_mid"]; changed = True
                            logger.info("PARTIAL_TP %s qty=%s price=%.4f note=mid-band", sym, sell_qty, last)
                            log_csv(csv_dir, "trades", {"ts": loop_ts.isoformat(), "event": "PARTIAL_TP", "symbol": sym, "side": "SELL", "price": last, "qty": sell_qty, "note": "mid-band"}, fieldnames=TRADE_FIELDS)

                # stop-loss
                if last <= sl:
                    if approval_enabled:
                        approved, tg_offset = request_trade_approval(
                            tg, action="SELL", symbol=sym, qty=pos["qty"], price=last,
                            timeout_sec=approval_timeout_sec, offset=tg_offset,
                        )
                        if not approved:
                            logger.info("Stop exit skipped (approval not granted): %s qty=%s", sym, pos["qty"])
                            continue
                    place_order(exchange, sym, "sell", pos["qty"], price=last, dry_run=cfg["general"]["dry_run"])
                    logger.info("EXIT %s reason=STOP qty=%s price=%.4f", sym, pos["qty"], last)
                    finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "STOP", last, pos["qty"])
                    continue

                if changed:
                    state["positions"][sym] = pos
                    write_state(state_path, state)

            # equity snapshot
            equity_now = fetch_equity_usdt(exchange, base_ccy, balances_total, tickers)
            _update_network_health_state(state)
            state.setdefault("runtime_health", {})["last_loop_at"] = loop_ts.isoformat()
            write_state(state_path, state)
            log_csv(csv_dir, "equity", {"ts": loop_ts.isoformat(), "equity": equity_now}, fieldnames=EQUITY_FIELDS)
            sleep_sec = int(cfg["strategy"].get("loop_sleep_seconds", 60) or 60)
            logger.info("Equity snapshot: %.4f and sleep %d seconds", equity_now, sleep_sec)
            time.sleep(sleep_sec)
        except Exception as e:
            logger.exception("Loop error: %s", e)
            time.sleep(10)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Ensure startup failures are persisted into bot.log so Web UI log stream
        # shows the real reason instead of appearing to stop silently.
        logging.getLogger("bot").exception("Fatal startup error")
        raise
