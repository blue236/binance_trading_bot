from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Body, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import UIConfig, RefreshRequest, BacktestRequest
from .config_manager import ConfigManager
from .storage import Storage
from .chart_service import ChartService
from .backtest_service import BacktestService

BASE = Path(__file__).resolve().parent
(BASE / "static").mkdir(parents=True, exist_ok=True)

config_mgr = ConfigManager(os.environ.get("BTB_WEB_CONFIG", "web_config.yaml"))
storage = Storage(os.environ.get("BTB_WEB_DB", "webapp_state.sqlite"))
chart_service = ChartService(storage)
backtest_service = BacktestService(storage)

app = FastAPI(title="Binance Trading Bot Web UI", version="2.0")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

scheduler: BackgroundScheduler | None = None


def parse_cron(expr: str):
    m, h, dom, mon, dow = expr.split()
    return dict(minute=m, hour=h, day=dom, month=mon, day_of_week=dow)


@app.on_event("startup")
def startup():
    global scheduler
    cfg = config_mgr.load()
    scheduler = BackgroundScheduler()
    try:
        scheduler.add_job(lambda: chart_service.refresh_all(cfg), "cron", **parse_cron(cfg.refresh_cron), id="daily_chart_refresh", replace_existing=True)
    except Exception:
        pass
    scheduler.start()


@app.on_event("shutdown")
def shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None


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
            "default_symbol": symbol,
            "series": series,
            "last_refresh": storage.get_meta("last_chart_refresh") or "(never)",
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


@app.get("/api/health")
def health():
    return {"ok": True}
