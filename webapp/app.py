from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import json
import urllib.parse
import urllib.request
import time
import base64
import threading
import datetime as dt
import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Body, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import UIConfig, RefreshRequest, BacktestRequest
from .config_manager import ConfigManager
from .storage import Storage
from .chart_service import ChartService
from .backtest_service import BacktestService
from credentials import load_credentials, save_credentials
from telegram_shared import build_summary_text, HELP_TEXT

# Reuse legacy backtester web helpers to stay compatible with existing CLI options.
from web_backtester_ui import DEFAULTS as LEGACY_BT_DEFAULTS, build_command as legacy_build_command, validate_values as legacy_validate_values, list_plot_files

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
(BASE / "static").mkdir(parents=True, exist_ok=True)

AI_CONFIG_PATH = ROOT / "config.yaml"
AI_PID_FILE = ROOT / ".web_ai_bot.pid"
MASK_TOKEN = "__MASKED__"

config_mgr = ConfigManager(os.environ.get("BTB_WEB_CONFIG", "web_config.yaml"))
storage = Storage(os.environ.get("BTB_WEB_DB", "webapp_state.sqlite"))
chart_service = ChartService(storage)
backtest_service = BacktestService(storage)

app = FastAPI(title="Binance Trading Bot Web UI", version="2.1")
app.mount("/static", StaticFiles(directory=str(BASE / "static"), check_dir=False), name="static")
app.mount("/plots", StaticFiles(directory=str(ROOT / "plots"), check_dir=False), name="plots")
templates = Jinja2Templates(directory=str(BASE / "templates"))

scheduler: BackgroundScheduler | None = None
_AI_RELOAD_LOCK = threading.Lock()
_AI_CONTROL_LOCK = threading.Lock()


def _auth_enabled() -> bool:
    return os.environ.get("BTB_WEB_AUTH_ENABLED", "1").strip() not in ("0", "false", "False")


def _auth_user() -> str:
    return os.environ.get("BTB_WEB_USERNAME", "admin")


def _auth_pass() -> str:
    return os.environ.get("BTB_WEB_PASSWORD", "")


def _session_secret() -> str:
    return os.environ.get("BTB_WEB_SESSION_SECRET", "change-me")


def _session_cookie_name() -> str:
    return "btb_session"


def _make_session_token(username: str) -> str:
    import hmac, hashlib
    msg = f"{username}:ok".encode("utf-8")
    sig = hmac.new(_session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{username}:{sig}"


def _is_valid_session(token: str | None) -> bool:
    if not token or ":" not in token:
        return False
    username, sig = token.split(":", 1)
    return token == _make_session_token(username) and username == _auth_user()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _auth_enabled():
            return await call_next(request)

        path = request.url.path or "/"
        public_prefixes = ("/static", "/plots", "/login")
        if path.startswith(public_prefixes):
            return await call_next(request)

        token = request.cookies.get(_session_cookie_name())
        if _is_valid_session(token):
            return await call_next(request)

        if path.startswith("/api"):
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
        return RedirectResponse(url="/login", status_code=302)


app.add_middleware(AuthMiddleware)


def parse_cron(expr: str):
    m, h, dom, mon, dow = expr.split()
    return dict(minute=m, hour=h, day=dom, month=mon, day_of_week=dow)


def _is_ai_running() -> bool:
    if not AI_PID_FILE.exists():
        return False
    try:
        pid = int(AI_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _start_ai_bot() -> None:
    if _is_ai_running():
        return
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    AI_PID_FILE.write_text(str(proc.pid))


def _stop_ai_bot() -> None:
    if not AI_PID_FILE.exists():
        return
    try:
        pid = int(AI_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    finally:
        try:
            AI_PID_FILE.unlink()
        except Exception:
            pass


def _wait_ai_stopped(timeout_sec: float = 6.0) -> bool:
    end = time.time() + max(0.5, timeout_sec)
    while time.time() < end:
        if not _is_ai_running():
            return True
        time.sleep(0.2)
    return not _is_ai_running()


def _set_ai_running(desired_running: bool, source: str) -> dict:
    """Set AI bot running state in an idempotent and race-safe way."""
    with _AI_CONTROL_LOCK:
        before = _is_ai_running()
        if before == desired_running:
            return {
                "ok": True,
                "running": before,
                "changed": False,
                "noop": True,
                "message": "already_running" if before else "already_stopped",
            }

        if desired_running:
            _start_ai_bot()
            after = _is_ai_running()
            changed = bool(after)
            if changed:
                _notify_telegram(f"🟢 AI bot started from {source}")
            return {
                "ok": changed,
                "running": after,
                "changed": changed,
                "noop": False,
                "message": "started" if changed else "start_failed",
            }

        _stop_ai_bot()
        after = _is_ai_running()
        changed = not after
        if changed:
            _notify_telegram(f"🛑 AI bot stopped from {source}")
        return {
            "ok": changed,
            "running": after,
            "changed": changed,
            "noop": False,
            "message": "stopped" if changed else "stop_failed",
        }


def _create_new_ai_log() -> dict:
    path = _ai_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    was_running = _is_ai_running()
    if was_running:
        _stop_ai_bot()
        if not _wait_ai_stopped(6.0):
            return {"ok": False, "error": "failed to stop AI bot for log rotation"}

    backup_path: Path | None = None
    try:
        if path.exists() and path.stat().st_size > 0:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            candidate = path.with_name(f"{path.stem}-{stamp}{path.suffix}.bak")
            i = 1
            while candidate.exists():
                candidate = path.with_name(f"{path.stem}-{stamp}-{i}{path.suffix}.bak")
                i += 1
            path.replace(candidate)
            backup_path = candidate

        path.write_text("")
    except Exception as e:
        return {"ok": False, "error": f"log rotate failed: {e}"}
    finally:
        if was_running:
            _start_ai_bot()

    return {
        "ok": True,
        "backup": str(backup_path) if backup_path else None,
        "running": _is_ai_running(),
        "log_tail": _tail_text(path, 10),
    }


def _load_ai_config_text() -> str:
    if not AI_CONFIG_PATH.exists():
        return ""
    raw = AI_CONFIG_PATH.read_text()
    try:
        data = yaml.safe_load(raw) or {}
        data.setdefault("credentials", {})
        for k in ("api_key", "api_secret"):
            if data["credentials"].get(k):
                data["credentials"][k] = MASK_TOKEN
        data.setdefault("alerts", {})
        for k in ("telegram_bot_token", "telegram_chat_id"):
            if data["alerts"].get(k):
                data["alerts"][k] = MASK_TOKEN
        return yaml.safe_dump(data, sort_keys=False)
    except Exception:
        return raw


def _save_ai_config_text(raw: str) -> None:
    data = yaml.safe_load(raw) or {}
    # Prevent accidental secret persistence in config.yaml.
    data.setdefault("credentials", {})
    data["credentials"]["api_key"] = ""
    data["credentials"]["api_secret"] = ""
    data.setdefault("alerts", {})
    data["alerts"]["telegram_bot_token"] = ""
    data["alerts"]["telegram_chat_id"] = ""
    AI_CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False))


def _apply_ai_config_runtime_reload() -> dict:
    """Apply updated config.yaml to runtime bot process.

    main.py reads config only at startup, so we must restart a running process.
    If bot is currently stopped, new config will apply on next start.
    """
    with _AI_RELOAD_LOCK:
        was_running = _is_ai_running()
        if not was_running:
            return {"applied": False, "running": False, "message": "bot_not_running"}

        _stop_ai_bot()
        if not _wait_ai_stopped(6.0):
            raise RuntimeError("failed to stop running AI bot for config reload")

        _start_ai_bot()
        if not _is_ai_running():
            raise RuntimeError("failed to restart AI bot after config save")

        return {"applied": True, "running": True, "message": "bot_restarted"}


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 6:
        return "*" * len(v)
    return f"{v[:2]}***{v[-2:]}"


def _load_secrets() -> dict:
    return load_credentials()


def _save_secrets(values: dict) -> None:
    existing = _load_secrets()
    merged = dict(existing)
    for k in ("api_key", "api_secret", "telegram_bot_token", "telegram_chat_id"):
        v = (values.get(k) or "").strip()
        if v:
            merged[k] = v
    save_credentials(merged)


def _secrets_status() -> dict:
    d = _load_secrets()
    return {
        "api_key": _mask(str(d.get("api_key", ""))),
        "api_secret": _mask(str(d.get("api_secret", ""))),
        "telegram_bot_token": _mask(str(d.get("telegram_bot_token", ""))),
        "telegram_chat_id": _mask(str(d.get("telegram_chat_id", ""))),
    }


def _notify_telegram(text: str) -> None:
    try:
        d = _load_secrets()
        token = str(d.get("telegram_bot_token", "") or "").strip()
        chat_id = str(d.get("telegram_chat_id", "") or "").strip()
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000]}).encode("utf-8")
        req = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(req, timeout=15) as resp:
            _ = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return


def _telegram_get_updates(token: str, offset: int | None = None, timeout: int = 0):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    data = {"timeout": max(0, int(timeout))}
    if offset is not None:
        data["offset"] = int(offset)
    payload = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    j = json.loads(raw)
    return j.get("result", []) if isinstance(j, dict) else []


def _offset_path() -> Path:
    return ROOT / ".web_tg_offset"


def _load_offset() -> int | None:
    p = _offset_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except Exception:
        return None


def _save_offset(v: int) -> None:
    _offset_path().write_text(str(int(v)))


def _inbox_path() -> Path:
    return ROOT / ".telegram_inbox.jsonl"


def _append_inbox(chat_id: str, text: str) -> None:
    rec = {"ts": time.time(), "chat_id": str(chat_id), "text": str(text or "")}
    with _inbox_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _poll_server_telegram_commands() -> None:
    # Web server is the single Telegram poll owner by default.
    cfg = _load_main_cfg()
    poll_owner = str((cfg.get("alerts") or {}).get("telegram_polling_owner", "webapp") or "webapp").strip().lower()
    if poll_owner not in ("webapp", "server"):
        return

    d = _load_secrets()
    token = str(d.get("telegram_bot_token", "") or "").strip()
    chat_id = str(d.get("telegram_chat_id", "") or "").strip()
    if not token or not chat_id:
        return

    offset = _load_offset()
    try:
        updates = _telegram_get_updates(token, offset=offset, timeout=0)
    except Exception:
        return

    new_offset = offset
    for upd in updates:
        uid = int(upd.get("update_id", 0))
        new_offset = max(new_offset or 0, uid + 1)
        msg = upd.get("message") or {}
        c = str((msg.get("chat") or {}).get("id", "")).strip()
        if c != chat_id:
            continue

        raw_text = str(msg.get("text") or "").strip()
        text = raw_text.lower()
        if not text.startswith("/"):
            continue

        state = _load_main_state(cfg)
        owner_user_id = str((cfg.get("alerts") or {}).get("telegram_owner_user_id", "") or "").strip()
        msg_user_id = str((msg.get("from") or {}).get("id", "") or "").strip()
        owner_ok = bool(owner_user_id) and (msg_user_id == owner_user_id)
        parts = text.split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ("/approve", "/deny"):
            _append_inbox(chat_id, text)
            continue

        owner_only_cmds = {
            "/start", "/stop", "/restart", "/setrisk", "/setmaxpos", "/confirm", "/cancel", "/pause", "/resume"
        }
        if cmd in owner_only_cmds and not owner_ok:
            _notify_telegram("Owner-only command.")
            continue

        if cmd == "/start":
            result = _set_ai_running(True, "Telegram command")
            if result.get("noop"):
                _notify_telegram("✅ AI bot already running.")
        elif cmd == "/stop":
            result = _set_ai_running(False, "Telegram command")
            if result.get("noop"):
                _notify_telegram("✅ AI bot already stopped.")
        elif cmd == "/status":
            _notify_telegram(_server_status_text())
        elif cmd == "/summary":
            _notify_telegram(_server_summary_text())
        elif cmd == "/help":
            _notify_telegram(HELP_TEXT)
        elif cmd == "/health":
            _notify_telegram(_server_health_text())
        elif cmd == "/positions":
            positions = state.get("positions") or {}
            if not positions:
                _notify_telegram("No open positions.")
            else:
                lines = ["📌 Open positions"]
                for sym, pos in positions.items():
                    lines.append(f"- {sym}: qty={pos.get('qty')} entry={float(pos.get('entry_price', 0.0)):.4f} sl={float(pos.get('sl', 0.0)):.4f}")
                _notify_telegram("\n".join(lines)[:3900])
        elif cmd == "/risk":
            risk = cfg.get("risk") or {}
            _notify_telegram(
                "🛡 Risk config\n"
                f"per_trade_risk_pct: {risk.get('per_trade_risk_pct')}\n"
                f"daily_loss_stop_pct: {risk.get('daily_loss_stop_pct')}\n"
                f"max_concurrent_positions: {risk.get('max_concurrent_positions')}\n"
                f"cooldown_hours: {risk.get('cooldown_hours')}"
            )
        elif cmd in ("/restart", "/setrisk", "/setmaxpos"):
            token_c = secrets.token_hex(3)
            pending = {
                "cmd": cmd.lstrip("/"),
                "token": token_c,
                "requested_at": time.time(),
                "expires_at": int(time.time()) + 120,
                "user_id": msg_user_id,
            }
            if cmd == "/restart":
                pending["value"] = "now"
            elif cmd == "/setrisk":
                if len(parts) < 2:
                    _notify_telegram("Usage: /setrisk <percent>")
                    continue
                try:
                    v = float(parts[1])
                    if v < 0.05 or v > 5.0:
                        raise ValueError("range")
                except Exception:
                    _notify_telegram("Invalid risk. Allowed range: 0.05 ~ 5.0")
                    continue
                pending["value"] = v
            else:
                if len(parts) < 2:
                    _notify_telegram("Usage: /setmaxpos <n>")
                    continue
                try:
                    v = int(parts[1])
                    if v < 1 or v > 20:
                        raise ValueError("range")
                except Exception:
                    _notify_telegram("Invalid max positions. Allowed range: 1 ~ 20")
                    continue
                pending["value"] = v

            state["pending_change"] = pending
            _write_main_state(cfg, state)
            _notify_telegram(
                f"Pending change: {pending['cmd']} -> {pending['value']}\n"
                f"Reply /confirm {token_c} to apply (expires in 120s) or /cancel"
            )
        elif cmd == "/confirm":
            pending = state.get("pending_change") or {}
            pcmd = pending.get("cmd")
            pval = pending.get("value")
            token_in = parts[1].strip().lower() if len(parts) >= 2 else ""
            if not pcmd:
                _notify_telegram("No pending change.")
                continue
            if not token_in:
                _notify_telegram("Usage: /confirm <token>")
                continue
            if token_in != str(pending.get("token", "")).lower() or msg_user_id != str(pending.get("user_id", "")):
                _notify_telegram("Invalid confirm token.")
                continue
            if int(time.time()) > int(pending.get("expires_at", 0)):
                state.pop("pending_change", None)
                _write_main_state(cfg, state)
                _notify_telegram("Pending change expired. Please request again.")
                continue

            if pcmd == "restart":
                state.pop("pending_change", None)
                _write_main_state(cfg, state)
                _notify_telegram("♻️ Restarting AI bot now...")
                _stop_ai_bot()
                time.sleep(1)
                _start_ai_bot()
            elif pcmd == "setrisk":
                old = (cfg.get("risk") or {}).get("per_trade_risk_pct")
                cfg.setdefault("risk", {})["per_trade_risk_pct"] = float(pval)
                state.setdefault("runtime_overrides", {})["per_trade_risk_pct"] = float(pval)
                state.pop("pending_change", None)
                _save_main_cfg(cfg)
                _write_main_state(cfg, state)
                _notify_telegram(f"✅ per_trade_risk_pct updated: {old} -> {pval}")
            elif pcmd == "setmaxpos":
                old = (cfg.get("risk") or {}).get("max_concurrent_positions")
                cfg.setdefault("risk", {})["max_concurrent_positions"] = int(pval)
                state.setdefault("runtime_overrides", {})["max_concurrent_positions"] = int(pval)
                state.pop("pending_change", None)
                _save_main_cfg(cfg)
                _write_main_state(cfg, state)
                _notify_telegram(f"✅ max_concurrent_positions updated: {old} -> {pval}")
        elif cmd == "/cancel":
            pending = state.get("pending_change") or {}
            if pending and msg_user_id != str(pending.get("user_id", "")):
                _notify_telegram("Only the requester can cancel this pending change.")
                continue
            state.pop("pending_change", None)
            _write_main_state(cfg, state)
            _notify_telegram("Cancelled pending change.")
        elif cmd == "/pause":
            state["bot_paused"] = True
            _write_main_state(cfg, state)
            _notify_telegram("⏸ Bot is now PAUSED.")
        elif cmd == "/resume":
            state["bot_paused"] = False
            _write_main_state(cfg, state)
            _notify_telegram("▶️ Bot resumed.")

    if new_offset is not None:
        _save_offset(new_offset)


def _ai_state_path() -> Path:
    if AI_CONFIG_PATH.exists():
        try:
            cfg = yaml.safe_load(AI_CONFIG_PATH.read_text()) or {}
            p = (cfg.get("logging") or {}).get("state_file") or "./state.json"
            return (ROOT / p).resolve()
        except Exception:
            pass
    return (ROOT / "state.json").resolve()


def _ai_network_health() -> dict:
    path = _ai_state_path()
    base = {
        "failures": 0,
        "last_error": "",
        "last_ok_at": None,
        "label": "unknown",
    }
    if not path.exists():
        return {**base, "label": "no_state"}
    try:
        s = json.loads(path.read_text())
        net = ((s.get("runtime_health") or {}).get("network") or {}) if isinstance(s, dict) else {}
        failures = int(net.get("failures", 0) or 0)
        last_error = str(net.get("last_error", "") or "")
        last_ok_at = net.get("last_ok_at")
        if failures <= 0 and not last_error:
            label = "ok"
        elif failures < 3:
            label = "degraded"
        else:
            label = "down"
        return {
            "failures": failures,
            "last_error": last_error,
            "last_ok_at": last_ok_at,
            "label": label,
        }
    except Exception as e:
        return {**base, "label": "parse_error", "last_error": str(e)}


def _ai_log_path() -> Path:
    if AI_CONFIG_PATH.exists():
        try:
            cfg = yaml.safe_load(AI_CONFIG_PATH.read_text()) or {}
            log_dir = (cfg.get("logging") or {}).get("csv_dir") or "./logs"
            return (ROOT / log_dir / "bot.log").resolve()
        except Exception:
            pass
    return (ROOT / "logs" / "bot.log").resolve()


def _load_main_cfg() -> dict:
    if not AI_CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(AI_CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


def _load_main_state(cfg: dict) -> dict:
    try:
        state_rel = (cfg.get("logging") or {}).get("state_file") or "./state.json"
        p = (ROOT / state_rel).resolve()
        if not p.exists():
            return {"positions": {}}
        return json.loads(p.read_text())
    except Exception:
        return {"positions": {}}


def _main_state_path(cfg: dict) -> Path:
    state_rel = (cfg.get("logging") or {}).get("state_file") or "./state.json"
    return (ROOT / state_rel).resolve()


def _write_main_state(cfg: dict, state: dict) -> None:
    p = _main_state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _save_main_cfg(cfg: dict) -> None:
    AI_CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))


def _server_status_text() -> str:
    running = _is_ai_running()
    cfg = _load_main_cfg()
    state = _load_main_state(cfg)
    positions = state.get("positions") or {}
    risk = cfg.get("risk") or {}
    syms = (cfg.get("general") or {}).get("symbols") or []
    base = (cfg.get("general") or {}).get("base_currency") or "USDT"
    paused = bool(state.get("bot_paused", False))
    return (
        "📊 Current status\n"
        f"mode: {'PAUSED' if paused else ('RUNNING' if running else 'STOPPED')}\n"
        f"dry_run: {(cfg.get('general') or {}).get('dry_run')}\n"
        f"base_currency: {base}\n"
        f"open_positions: {len(positions)}\n"
        f"symbols: {', '.join(syms)}\n"
        f"risk: per_trade={risk.get('per_trade_risk_pct')}%, max_pos={risk.get('max_concurrent_positions')}"
    )


def _server_summary_text() -> str:
    cfg = _load_main_cfg()
    state = _load_main_state(cfg)
    base = (cfg.get("general") or {}).get("base_currency") or "USDT"

    equity_now = None
    try:
        csv_dir = Path((cfg.get("logging") or {}).get("csv_dir") or "./logs")
        eq_file = csv_dir / "equity.csv"
        if eq_file.exists():
            rows = [ln for ln in eq_file.read_text(errors="ignore").splitlines() if ln.strip()]
            if len(rows) >= 2:
                last = rows[-1].split(",")
                if len(last) >= 2:
                    equity_now = float(last[1])
    except Exception:
        equity_now = None

    if equity_now is None:
        session = state.get("session") if isinstance(state.get("session"), dict) else {}
        equity_now = float(session.get("equity_start") or 0.0)

    # Enrich open-position summary with latest known price from chart DB.
    try:
        tf = str((cfg.get("general") or {}).get("timeframe_signal") or "1h")
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        for sym, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            rows = storage.fetch_ohlcv(sym, tf, 1)
            if rows:
                pos["current_price"] = float(rows[-1][4])
    except Exception:
        pass

    return build_summary_text(
        cfg,
        state,
        equity_now=float(equity_now),
        base_ccy=base,
        now_ts=dt.datetime.now(dt.timezone.utc),
        running=_is_ai_running(),
    )


def _server_health_text() -> str:
    cfg = _load_main_cfg()
    state = _load_main_state(cfg)
    owner = str((cfg.get("alerts") or {}).get("telegram_owner_user_id", "") or "").strip()
    pending = state.get("pending_change") or {}
    return (
        "🩺 Health\n"
        f"ai_running: {'yes' if _is_ai_running() else 'no'}\n"
        f"owner_configured: {'yes' if owner else 'no'}\n"
        f"pending_change: {'yes' if bool(pending) else 'no'}\n"
        f"state_file: {str(_main_state_path(cfg))}\n"
        f"paused: {'yes' if bool(state.get('bot_paused', False)) else 'no'}"
    )


def _tail_text(path: Path, lines: int = 10) -> str:
    if not path.exists():
        return "(log file not found yet)"
    try:
        arr = path.read_text(errors="ignore").splitlines()
        tail = arr[-max(lines, 1):]
        return "\n".join(tail) if tail else "(empty log)"
    except Exception as e:
        return f"(failed to read log: {e})"


def _clean_console_output(text: str) -> str:
    # Remove ANSI escape sequences and normalize carriage-return updates.
    text = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text or "")
    # Some scripts print literal backslash-n; convert for readability.
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


def _error_response(code: str, message: str, status_code: int = 400):
    return JSONResponse(status_code=status_code, content={"ok": False, "error": {"code": code, "message": message}})


def _run_legacy_backtest(values: Dict):
    cmd = legacy_build_command(values)
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    raw_output = (proc.stdout or "")
    if not raw_output.strip():
        raw_output = proc.stderr or ""
    output = _clean_console_output(raw_output)
    plots = list_plot_files()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": output,
        "plots": plots,
    }


@app.on_event("startup")
def startup():
    global scheduler
    cfg = config_mgr.load()
    scheduler = BackgroundScheduler()
    try:
        scheduler.add_job(lambda: chart_service.refresh_all(cfg), "cron", **parse_cron(cfg.refresh_cron), id="daily_chart_refresh", replace_existing=True)
    except Exception:
        pass
    # Telegram command polling (server-level fallback, useful when AI bot is down)
    scheduler.add_job(_poll_server_telegram_commands, "interval", seconds=5, id="telegram_command_poll", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.start()

    # Initialize command offset once to avoid replaying old chat history.
    if _load_offset() is None:
        try:
            d = _load_secrets()
            token = str(d.get("telegram_bot_token", "") or "").strip()
            if token:
                updates = _telegram_get_updates(token, offset=None, timeout=0)
                if updates:
                    _save_offset(int(updates[-1].get("update_id", 0)) + 1)
        except Exception:
            pass


@app.on_event("shutdown")
def shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    if not _auth_enabled():
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": error or ""})


@app.post("/login")
def login_submit(request: Request):
    if not _auth_enabled():
        return JSONResponse({"ok": True, "redirect": "/"})

    username = (request.headers.get("x-btb-user") or "").strip()
    password = request.headers.get("x-btb-pass") or ""

    if username != _auth_user() or password != _auth_pass() or not _auth_pass():
        return JSONResponse({"ok": False, "error": "Invalid credentials"}, status_code=401)

    resp = JSONResponse({"ok": True, "redirect": "/"})
    secure_cookie = os.environ.get("BTB_WEB_COOKIE_SECURE", "1").strip() not in ("0", "false", "False")
    resp.set_cookie(_session_cookie_name(), _make_session_token(username), httponly=True, samesite="lax", secure=secure_cookie, max_age=60 * 60 * 12)
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_session_cookie_name())
    return resp


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    cfg = config_mgr.load()
    runtime = _chart_runtime_params()
    symbol = runtime["symbols"][0] if runtime["symbols"] else "BTC/USDT"
    series = chart_service.series(symbol, runtime["timeframe"], runtime["history_limit"])
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "config": cfg.model_dump(),
            "legacy_backtester_defaults": LEGACY_BT_DEFAULTS,
            "default_symbol": symbol,
            "series": series,
            "last_refresh": storage.get_meta("last_chart_refresh") or "(never)",
            "ai_running": _is_ai_running(),
            "ai_config_text": _load_ai_config_text(),
            "secrets_status": _secrets_status(),
        },
    )


@app.get("/api/config")
def get_config():
    return config_mgr.load().model_dump()


@app.put("/api/config")
def update_config(payload: Dict = Body(...)):
    cfg = UIConfig(**payload)
    config_mgr.save(cfg)
    return {"ok": True, "config": cfg.model_dump()}


@app.post("/api/config/load")
def load_config_file():
    cfg = config_mgr.load()
    return {"ok": True, "config": cfg.model_dump()}


@app.post("/api/config/save")
def save_config_file(payload: Dict = Body(...)):
    cfg = UIConfig(**payload)
    config_mgr.save(cfg)
    return {"ok": True}


@app.post("/api/config/save2")
async def save_config_file_v2(request: Request):
    # Body parsing fallback-free route: config payload comes from base64 header.
    encoded = (request.headers.get("x-btb-config") or "").strip()
    if not encoded:
        return _error_response("MISSING_CONFIG", "x-btb-config header is required", 400)
    try:
        raw = base64.b64decode(encoded + "===").decode("utf-8", errors="strict")
        payload = json.loads(raw)
        cfg = UIConfig(**(payload or {}))
        config_mgr.save(cfg)
        return {"ok": True}
    except Exception as e:
        return _error_response("INVALID_CONFIG", str(e), 400)


def _moving_average(values: list[float], window: int) -> list[float | None]:
    if window <= 0:
        return [None] * len(values)
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += float(v)
        if i >= window:
            running -= float(values[i - window])
        if i >= window - 1:
            out[i] = running / float(window)
    return out


def _effective_main_config() -> dict:
    cfg = _load_main_cfg()
    if not isinstance(cfg, dict):
        return {}

    if bool((cfg.get("general") or {}).get("aggressive_mode", False)):
        agg = cfg.get("aggressive") or {}
        if isinstance(agg, dict):
            out = dict(cfg)
            for section in ("general", "risk", "strategy"):
                base_sec = dict((cfg.get(section) or {})) if isinstance(cfg.get(section), dict) else {}
                agg_sec = dict((agg.get(section) or {})) if isinstance(agg.get(section), dict) else {}
                merged = {**base_sec, **agg_sec}
                out[section] = merged
            return out
    return cfg


def _chart_runtime_params() -> dict:
    web_cfg = config_mgr.load()
    main_cfg = _effective_main_config()
    general = (main_cfg.get("general") or {}) if isinstance(main_cfg, dict) else {}

    timeframe = str(general.get("timeframe_signal") or web_cfg.timeframe)
    symbols = list(general.get("symbols") or web_cfg.symbols)
    history_limit = int(web_cfg.history_limit)
    return {
        "timeframe": timeframe,
        "symbols": symbols,
        "history_limit": history_limit,
    }


def _signal_params_from_main_config() -> tuple[int, int]:
    main_cfg = _effective_main_config()
    strategy = (main_cfg.get("strategy") or {}) if isinstance(main_cfg, dict) else {}

    def _as_pos_int(v, default: int) -> int:
        try:
            n = int(v)
            return n if n > 0 else default
        except Exception:
            return default

    fast = _as_pos_int(strategy.get("ema_fast", 20), 20)
    slow = _as_pos_int(strategy.get("ema_slow", 60), 60)
    if fast == slow:
        slow = fast + 1
    return fast, slow


def _build_price_signal_markers(labels: list[str], closes: list[float], fast: int, slow: int) -> list[dict]:
    if len(closes) < max(fast, slow) + 2:
        return []

    fast_ma = _moving_average(closes, fast)
    slow_ma = _moving_average(closes, slow)
    markers: list[dict] = []

    for idx in range(1, len(closes)):
        pf, ps = fast_ma[idx - 1], slow_ma[idx - 1]
        cf, cs = fast_ma[idx], slow_ma[idx]
        if pf is None or ps is None or cf is None or cs is None:
            continue

        if pf <= ps and cf > cs:
            markers.append({
                "index": idx,
                "label": labels[idx] if idx < len(labels) else "",
                "price": float(closes[idx]),
                "side": "buy",
                "reason": f"ema_cross_up(fast={fast},slow={slow})",
            })
        elif pf >= ps and cf < cs:
            markers.append({
                "index": idx,
                "label": labels[idx] if idx < len(labels) else "",
                "price": float(closes[idx]),
                "side": "sell",
                "reason": f"ema_cross_down(fast={fast},slow={slow})",
            })

    return markers


@app.get("/api/charts")
def get_chart(symbol: str):
    runtime = _chart_runtime_params()
    fast, slow = _signal_params_from_main_config()

    display_limit = int(runtime["history_limit"])
    calc_limit = max(display_limit + slow + 5, display_limit)
    rows = storage.fetch_ohlcv(symbol, runtime["timeframe"], limit=calc_limit)

    labels_all = [dt.datetime.utcfromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d") for ts, *_ in rows]
    closes_all = [float(c) for _, _, _, _, c, _ in rows]
    markers_all = _build_price_signal_markers(labels_all, closes_all, fast=fast, slow=slow)
    insufficient_signal_data = len(closes_all) < (slow + 2)

    trim_start = max(0, len(labels_all) - display_limit)
    labels = labels_all[trim_start:]
    closes = closes_all[trim_start:]

    markers = []
    for m in markers_all:
        idx = int(m.get("index", -1))
        if idx < trim_start:
            continue
        m2 = dict(m)
        m2["index"] = idx - trim_start
        markers.append(m2)

    return {
        "symbol": symbol,
        "timeframe": runtime["timeframe"],
        "labels": labels,
        "values": closes,
        "signal_basis": f"config.yaml ({'aggressive' if bool((_load_main_cfg().get('general') or {}).get('aggressive_mode', False)) else 'normal'}) strategy.ema_fast/ema_slow crossover",
        "signal_params": {"fast": fast, "slow": slow},
        "signal_note": f"Need at least {slow + 2} candles for reliable EMA crossover (current={len(closes_all)})" if insufficient_signal_data else "",
        "markers": markers,
    }


@app.post("/api/charts/refresh")
def refresh_charts(req: RefreshRequest = Body(default=RefreshRequest())):
    runtime = _chart_runtime_params()
    fast, slow = _signal_params_from_main_config()
    refresh_limit = max(int(runtime["history_limit"]) + slow + 5, int(runtime["history_limit"]))

    targets = req.symbols or runtime["symbols"]
    refreshed: list[str] = []
    errors: dict[str, str] = {}

    for sym in targets:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(chart_service.refresh_symbol, sym, runtime["timeframe"], refresh_limit)
                fut.result(timeout=20)
            refreshed.append(sym)
        except FuturesTimeoutError:
            errors[sym] = "timeout while fetching OHLCV"
        except Exception as e:
            errors[sym] = str(e)

    storage.set_meta("last_chart_refresh", dt.datetime.utcnow().isoformat(timespec="seconds"))

    return {
        "ok": len(errors) == 0,
        "last_refresh": storage.get_meta("last_chart_refresh"),
        "signal_params": {"fast": fast, "slow": slow},
        "refreshed": refreshed,
        "errors": errors,
    }


@app.get("/api/charts/refresh2")
def refresh_charts_v2(symbol: str | None = None):
    cfg = config_mgr.load()
    req = RefreshRequest(symbols=[symbol] if symbol else None)
    return refresh_charts(req)


@app.post("/api/backtester/run")
def run_backtester(req: BacktestRequest):
    cfg = config_mgr.load()
    return backtest_service.run_sma_crossover(
        symbol=req.symbol,
        timeframe=cfg.timeframe,
        fast=req.fast_window,
        slow=req.slow_window,
        starting_capital=req.starting_capital or cfg.starting_capital,
        fee_rate=req.fee_rate if req.fee_rate is not None else cfg.fee_rate,
    )


@app.get("/api/ai/status")
def ai_status():
    running = _is_ai_running()
    return {
        "running": running,
        "can_start": not running,
        "can_stop": running,
        "log_tail": _tail_text(_ai_log_path(), 10),
        "network_health": _ai_network_health(),
    }


@app.get("/api/system/health")
def system_health():
    cfg = _load_main_cfg()
    state = _load_main_state(cfg)
    return {
        "ok": True,
        "ai_running": _is_ai_running(),
        "last_loop_ts": state.get("last_loop_ts") or state.get("last_tick_ts") or state.get("updated_at"),
        "network_health": _ai_network_health(),
        "pending_change": state.get("pending_change") or {},
        "auth_enabled": _auth_enabled(),
    }


@app.post("/api/ai/start")
def ai_start():
    result = _set_ai_running(True, "Web UI")
    result["log_tail"] = _tail_text(_ai_log_path(), 10)
    return result


@app.post("/api/ai/stop")
def ai_stop():
    result = _set_ai_running(False, "Web UI")
    result["log_tail"] = _tail_text(_ai_log_path(), 10)
    return result


@app.get("/api/ai/logs")
def ai_logs(lines: int = 10):
    return {"ok": True, "lines": lines, "tail": _tail_text(_ai_log_path(), lines)}


@app.get("/api/ai/logs/download")
def ai_logs_download():
    path = _ai_log_path()
    if not path.exists():
        return {"ok": False, "error": "log file not found"}
    return FileResponse(path=str(path), media_type="text/plain", filename="ai_bot.log")


@app.post("/api/ai/logs/new")
def ai_logs_new():
    return _create_new_ai_log()


@app.get("/api/ai/config")
def ai_config_get():
    return {"text": _load_ai_config_text()}


@app.post("/api/ai/config")
def ai_config_save(payload: Dict = Body(...)):
    try:
        raw = str(payload.get("text", ""))
        _save_ai_config_text(raw)
        reload_status = _apply_ai_config_runtime_reload()
        return {"ok": True, "runtime_reload": reload_status}
    except RuntimeError as e:
        return _error_response("RUNTIME_RELOAD_FAILED", str(e), 500)


@app.post("/api/ai/config2")
async def ai_config_save_v2(request: Request):
    encoded = (request.headers.get("x-btb-ai-config") or "").strip()
    if not encoded:
        return _error_response("MISSING_CONFIG", "x-btb-ai-config header is required", 400)
    try:
        raw = base64.b64decode(encoded + "===").decode("utf-8", errors="strict")
        _save_ai_config_text(raw)
        reload_status = _apply_ai_config_runtime_reload()
        return {"ok": True, "runtime_reload": reload_status}
    except RuntimeError as e:
        return _error_response("RUNTIME_RELOAD_FAILED", str(e), 500)
    except Exception as e:
        return _error_response("INVALID_CONFIG", str(e), 400)


@app.get("/api/ai/secrets")
def ai_secrets_get():
    return {"ok": True, "status": _secrets_status()}


@app.post("/api/ai/secrets")
def ai_secrets_save(payload: Dict = Body(...)):
    try:
        _save_secrets(payload or {})
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "status": _secrets_status()}


@app.post("/api/ai/secrets2")
async def ai_secrets_save_v2(request: Request):
    encoded = (request.headers.get("x-btb-secrets") or "").strip()
    if not encoded:
        return _error_response("MISSING_SECRETS", "x-btb-secrets header is required", 400)
    try:
        raw = base64.b64decode(encoded + "===").decode("utf-8", errors="strict")
        payload = json.loads(raw)
        _save_secrets(payload or {})
        return {"ok": True, "status": _secrets_status()}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return _error_response("INVALID_SECRETS", str(e), 400)


@app.post("/api/backtest/run")
def run_backtest_unified(payload: Dict = Body(...)):
    try:
        cfg = config_mgr.load()
        mode = str((payload or {}).get("mode", "quick")).strip().lower()
        if mode not in ("quick", "legacy", "both"):
            return _error_response("INVALID_MODE", "mode must be one of quick|legacy|both", 400)

        symbol = str((payload or {}).get("symbol") or (cfg.symbols[0] if cfg.symbols else "BTC/USDT"))
        timeframe = str((payload or {}).get("timeframe") or cfg.timeframe)
        main_cfg = _load_main_cfg()
        stg = (main_cfg.get("strategy") or {}) if isinstance(main_cfg, dict) else {}
        fast_default = int(stg.get("ema_fast", 20) or 20)
        slow_default = int(stg.get("ema_slow", 60) or 60)
        fast = int((payload or {}).get("fast_window", fast_default))
        slow = int((payload or {}).get("slow_window", slow_default))
        starting_capital = float((payload or {}).get("starting_capital", cfg.starting_capital))
        fee_rate = float((payload or {}).get("fee_rate", cfg.fee_rate))

        results = []
        if mode in ("quick", "both"):
            quick_raw = backtest_service.run_sma_crossover(symbol, timeframe, fast, slow, starting_capital, fee_rate)
            results.append(backtest_service.to_unified_quick(symbol, quick_raw))

        if mode in ("legacy", "both"):
            values = dict(LEGACY_BT_DEFAULTS)
            values["symbols"] = symbol
            values["timeframe"] = timeframe
            if "legacy" in payload and isinstance(payload.get("legacy"), dict):
                for k, v in payload.get("legacy", {}).items():
                    if k in LEGACY_BT_DEFAULTS and v is not None:
                        values[k] = str(v)
            errors = legacy_validate_values(values)
            if errors:
                return _error_response("LEGACY_VALIDATION_ERROR", "; ".join(errors), 400)
            legacy_raw = _run_legacy_backtest(values)
            results.append(backtest_service.to_unified_legacy(symbol, legacy_raw.get("output", ""), legacy_raw.get("returncode", 1)))

        return {
            "ok": True,
            "mode": mode,
            "results": results,
        }
    except ValueError as e:
        return _error_response("VALIDATION_ERROR", str(e), 400)
    except Exception as e:
        return _error_response("BACKTEST_RUN_FAILED", str(e), 500)


@app.post("/api/legacy/backtester/run")
def run_legacy_backtester(payload: Dict = Body(...)):
    values = dict(LEGACY_BT_DEFAULTS)
    for k in LEGACY_BT_DEFAULTS.keys():
        if k in payload and payload[k] is not None:
            values[k] = str(payload[k])

    errors = legacy_validate_values(values)
    if errors:
        return {"ok": False, "errors": errors}

    return _run_legacy_backtest(values)


@app.get("/api/health")
def health():
    return {"ok": True}
