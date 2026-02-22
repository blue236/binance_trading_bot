from __future__ import annotations

from typing import Any, Dict, List, Literal
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


# ---- M2 convergence: unified backtest contract ----
class BacktestMarker(BaseModel):
    ts: int
    index: int | None = None
    price: float | None = None
    side: str | None = None
    reason: str | None = None


class BacktestMetrics(BaseModel):
    roi_pct: float | None = None
    final_equity: float | None = None
    max_drawdown_pct: float | None = None
    trades: int | None = None


class BacktestSummary(BaseModel):
    symbol: str
    status: Literal["ok", "error"] = "ok"
    signal_basis: str | None = None
    source: str | None = None
    note: str | None = None


class BacktestCurve(BaseModel):
    labels: List[str] = Field(default_factory=list)
    values: List[float] = Field(default_factory=list)


class UnifiedBacktestResult(BaseModel):
    engine: Literal["quick", "legacy", "both"]
    summary: BacktestSummary
    metrics: BacktestMetrics
    trades: List[Dict[str, Any]] = Field(default_factory=list)
    equity_curve: BacktestCurve = Field(default_factory=BacktestCurve)
    markers: List[BacktestMarker] = Field(default_factory=list)
    raw_output: str | None = None


class UnifiedBacktestBundle(BaseModel):
    engine: Literal["both"] = "both"
    summary: BacktestSummary
    metrics: BacktestMetrics = Field(default_factory=BacktestMetrics)
    markers: List[BacktestMarker] = Field(default_factory=list)
    quick: UnifiedBacktestResult | None = None
    legacy: UnifiedBacktestResult | None = None
