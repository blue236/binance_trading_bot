from __future__ import annotations

from datetime import datetime, timedelta, timezone

HELP_TEXT = (
    "Available commands: /status, /positions, /risk, /summary, /health, "
    "/setrisk, /setmaxpos, /setcooldown, /mode, /restart, /confirm <token>, "
    "/cancel, /pause, /resume, /start, /stop, /help "
    "(owner-only for state-changing commands)"
)


def build_summary_text(cfg, state, equity_now, base_ccy, now_ts=None, running=True):
    state = state or {}
    risk = cfg.get("risk", {})
    alerts = cfg.get("alerts", {})
    positions = state.get("positions", {}) if isinstance(state, dict) else {}
    max_pos = int(risk.get("max_concurrent_positions", 0) or 0)
    paused = bool(state.get("bot_paused", False))
    runtime_mode = state.get("runtime_mode") or (
        "aggressive" if cfg.get("general", {}).get("aggressive_mode") else "normal"
    )
    approval = bool(alerts.get("enable_trade_approval", False))

    session = state.get("session", {}) if isinstance(state.get("session", {}), dict) else {}
    session_eq = session.get("equity_start")
    pnl_abs = None
    pnl_pct = None
    if session_eq is not None:
        try:
            session_eq = float(session_eq)
            if session_eq > 0:
                pnl_abs = float(equity_now) - session_eq
                pnl_pct = (pnl_abs / session_eq) * 100.0
        except Exception:
            session_eq = None

    now_obj = now_ts or datetime.now(timezone.utc)
    now_str = now_obj.strftime("%Y-%m-%d %H:%M:%S")

    cooldowns = state.get("cooldowns", {}) if isinstance(state.get("cooldowns", {}), dict) else {}
    cooldown_h = float(risk.get("cooldown_hours", 0) or 0)
    cooldown_active = 0
    if cooldown_h > 0:
        for _, iso_ts in cooldowns.items():
            try:
                cdt = datetime.fromisoformat(str(iso_ts))
                if cdt.tzinfo is None:
                    cdt = cdt.replace(tzinfo=now_obj.tzinfo or timezone.utc)
                if (now_obj - cdt) < timedelta(hours=cooldown_h):
                    cooldown_active += 1
            except Exception:
                continue

    health = state.get("runtime_health", {}) if isinstance(state.get("runtime_health", {}), dict) else {}
    network = health.get("network", {}) if isinstance(health.get("network", {}), dict) else {}
    network_label = network.get("last_label") or "unknown"
    net_failures = int(network.get("consecutive_failures", 0) or 0)
    last_loop_at = health.get("last_loop_at") or "n/a"

    pnl_line = "session PnL: n/a"
    if pnl_abs is not None and pnl_pct is not None:
        pnl_line = f"session PnL: {pnl_abs:+.2f} {base_ccy} ({pnl_pct:+.2f}%)"

    mode_label = "STOPPED" if not running and not paused else ("PAUSED" if paused else "RUNNING")

    pos_details = []
    for i, (sym, pos) in enumerate((positions or {}).items()):
        if i >= 5:
            break
        p = pos if isinstance(pos, dict) else {}
        entry = p.get("entry_price")
        current = p.get("current_price", p.get("last_price"))
        try:
            entry_txt = f"{float(entry):.4f}" if entry is not None else "n/a"
        except Exception:
            entry_txt = "n/a"
        try:
            current_txt = f"{float(current):.4f}" if current is not None else "n/a"
        except Exception:
            current_txt = "n/a"
        pos_details.append(f"- {sym}: buy={entry_txt} | now={current_txt}")

    pos_block = "\nPosition detail:\n"
    if pos_details:
        pos_block += "\n".join(pos_details)
    else:
        pos_block += "- (no open positions)"

    return (
        "📌 Ops Summary\n"
        f"Mode: {mode_label} / {runtime_mode} | dry_run={cfg.get('general', {}).get('dry_run')} | approval={'ON' if approval else 'OFF'}\n"
        f"Equity: {float(equity_now):.2f} {base_ccy}"
        + (f" (start {session_eq:.2f})\n" if session_eq is not None else "\n")
        + f"Profit-rate: {pnl_line}\n"
        + f"Positions: {len(positions)}/{max_pos if max_pos > 0 else '-'} | cooldown_active={cooldown_active}"
        + pos_block + "\n"
        + f"Risk gate: daily_loss_stop={risk.get('daily_loss_stop_pct')}% | pending_change={'YES' if bool(state.get('pending_change')) else 'NO'}\n"
        + f"Health: network={network_label} (fail={net_failures}) | last_loop={last_loop_at}\n"
        + f"Updated: {now_str}"
    )
