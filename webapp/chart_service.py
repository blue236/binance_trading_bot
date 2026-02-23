from __future__ import annotations

import datetime as dt
import ccxt

from .storage import Storage
from .models import UIConfig


class ChartService:
    def __init__(self, storage: Storage):
        self.storage = storage
        # Set explicit HTTP timeout so UI actions do not hang indefinitely on slow exchange responses.
        self.exchange = ccxt.binance({"enableRateLimit": True, "timeout": 15000})

    def refresh_symbol(self, symbol: str, timeframe: str, limit: int):
        data = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        rows = [(int(ts), float(o), float(h), float(l), float(c), float(v)) for ts, o, h, l, c, v in data]
        self.storage.upsert_ohlcv(symbol, timeframe, rows)

    def refresh_all(self, cfg: UIConfig, symbols: list[str] | None = None):
        targets = symbols or cfg.symbols
        for s in targets:
            self.refresh_symbol(s, cfg.timeframe, cfg.history_limit)
        self.storage.set_meta("last_chart_refresh", dt.datetime.utcnow().isoformat(timespec="seconds"))

    def series(self, symbol: str, timeframe: str, limit: int = 500):
        rows = self.storage.fetch_ohlcv(symbol, timeframe, limit)
        labels = [dt.datetime.utcfromtimestamp(ts/1000).strftime("%Y-%m-%d") for ts, *_ in rows]
        closes = [float(c) for _, _, _, _, c, _ in rows]
        return {"symbol": symbol, "timeframe": timeframe, "labels": labels, "values": closes}
