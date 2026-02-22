from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  PRIMARY KEY(symbol, timeframe, ts)
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str = "webapp_state.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.executescript(SCHEMA)

    def _conn(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def upsert_ohlcv(self, symbol: str, timeframe: str, rows: Iterable[tuple[int, float, float, float, float, float]]):
        with self._conn() as con:
            con.executemany(
                """
                INSERT INTO ohlcv(symbol,timeframe,ts,open,high,low,close,volume)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,timeframe,ts) DO UPDATE SET
                  open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume
                """,
                [(symbol, timeframe, *r) for r in rows],
            )

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500):
        with self._conn() as con:
            cur = con.execute(
                """
                SELECT ts,open,high,low,close,volume FROM (
                  SELECT ts,open,high,low,close,volume FROM ohlcv
                  WHERE symbol=? AND timeframe=?
                  ORDER BY ts DESC LIMIT ?
                ) t ORDER BY ts ASC
                """,
                (symbol, timeframe, limit),
            )
            return [tuple(r) for r in cur.fetchall()]

    def set_meta(self, key: str, value: str):
        with self._conn() as con:
            con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key: str):
        with self._conn() as con:
            r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return r["value"] if r else None
