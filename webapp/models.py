from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field


class UIConfig(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    timeframe: str = "1d"
    history_limit: int = 180
    refresh_cron: str = "0 6 * * *"  # daily UTC
    quote_currency: str = "USDT"
    starting_capital: float = 10000.0
    fee_rate: float = 0.001


class RefreshRequest(BaseModel):
    symbols: List[str] | None = None


class BacktestRequest(BaseModel):
    symbol: str
    fast_window: int = 20
    slow_window: int = 60
    starting_capital: float | None = None
    fee_rate: float | None = None
