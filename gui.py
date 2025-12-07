#!/usr/bin/env python3
import os, sys, yaml, subprocess, json, signal, time
import PySimpleGUI as sg

CONFIG_PATH = "config.yaml"
PID_FILE = ".bot_pid"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

def is_bot_running():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except:
        return False

def start_bot(python_exec="python", cwd=None):
    if is_bot_running():
        return
    proc = subprocess.Popen([python_exec, "main.py"], cwd=cwd or os.getcwd(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

def stop_bot():
    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except Exception as e:
        pass
    finally:
        try: os.remove(PID_FILE)
        except: pass

def layout_from_cfg(cfg):
    gen = cfg.get("general", {})
    risk = cfg.get("risk", {})
    strat = cfg.get("strategy", {})
    alerts = cfg.get("alerts", {})
    logging = cfg.get("logging", {})
    creds = cfg.get("credentials", {})

    layout = [
        [sg.Text("Binance Spot Auto-Trader — GUI Config", font=("Helvetica", 16), justification="center")],
        [sg.Frame("Credentials", [
            [sg.Text("API Key", size=(18,1)), sg.Input(creds.get("api_key",""), key="-APIKEY-", password_char="*")],
            [sg.Text("API Secret", size=(18,1)), sg.Input(creds.get("api_secret",""), key="-APISECRET-", password_char="*")],
        ])],
        [sg.Frame("General", [
            [sg.Text("Symbols (comma separated)"), sg.Input(", ".join(gen.get("symbols",["BTC/USDT","ETH/USDT"])), key="-SYMS-")],
            [sg.Text("Signal TF"), sg.Input(gen.get("timeframe_signal","1h"), key="-TFSIG-", size=(8,1)),
             sg.Text("Regime TF"), sg.Input(gen.get("timeframe_regime","1d"), key="-TFREG-", size=(8,1)),
             sg.Checkbox("Dry Run (paper)", default=gen.get("dry_run", True), key="-DRY-")],
            [sg.Text("Min Notional (USDT)"), sg.Input(str(gen.get("min_notional_usdt",10)), key="-MINNOT-", size=(10,1))],
        ])],
        [sg.Frame("Risk", [
            [sg.Text("Per-Trade Risk (%)"), sg.Input(str(risk.get("per_trade_risk_pct",0.5)), key="-RTRISK-", size=(10,1)),
             sg.Text("Daily Loss Stop (%)"), sg.Input(str(risk.get("daily_loss_stop_pct",2.0)), key="-DDSTOP-", size=(10,1))],
            [sg.Text("Max Concurrent Positions"), sg.Input(str(risk.get("max_concurrent_positions",3)), key="-MAXPOS-", size=(10,1)),
             sg.Text("Cooldown Hours"), sg.Input(str(risk.get("cooldown_hours",8)), key="-COOLD-", size=(10,1))],
        ])],
        [sg.Frame("Strategy", [
            [sg.Text("ADX Len"), sg.Input(str(strat.get("adx_len",14)), key="-ADXLEN-", size=(6,1)),
             sg.Text("Trend ADX Thresh"), sg.Input(str(strat.get("trend_adx_threshold",20)), key="-ADXTH-", size=(6,1))],
            [sg.Text("EMA Slow"), sg.Input(str(strat.get("ema_slow",200)), key="-EMASLOW-", size=(6,1)),
             sg.Text("EMA Fast"), sg.Input(str(strat.get("ema_fast",50)), key="-EMAFAST-", size=(6,1))],
            [sg.Text("Donchian Len"), sg.Input(str(strat.get("donchian_len",20)), key="-DONLEN-", size=(6,1)),
             sg.Text("RSI Len"), sg.Input(str(strat.get("rsi_len",14)), key="-RSILEN-", size=(6,1)),
             sg.Text("RSI Overheat"), sg.Input(str(strat.get("rsi_overheat",80)), key="-RSIHOT-", size=(6,1))],
            [sg.Text("ATR Len"), sg.Input(str(strat.get("atr_len",14)), key="-ATRLEN-", size=(6,1)),
             sg.Text("ATR SL Trend x"), sg.Input(str(strat.get("atr_sl_trend_mult",1.5)), key="-ATRSLT-", size=(6,1)),
             sg.Text("ATR Trail x"), sg.Input(str(strat.get("atr_trail_mult",2.5)), key="-ATRTRAIL-", size=(6,1))],
            [sg.Text("BB Len"), sg.Input(str(strat.get("bb_len",20)), key="-BBLEN-", size=(6,1)),
             sg.Text("BB Mult"), sg.Input(str(strat.get("bb_mult",2.0)), key="-BBMUL-", size=(6,1)),
             sg.Text("ATR SL MR x"), sg.Input(str(strat.get("atr_sl_mr_mult",1.2)), key="-ATRMR-", size=(6,1)),
             sg.Text("RSI MR Thr"), sg.Input(str(strat.get("rsi_mr_threshold",30)), key="-RSIMR-", size=(6,1))],
            [sg.Text("MR Time Stop (h)"), sg.Input(str(strat.get("mean_reversion_time_stop_hours",24)), key="-MRH-", size=(6,1))],
        ])],
        [sg.Frame("Alerts (Telegram)", [
            [sg.Checkbox("Enable Telegram", default=alerts.get("enable_telegram", False), key="-TGON-")],
            [sg.Text("Bot Token", size=(12,1)), sg.Input(alerts.get("telegram_bot_token",""), key="-TGTOK-", password_char="*")],
            [sg.Text("Chat ID", size=(12,1)), sg.Input(alerts.get("telegram_chat_id",""), key="-TGCHAT-")],
        ])],
        [sg.Frame("Logging", [
            [sg.Text("CSV Dir"), sg.Input(logging.get("csv_dir","./logs"), key="-CSV-"),
             sg.Text("State File"), sg.Input(logging.get("state_file","./state.json"), key="-STATE-"),
             sg.Text("TZ"), sg.Input(logging.get("tz","UTC"), key="-TZ-", size=(10,1))],
        ])],
        [sg.Column([[sg.Button("Save Config", button_color=("white","#007bff")),
                     sg.Button("Start Bot", button_color=("white","#28a745")),
                     sg.Button("Stop Bot", button_color=("white","#dc3545")),
                     sg.Button("Exit")]], justification="center")]
    ]
    return layout

def collect_cfg(values, current):
    def csv_to_list(s):
        return [x.strip() for x in s.split(",") if x.strip()]
    cfg = current or {}
    cfg["general"] = cfg.get("general", {})
    cfg["risk"] = cfg.get("risk", {})
    cfg["strategy"] = cfg.get("strategy", {})
    cfg["alerts"] = cfg.get("alerts", {})
    cfg["logging"] = cfg.get("logging", {})
    cfg["credentials"] = cfg.get("credentials", {})

    cfg["credentials"]["api_key"] = values["-APIKEY-"]
    cfg["credentials"]["api_secret"] = values["-APISECRET-"]

    cfg["general"]["symbols"] = csv_to_list(values["-SYMS-"])
    cfg["general"]["timeframe_signal"] = values["-TFSIG-"]
    cfg["general"]["timeframe_regime"] = values["-TFREG-"]
    cfg["general"]["dry_run"] = values["-DRY-"]
    cfg["general"]["min_notional_usdt"] = float(values["-MINNOT-"])

    cfg["risk"]["per_trade_risk_pct"] = float(values["-RTRISK-"])
    cfg["risk"]["daily_loss_stop_pct"] = float(values["-DDSTOP-"])
    cfg["risk"]["max_concurrent_positions"] = int(values["-MAXPOS-"])
    cfg["risk"]["cooldown_hours"] = int(values["-COOLD-"])

    cfg["strategy"]["adx_len"] = int(values["-ADXLEN-"])
    cfg["strategy"]["trend_adx_threshold"] = int(values["-ADXTH-"])
    cfg["strategy"]["ema_slow"] = int(values["-EMASLOW-"])
    cfg["strategy"]["ema_fast"] = int(values["-EMAFAST-"])
    cfg["strategy"]["donchian_len"] = int(values["-DONLEN-"])
    cfg["strategy"]["rsi_len"] = int(values["-RSILEN-"])
    cfg["strategy"]["rsi_overheat"] = int(values["-RSIHOT-"])
    cfg["strategy"]["atr_len"] = int(values["-ATRLEN-"])
    cfg["strategy"]["atr_sl_trend_mult"] = float(values["-ATRSLT-"])
    cfg["strategy"]["atr_trail_mult"] = float(values["-ATRTRAIL-"])
    cfg["strategy"]["bb_len"] = int(values["-BBLEN-"])
    cfg["strategy"]["bb_mult"] = float(values["-BBMUL-"])
    cfg["strategy"]["atr_sl_mr_mult"] = float(values["-ATRMR-"])
    cfg["strategy"]["rsi_mr_threshold"] = int(values["-RSIMR-"])
    cfg["strategy"]["mean_reversion_time_stop_hours"] = int(values["-MRH-"])

    cfg["alerts"]["enable_telegram"] = values["-TGON-"]
    cfg["alerts"]["telegram_bot_token"] = values["-TGTOK-"]
    cfg["alerts"]["telegram_chat_id"] = values["-TGCHAT-"]

    cfg["logging"]["csv_dir"] = values["-CSV-"]
    cfg["logging"]["state_file"] = values["-STATE-"]
    cfg["logging"]["tz"] = values["-TZ-"]

    return cfg

def main():
    cfg = load_config()
    sg.theme("SystemDefaultForReal")
    layout = layout_from_cfg(cfg)
    win = sg.Window("Binance Spot Auto-Trader — GUI", layout, finalize=True, resizable=True)

    while True:
        ev, val = win.read()
        if ev in (sg.WINDOW_CLOSED, "Exit"):
            break
        if ev == "Save Config":
            try:
                cfg = collect_cfg(val, cfg)
                save_config(cfg)
                sg.popup_ok("Config saved.", keep_on_top=True)
            except Exception as e:
                sg.popup_error(f"Save failed: {e}", keep_on_top=True)
        if ev == "Start Bot":
            try:
                start_bot(python_exec=sys.executable, cwd=os.getcwd())
                sg.popup_ok("Bot started (background).", keep_on_top=True)
            except Exception as e:
                sg.popup_error(f"Start failed: {e}", keep_on_top=True)
        if ev == "Stop Bot":
            try:
                stop_bot()
                sg.popup_ok("Bot stopped.", keep_on_top=True)
            except Exception as e:
                sg.popup_error(f"Stop failed: {e}", keep_on_top=True)

    win.close()

if __name__ == "__main__":
    main()
