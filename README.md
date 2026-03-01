# Binance Trading Bot — Web Architecture Redesign

This branch introduces a new modular **web-first architecture** to make feature extension easier.

## New architecture (`webapp/`)

- `webapp/app.py` — FastAPI server (UI + APIs)
- `webapp/models.py` — typed request/config models
- `webapp/config_manager.py` — load/save YAML config
- `webapp/storage.py` — SQLite cache for OHLCV + metadata
- `webapp/chart_service.py` — Binance OHLCV fetch + daily/manual refresh
- `webapp/backtest_service.py` — ROI simulator (SMA crossover)
- `webapp/templates/index.html` — main dashboard UI

## Implemented requested features

1. **Web based UI server**
   - FastAPI + Jinja UI
2. **Main page coin price chart**
   - Select symbol chart on main page
   - Daily refresh via scheduler (`refresh_cron`)
   - Manual **Refresh Charts** button
3. **Config button**
   - Load config to panel
   - Save edited options to YAML config file
4. **Backtester button**
   - Run ROI simulation from UI
   - Shows ROI, drawdown, trades, equity curve data in result panel

## Chart UX v1 (Investing-style practical bundle)

- Timeframe quick switch: `15m / 1h / 4h / 1d`
- Mouse interactions: wheel zoom + `Shift+Drag` pan on price/backtest charts
- Rich hover context: timestamp, close/high/low, volume, and signal reason
- Trading overlays: EMA fast/slow (from config), optional Bollinger Bands
- Optional volume bars overlay (right axis)
- Toolbar actions: reset zoom, download chart PNG, toggle markers/EMA/BB/volume
- Fullscreen compatibility retained for both price/backtest charts

## Run

```bash
chmod +x run_web_ui.sh
./run_web_ui.sh
```

Default URL: `http://127.0.0.1:8080`

## Config file

`web_config.yaml` is auto-created on first run.

Core fields:
- `symbols`
- `timeframe`
- `history_limit`
- `refresh_cron` (daily scheduler)
- `starting_capital`
- `fee_rate`

## API quick reference

- `GET /api/config`
- `POST /api/config/load`
- `POST /api/config/save`
- `GET /api/charts?symbol=BTC/USDT`
- `POST /api/charts/refresh`
- `POST /api/backtester/run`
- `GET /api/health`

## Credential security

Secrets are no longer stored in plaintext config files.

- `config.yaml` editor masks and strips secret fields on save.
- Web secrets manager stores encrypted credentials in `.credentials.enc.json`.
- Set `BTB_CREDENTIALS_PASSPHRASE` before saving secrets.

Example:

```bash
export BTB_CREDENTIALS_PASSPHRASE='use-a-long-random-passphrase'
./run_web_ui.sh
```

Environment variables (`BINANCE_API_KEY`, `BINANCE_API_SECRET`, etc.) still override stored values.

## Operational docs

- QA release gate checklist: `QA_RELEASE_GATE_CHECKLIST.md`
- Security cookie policy: `SECURITY_COOKIE_POLICY.md`
- Mobile web QA report: `MOBILE_WEB_QA_REPORT.md`
- M5 trading readiness report: `M5_TRADING_READINESS_REPORT_2026-02-22.md`
- M6 controlled launch report: `M6_CONTROLLED_LAUNCH_REPORT_2026-02-22.md`
- Controlled launch policy: `LAUNCH_POLICY.yaml`

## Gate scripts

```bash
# baseline + risk gate
./scripts/release_gate.sh --with-risk

# M5 readiness gate
./scripts/m5_trading_readiness_gate.sh

# M6 controlled launch gate
./scripts/m6_controlled_launch_gate.sh
```
