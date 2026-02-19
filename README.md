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
