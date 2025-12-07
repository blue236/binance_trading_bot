#!/usr/bin/env python3
import os, sys, time, json, math, traceback
import pandas as pd
import numpy as np
import yaml
from datetime import datetime, timedelta
from dateutil import tz

import ccxt
from ta.volatility import AverageTrueRange, BollingerBands
from ta.trend import EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator

# Optional Telegram
try:
    from telegram import Bot
except Exception:
    Bot = None

def load_config(path="config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def now_tz(tz_name):
    return datetime.now(tz.gettz(tz_name))

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

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

def log_csv(csv_dir, name, row):
    ensure_dir(csv_dir)
    fpath = os.path.join(csv_dir, f"{name}.csv")
    exists = os.path.exists(fpath)
    import csv
    with open(fpath, "a", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)

def connect_exchange(cfg):
    ex_id = cfg["general"]["exchange"]
    creds = cfg["credentials"]
    exchange_class = getattr(ccxt, ex_id)
    exchange = exchange_class({
        "apiKey": creds["api_key"],
        "secret": creds["api_secret"],
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    exchange.load_markets()
    return exchange

def connect_telegram(cfg):
    if not cfg["alerts"]["enable_telegram"] or Bot is None:
        return None
    try:
        bot = Bot(token=cfg["alerts"]["telegram_bot_token"])
        return (bot, cfg["alerts"]["telegram_chat_id"])
    except Exception as e:
        print("Telegram init failed:", e, file=sys.stderr)
        return None

def send_telegram(tg, text):
    if tg is None:
        return
    bot, chat_id = tg
    try:
        bot.send_message(chat_id=chat_id, text=text[:4000])
    except Exception as e:
        print("Telegram send failed:", e, file=sys.stderr)

def fetch_ohlc(exchange, symbol, timeframe, limit=500):
    o = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(o, columns=["ts","o","h","l","c","v"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df

def regime_filter(exchange, symbol, cfg):
    tf = cfg["general"]["timeframe_regime"]
    d = fetch_ohlc(exchange, symbol, tf, 400)
    ema200 = EMAIndicator(d["c"], cfg["strategy"]["ema_slow"]).ema_indicator()
    slope_pos = (ema200.diff().iloc[-1] > 0)
    adx_val = ADXIndicator(d["h"], d["l"], d["c"], cfg["strategy"]["adx_len"]).adx().iloc[-1]
    is_trend = slope_pos and adx_val > cfg["strategy"]["trend_adx_threshold"]
    is_range = adx_val <= cfg["strategy"]["trend_adx_threshold"]
    return ("trend" if is_trend else ("range" if is_range else "none")), float(adx_val)

def h1_signals(df, cfg, regime):
    st = cfg["strategy"]
    emaF = EMAIndicator(df["c"], st["ema_fast"]).ema_indicator()
    emaS = EMAIndicator(df["c"], st["ema_slow"]).ema_indicator()
    atr  = AverageTrueRange(df["h"], df["l"], df["c"], st["atr_len"]).average_true_range()
    rsi  = RSIIndicator(df["c"], st["rsi_len"]).rsi()
    don_hi = df["h"].rolling(st["donchian_len"]).max()
    bb = BollingerBands(df["c"], st["bb_len"], st["bb_mult"])
    lower = bb.bollinger_lband()
    mid   = bb.bollinger_mavg()

    close = float(df["c"].iloc[-1])
    atr_v = float(atr.iloc[-1])
    emaFv, emaSv = float(emaF.iloc[-1]), float(emaS.iloc[-1])
    rsiv = float(rsi.iloc[-1])
    don_hi_prev = float(don_hi.iloc[-2]) if not math.isnan(don_hi.iloc[-2]) else None
    lower_v = float(lower.iloc[-1]) if not math.isnan(lower.iloc[-1]) else None
    mid_v   = float(mid.iloc[-1]) if not math.isnan(mid.iloc[-1]) else None

    signal = None
    params = {}

    if regime == "trend":
        cond = (don_hi_prev is not None) and (close > don_hi_prev) and (emaFv > emaSv) and (rsiv < st["rsi_overheat"])
        if cond:
            signal = "T_LONG"
            params["sl"] = close - st["atr_sl_trend_mult"] * atr_v
            params["trail_mult"] = st["atr_trail_mult"]
    elif regime == "range":
        cond = (lower_v is not None) and (close < lower_v) and (rsiv <= st["rsi_mr_threshold"])
        if cond:
            signal = "R_LONG"
            params["sl"] = close - st["atr_sl_mr_mult"] * atr_v
            params["tp_mid"] = mid_v

    params["atr"] = atr_v
    params["close"] = close
    return signal, params

def fetch_equity_usdt(exchange, base_ccy="USDT"):
    balances = exchange.fetch_balance()["total"]
    equity = balances.get(base_ccy, 0.0)
    tickers = exchange.fetch_tickers()
    for asset, amt in balances.items():
        if asset in [base_ccy, None] or not amt:
            continue
        sym = f"{asset}/{base_ccy}"
        if sym in exchange.markets and sym in tickers and "last" in tickers[sym]:
            equity += amt * tickers[sym]["last"]
    return float(equity)

def position_size(equity_usdt, price, atr, atr_mult, risk_pct):
    risk_usdt = equity_usdt * (risk_pct / 100.0)
    stop_dist = atr_mult * atr
    if stop_dist <= 0:
        return 0.0
    qty = max(risk_usdt / stop_dist, 0.0)
    return float(round(qty, 6))

def notional_ok(qty, price, min_notional):
    return (qty * price) >= min_notional

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
        "reason": reason,
        "price": price,
        "qty": qty
    }
    log_csv(csv_dir, "trades", row)
    if sym in state["positions"]:
        del state["positions"][sym]
    write_state(state_path, state)
    if tg: 
        bot, chat = tg
        try: bot.send_message(chat_id=chat, text=f"🔴 EXIT {sym} reason={reason} qty={qty} @ {price:.4f}")
        except: pass

def main():
    cfg = load_config()
    tzname = cfg["logging"]["tz"]
    csv_dir = cfg["logging"]["csv_dir"]
    state_path = cfg["logging"]["state_file"]
    os.makedirs(csv_dir, exist_ok=True)

    exchange = connect_exchange(cfg)
    tg = connect_telegram(cfg)

    state = read_state(state_path)

    session_date = daily_key(now_tz(tzname))
    equity_start = fetch_equity_usdt(exchange, cfg["general"]["base_currency"])
    if tg:
        try: tg[0].send_message(chat_id=tg[1], text=f"🚀 Bot started. Equity start: {equity_start:.2f} {cfg['general']['base_currency']} (dry_run={cfg['general']['dry_run']})")
        except: pass

    while True:
        loop_ts = now_tz(tzname)
        try:
            # daily rollover
            dk = daily_key(loop_ts)
            if dk != session_date:
                session_date = dk
                equity_start = fetch_equity_usdt(exchange, cfg["general"]["base_currency"])
                if tg:
                    try: tg[0].send_message(chat_id=tg[1], text=f"📅 New session {session_date}. Equity start: {equity_start:.2f}")
                    except: pass

            equity_now = fetch_equity_usdt(exchange, cfg["general"]["base_currency"])
            if daily_pnl_guard(cfg, equity_now, equity_start):
                if tg:
                    try: tg[0].send_message(chat_id=tg[1], text=f"⛔ Daily loss stop reached. Equity {equity_now:.2f} vs start {equity_start:.2f}. Cooling 60m.")
                    except: pass
                time.sleep(60*60)
                continue

            # entry checks
            for symbol in cfg["general"]["symbols"]:
                if is_cooldown(state, symbol, cfg, loop_ts):
                    continue
                regime, adx_val = regime_filter(exchange, symbol, cfg)
                if regime == "none":
                    continue
                df = fetch_ohlc(exchange, symbol, cfg["general"]["timeframe_signal"], 300)
                signal, params = h1_signals(df, cfg, regime)
                if not signal:
                    continue

                price = params["close"]
                atr = params["atr"]
                if signal == "T_LONG":
                    sl = params["sl"]; atr_mult = cfg["strategy"]["atr_sl_trend_mult"]
                else:
                    sl = params["sl"]; atr_mult = cfg["strategy"]["atr_sl_mr_mult"]

                qty = position_size(equity_now, price, atr, atr_mult, cfg["risk"]["per_trade_risk_pct"])
                if notional_ok(qty, price, cfg["general"]["min_notional_usdt"]):
                    place_order(exchange, symbol, "buy", qty, price=price, dry_run=cfg["general"]["dry_run"])
                    state["cooldowns"][symbol] = loop_ts.isoformat()
                    pos = {"symbol":symbol, "entry_time": loop_ts.isoformat(), "entry_price": price, "qty": qty, "sl": sl, "signal": signal, "regime": regime}
                    if signal == "R_LONG" and "tp_mid" in params and params["tp_mid"]:
                        pos["tp_mid"] = params["tp_mid"]
                    if signal == "T_LONG" and "trail_mult" in params:
                        pos["trail_mult"] = params["trail_mult"]; pos["trail_ref"] = price
                    state["positions"][symbol] = pos
                    write_state(state_path, state)
                    log_csv(csv_dir, "trades", {
                        "ts": loop_ts.isoformat(), "event":"ENTER", "symbol":symbol, "side":"LONG",
                        "price": price, "qty": qty, "sl": sl, "signal": signal, "regime": regime, "equity": equity_now, "adx_d": adx_val
                    })
                    if tg:
                        try: tg[0].send_message(chat_id=tg[1], text=f"🟢 ENTER {symbol} {signal} qty={qty} @ {price:.4f} SL={sl:.4f} regime={regime}")
                        except: pass

            # exits
            positions = dict(state["positions"])
            for sym, pos in positions.items():
                ticker = exchange.fetch_ticker(sym)
                last = ticker["last"]
                qty = pos["qty"]
                sl = pos["sl"]
                changed = False

                # trend trailing
                if pos["signal"] == "T_LONG" and "trail_mult" in pos:
                    df = fetch_ohlc(exchange, sym, cfg["general"]["timeframe_signal"], 200)
                    atr = AverageTrueRange(df["h"], df["l"], df["c"], cfg["strategy"]["atr_len"]).average_true_range().iloc[-1]
                    hi_since = df["c"].iloc[-200:].max()
                    trail = hi_since - pos["trail_mult"] * atr
                    if trail > sl:
                        sl = trail; pos["sl"] = float(sl); changed = True

                # MR time stop or mid-band partial
                if pos["signal"] == "R_LONG":
                    et = datetime.fromisoformat(pos["entry_time"])
                    if loop_ts - et >= timedelta(hours=cfg["strategy"]["mean_reversion_time_stop_hours"]):
                        place_order(exchange, sym, "sell", qty, price=last, dry_run=cfg["general"]["dry_run"])
                        finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "TIME_STOP", last, qty)
                        continue
                    if "tp_mid" in pos and last >= pos["tp_mid"]:
                        sell_qty = qty * 0.5
                        place_order(exchange, sym, "sell", sell_qty, price=last, dry_run=cfg["general"]["dry_run"])
                        pos["qty"] = float(qty - sell_qty); del pos["tp_mid"]; changed = True
                        log_csv(csv_dir, "trades", {"ts": loop_ts.isoformat(), "event": "PARTIAL_TP", "symbol": sym, "price": last, "qty": sell_qty, "note": "mid-band"})

                # stop-loss
                if last <= sl:
                    place_order(exchange, sym, "sell", pos["qty"], price=last, dry_run=cfg["general"]["dry_run"])
                    finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "STOP", last, pos["qty"])
                    continue

                if changed:
                    state["positions"][sym] = pos
                    write_state(state_path, state)

            # equity snapshot
            equity_now = fetch_equity_usdt(exchange, cfg["general"]["base_currency"])
            log_csv(csv_dir, "equity", {"ts": loop_ts.isoformat(), "equity": equity_now})
            time.sleep(30)
        except Exception as e:
            print("Loop error:", e, file=sys.stderr)
            traceback.print_exc()
            time.sleep(10)

if __name__ == "__main__":
    main()
