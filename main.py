#!/usr/bin/env python3
import os, sys, time, json, math, traceback, logging
import pandas as pd
import numpy as np
import yaml
from datetime import datetime, timedelta
from dateutil import tz

import ccxt
from ta.volatility import AverageTrueRange, BollingerBands
from ta.trend import EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
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

def now_tz(tz_name):
    return datetime.now(tz.gettz(tz_name))

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def setup_logger(log_dir, name="bot.log"):
    ensure_dir(log_dir)
    logger = logging.getLogger("bot")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fpath = os.path.join(log_dir, name)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(fpath)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
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
            "adjustForTimeDifference": True,  # Binance 서버 시간에 맞춰 자동 보정
        },
    })

    # 추가: 시작 시 시간차를 여러 번 잡아 안정화
    for _ in range(3):
        # 서버와 시간 차이 미리 계산 (Optional but recommended)
        try:
            exchange.load_time_difference()
            break
        except Exception as e:
            print("Warning: load_time_difference() failed:", e)
            time.sleep(1)

    exchange.load_markets()
    return exchange

def safe_fetch_balance(exchange, retries=5):
    for i in range(retries):
        try:
            return exchange.fetch_balance()
        except ccxt.base.errors.InvalidNonce as e:
            # Binance -1021 대응
            try:
                exchange.load_time_difference()
            except Exception:
                pass
            time.sleep(1 + i)   # 점진적 backoff
    # 끝까지 실패하면 마지막 예외 다시 raise
    return exchange.fetch_balance()

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
    if len(df) < max(st["donchian_len"], st["bb_len"], st["ema_slow"]) + 2:
        return None, {}
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

def fetch_equity_usdt(exchange, base_ccy="USDT", balances=None, tickers=None):
    balances = balances or safe_fetch_balance(exchange)["total"]
    equity = balances.get(base_ccy, 0.0)
    tickers = tickers or exchange.fetch_tickers()
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
        bot, chat = tg
        try: bot.send_message(chat_id=chat, text=f"🔴 EXIT {sym} reason={reason} qty={qty} @ {price:.4f}")
        except: pass

def main():
    cfg = apply_env_overrides(load_config())
    tzname = cfg["logging"]["tz"]
    csv_dir = cfg["logging"]["csv_dir"]
    state_path = cfg["logging"]["state_file"]
    os.makedirs(csv_dir, exist_ok=True)

    logger = setup_logger(cfg["logging"]["csv_dir"])
    exchange = connect_exchange(cfg)
    tg = connect_telegram(cfg)

    state = read_state(state_path)

    session_date = daily_key(now_tz(tzname))
    base_ccy = cfg["general"]["base_currency"]
    equity_start = fetch_equity_usdt(exchange, base_ccy)
    logger.info("Bot started. dry_run=%s", cfg["general"]["dry_run"])
    if tg:
        logger.info("Telegram alerts enabled.")
        try: tg[0].send_message(chat_id=tg[1], text=f"🚀 Bot started. Equity start: {equity_start:.2f} {cfg['general']['base_currency']} (dry_run={cfg['general']['dry_run']})")
        except: pass
    else:
        logger.info("Telegram alerts not enabled or failed to connect.")

    while True:
        loop_ts = now_tz(tzname)
        try:
            # daily rollover
            dk = daily_key(loop_ts)
            # print dk, session_date
            logger.info("dk=%s session_date=%s", dk, session_date)
            if dk != session_date:
                session_date = dk
                equity_start = fetch_equity_usdt(exchange, cfg["general"]["base_currency"])
                logger.info("New session %s. Equity start %.2f %s", session_date, equity_start, base_ccy)
                if tg:
                    try: tg[0].send_message(chat_id=tg[1], text=f"📅 New session {session_date}. Equity start: {equity_start:.2f}")
                    except: pass

            symbols = cfg["general"]["symbols"]
            pos_syms = list(state["positions"].keys())
            ticker_syms = sorted(set(symbols + pos_syms))
            tickers = exchange.fetch_tickers(ticker_syms) if ticker_syms else {}
            balances = safe_fetch_balance(exchange)
            balances_total = balances.get("total", {})
            balances_free = balances.get("free", {})
            equity_now = fetch_equity_usdt(exchange, base_ccy, balances_total, tickers)
            logger.info("Tickers: %s. Equity now: %.2f %s", tickers, equity_now, base_ccy)

            # daily PnL guard
            if daily_pnl_guard(cfg, equity_now, equity_start):
                logger.warning("Daily loss stop reached. Equity %.2f vs start %.2f", equity_now, equity_start)
                if tg:
                    try: tg[0].send_message(chat_id=tg[1], text=f"⛔ Daily loss stop reached. Equity {equity_now:.2f} vs start {equity_start:.2f}. Cooling 60m.")
                    except: pass
                time.sleep(60*60)
                continue

            # entry checks
            allow_entries = len(state["positions"]) < cfg["risk"]["max_concurrent_positions"]
            free_base = balances_free.get(base_ccy, 0.0)
            for symbol in symbols:
                if not allow_entries:
                    break
                if symbol in state["positions"]:
                    continue
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

                risk_qty = position_size(equity_now, price, atr, atr_mult, cfg["risk"]["per_trade_risk_pct"])
                max_affordable = free_base / price if price > 0 else 0.0
                qty = clamp_qty(exchange, symbol, min(risk_qty, max_affordable))
                if qty > 0 and order_constraints_ok(exchange, symbol, qty, price, cfg["general"]["min_notional_usdt"]):
                    place_order(exchange, symbol, "buy", qty, price=price, dry_run=cfg["general"]["dry_run"])
                    free_base = max(0.0, free_base - (qty * price))
                    set_cooldown(state, symbol, loop_ts)
                    pos = {"symbol":symbol, "entry_time": loop_ts.isoformat(), "entry_price": price, "qty": qty, "sl": sl, "signal": signal, "regime": regime}
                    if signal == "R_LONG" and "tp_mid" in params and params["tp_mid"]:
                        pos["tp_mid"] = params["tp_mid"]
                    if signal == "T_LONG" and "trail_mult" in params:
                        pos["trail_mult"] = params["trail_mult"]; pos["trail_ref"] = price
                    state["positions"][symbol] = pos
                    write_state(state_path, state)
                    allow_entries = len(state["positions"]) < cfg["risk"]["max_concurrent_positions"]
                    log_csv(csv_dir, "trades", {
                        "ts": loop_ts.isoformat(), "event":"ENTER", "symbol":symbol, "side":"LONG",
                        "price": price, "qty": qty, "sl": sl, "signal": signal, "regime": regime, "equity": equity_now, "adx_d": adx_val
                    }, fieldnames=TRADE_FIELDS)
                    logger.info("ENTER %s %s qty=%s price=%.4f sl=%.4f regime=%s", symbol, signal, qty, price, sl, regime)
                    if tg:
                        try: tg[0].send_message(chat_id=tg[1], text=f"🟢 ENTER {symbol} {signal} qty={qty} @ {price:.4f} SL={sl:.4f} regime={regime}")
                        except: pass

            # exits
            positions = dict(state["positions"])
            for sym, pos in positions.items():
                last = get_last_price(tickers, sym)
                if last is None:
                    last = exchange.fetch_ticker(sym)["last"]
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
                        logger.info("EXIT %s reason=TIME_STOP qty=%s price=%.4f", sym, qty, last)
                        finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "TIME_STOP", last, qty)
                        continue
                    if "tp_mid" in pos and last >= pos["tp_mid"]:
                        sell_qty = clamp_qty(exchange, sym, qty * 0.5)
                        if order_constraints_ok(exchange, sym, sell_qty, last, cfg["general"]["min_notional_usdt"]):
                            place_order(exchange, sym, "sell", sell_qty, price=last, dry_run=cfg["general"]["dry_run"])
                            pos["qty"] = float(qty - sell_qty); del pos["tp_mid"]; changed = True
                            logger.info("PARTIAL_TP %s qty=%s price=%.4f note=mid-band", sym, sell_qty, last)
                            log_csv(csv_dir, "trades", {"ts": loop_ts.isoformat(), "event": "PARTIAL_TP", "symbol": sym, "side": "SELL", "price": last, "qty": sell_qty, "note": "mid-band"}, fieldnames=TRADE_FIELDS)

                # stop-loss
                if last <= sl:
                    place_order(exchange, sym, "sell", pos["qty"], price=last, dry_run=cfg["general"]["dry_run"])
                    logger.info("EXIT %s reason=STOP qty=%s price=%.4f", sym, pos["qty"], last)
                    finalize_exit(cfg, state, state_path, csv_dir, tg, sym, "STOP", last, pos["qty"])
                    continue

                if changed:
                    state["positions"][sym] = pos
                    write_state(state_path, state)

            # equity snapshot
            equity_now = fetch_equity_usdt(exchange, base_ccy, balances_total, tickers)
            log_csv(csv_dir, "equity", {"ts": loop_ts.isoformat(), "equity": equity_now}, fieldnames=EQUITY_FIELDS)
            logger.info("Equity snapshot: %.4f and sleep 30 seconds", equity_now)
            time.sleep(30)
        except Exception as e:
            logger.exception("Loop error: %s", e)
            time.sleep(10)

if __name__ == "__main__":
    main()
