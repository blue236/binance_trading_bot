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
from pathlib import Path
from typing import Dict

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Body, Request, Form
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
    # Unified Telegram command handler at web server level.
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
        text = str(msg.get("text") or "").strip().lower()
        if not text.startswith("/"):
            continue

        cfg = _load_main_cfg()
        state = _load_main_state(cfg)
        parts = text.split()
        cmd = parts[0].lower() if parts else ""

        if cmd == "/start":
            if _is_ai_running():
                _notify_telegram("✅ AI bot already running.")
            else:
                _start_ai_bot()
                _notify_telegram("🟢 AI bot started from Telegram command")
        elif cmd == "/stop":
            if _is_ai_running():
                _stop_ai_bot()
                _notify_telegram("🛑 AI bot stopped from Telegram command")
            else:
                _notify_telegram("✅ AI bot already stopped.")
        elif cmd == "/status":
            _notify_telegram(_server_status_text())
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
        elif cmd == "/setrisk":
            if len(parts) < 2:
                _notify_telegram("Usage: /setrisk <percent>")
            else:
                try:
                    v = float(parts[1])
                    if v < 0.05 or v > 5.0:
                        raise ValueError("range")
                    pending = {"cmd": "setrisk", "value": v, "requested_at": time.time()}
                    state["pending_change"] = pending
                    _write_main_state(cfg, state)
                    _notify_telegram(f"Pending change: setrisk -> {v}\nReply /confirm to apply or /cancel to discard.")
                except Exception:
                    _notify_telegram("Invalid risk. Allowed range: 0.05 ~ 5.0")
        elif cmd == "/setmaxpos":
            if len(parts) < 2:
                _notify_telegram("Usage: /setmaxpos <n>")
            else:
                try:
                    v = int(parts[1])
                    if v < 1 or v > 20:
                        raise ValueError("range")
                    pending = {"cmd": "setmaxpos", "value": v, "requested_at": time.time()}
                    state["pending_change"] = pending
                    _write_main_state(cfg, state)
                    _notify_telegram(f"Pending change: setmaxpos -> {v}\nReply /confirm to apply or /cancel to discard.")
                except Exception:
                    _notify_telegram("Invalid max positions. Allowed range: 1 ~ 20")
        elif cmd == "/confirm":
            pending = state.get("pending_change") or {}
            pcmd = pending.get("cmd")
            pval = pending.get("value")
            if not pcmd:
                _notify_telegram("No pending change.")
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
        elif cmd == "/help":
            _notify_telegram("Available commands: /status, /positions, /risk, /setrisk, /setmaxpos, /confirm, /cancel, /pause, /resume, /start, /stop, /help")
        elif text.startswith("approve ") or text.startswith("deny ") or text.startswith("/approve ") or text.startswith("/deny "):
            _append_inbox(chat_id, text)

    if new_offset is not None:
        _save_offset(new_offset)


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
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not _auth_enabled():
        return RedirectResponse(url="/", status_code=302)
    if username != _auth_user() or password != _auth_pass() or not _auth_pass():
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    resp = RedirectResponse(url="/", status_code=302)
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
    symbol = cfg.symbols[0] if cfg.symbols else "BTC/USDT"
    series = chart_service.series(symbol, cfg.timeframe, cfg.history_limit)
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


@app.get("/api/charts")
def get_chart(symbol: str):
    cfg = config_mgr.load()
    return chart_service.series(symbol, cfg.timeframe, cfg.history_limit)


@app.post("/api/charts/refresh")
def refresh_charts(req: RefreshRequest = Body(default=RefreshRequest())):
    cfg = config_mgr.load()
    chart_service.refresh_all(cfg, req.symbols)
    return {"ok": True, "last_refresh": storage.get_meta("last_chart_refresh")}


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
    return {"running": _is_ai_running(), "log_tail": _tail_text(_ai_log_path(), 10)}


@app.post("/api/ai/start")
def ai_start():
    _start_ai_bot()
    if _is_ai_running():
        _notify_telegram("🟢 AI bot started from Web UI")
    return {"ok": True, "running": _is_ai_running(), "log_tail": _tail_text(_ai_log_path(), 10)}


@app.post("/api/ai/stop")
def ai_stop():
    _stop_ai_bot()
    _notify_telegram("🛑 AI bot stopped from Web UI")
    return {"ok": True, "running": _is_ai_running(), "log_tail": _tail_text(_ai_log_path(), 10)}


@app.get("/api/ai/logs")
def ai_logs(lines: int = 10):
    return {"ok": True, "lines": lines, "tail": _tail_text(_ai_log_path(), lines)}


@app.get("/api/ai/logs/download")
def ai_logs_download():
    path = _ai_log_path()
    if not path.exists():
        return {"ok": False, "error": "log file not found"}
    return FileResponse(path=str(path), media_type="text/plain", filename="ai_bot.log")


@app.get("/api/ai/config")
def ai_config_get():
    return {"text": _load_ai_config_text()}


@app.post("/api/ai/config")
def ai_config_save(payload: Dict = Body(...)):
    raw = str(payload.get("text", ""))
    _save_ai_config_text(raw)
    return {"ok": True}


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


@app.post("/api/legacy/backtester/run")
def run_legacy_backtester(payload: Dict = Body(...)):
    values = dict(LEGACY_BT_DEFAULTS)
    for k in LEGACY_BT_DEFAULTS.keys():
        if k in payload and payload[k] is not None:
            values[k] = str(payload[k])

    errors = legacy_validate_values(values)
    if errors:
        return {"ok": False, "errors": errors}

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


@app.get("/api/health")
def health():
    return {"ok": True}
