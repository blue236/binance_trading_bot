# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the web UI server
```bash
./run_web_ui.sh
# or manually:
source .venv/bin/activate
uvicorn webapp.app:app --host 127.0.0.1 --port 8080
```
Default URL: `http://127.0.0.1:8080`

### Run the legacy CLI bot
```bash
source .venv/bin/activate
python main.py
```

### Run tests
```bash
source .venv/bin/activate
pytest tests/
# single test file:
pytest tests/test_hv5_strategy_adoption.py -v
# single test:
pytest tests/test_hv5_strategy_adoption.py::TestHV5StrategyAdoption::test_h1_signals_v5_breakout_triggers_t_long -v
```

### Run the standalone backtester
```bash
source .venv/bin/activate
python backtester.py --symbols BTC/USDT ETH/USDT --timeframe 1d --limit 365
```

### Gate / release scripts
```bash
./scripts/release_gate.sh --with-risk
./scripts/m5_trading_readiness_gate.sh
./scripts/m6_controlled_launch_gate.sh
```

### Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Architecture

The project has two modes:

### 1. Web UI mode (`webapp/`)
FastAPI server that provides a browser dashboard for chart viewing, configuration editing, and backtesting. Launched via `run_web_ui.sh` or `uvicorn webapp.app:app`.

- **`webapp/app.py`** — FastAPI app, all routes, session auth middleware, APScheduler cron for chart refresh, AI bot subprocess control (start/stop via PID file `.web_ai_bot.pid`). The "AI bot" it controls is `main.py` launched as a subprocess.
- **`webapp/models.py`** — Pydantic models: `UIConfig`, `BacktestRequest`, `UnifiedBacktestResult`, `UnifiedBacktestBundle`.
- **`webapp/config_manager.py`** — Loads/saves `web_config.yaml` (the web UI's own config, separate from `config.yaml`).
- **`webapp/storage.py`** — SQLite (`webapp_state.sqlite`) for OHLCV cache and key-value metadata. Two tables: `ohlcv` and `meta`.
- **`webapp/chart_service.py`** — Fetches OHLCV from Binance via ccxt and writes to `storage`.
- **`webapp/backtest_service.py`** — In-process SMA crossover backtester using the OHLCV from `storage`. Returns `UnifiedBacktestResult`.

The web UI can also proxy to a "legacy backtester" CLI (`backtester.py` via `web_backtester_ui.py`) for the "both engines" comparison mode.

### 2. CLI bot mode (`main.py`)
Single-file trading bot (~2000 lines) containing all strategy logic, position management, Telegram handling, and the main trading loop. Reads `config.yaml` for strategy parameters.

**Strategy: H_V5 breakeven + EMA100** (`strategy.mode: h_v5_b_plus_breakeven_ema100`)
- Uses two timeframes: `timeframe_signal` (e.g. 1h) for entries and `timeframe_regime` (1d) for regime filtering.
- Regime filter: EMA fast on 1d, RSI threshold, Donchian channel.
- Entry: breakout via Bollinger Bands + RSI overheat guard + ADX trend filter + pullback EMA band.
- Exit: ATR-based trailing stop, breakeven move at `breakeven_r` × ATR, optional structural EMA100 exit on daily TF.
- `aggressive_mode` in dry_run overrides parameters from the `aggressive:` config block.

### Shared modules
- **`credentials.py`** — Encrypted credential storage (`.credentials.enc.json`) using Fernet/PBKDF2. Falls back to env vars (`BINANCE_API_KEY`, `BINANCE_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Passphrase via `BTB_CREDENTIALS_PASSPHRASE`.
- **`telegram_shared.py`** — `build_summary_text()` helper and `HELP_TEXT` constant shared between `main.py` and `webapp/app.py`.

## Configuration

**Two separate config files:**
- `config.yaml` — main bot config (strategy params, risk, credentials). Template at `config.template.yaml`. Reference at `CONFIG_REFERENCE.md`.
- `web_config.yaml` — web UI config (symbols, timeframe, chart refresh cron, backtest defaults). Auto-created on first run.

**Environment variable overrides** (highest priority):
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `BTB_WEB_HOST`, `BTB_WEB_PORT` (default: 127.0.0.1:8080)
- `BTB_WEB_CONFIG` (path to web config YAML)
- `BTB_WEB_DB` (path to SQLite DB)
- `BTB_WEB_AUTH_ENABLED`, `BTB_WEB_USERNAME`, `BTB_WEB_PASSWORD`, `BTB_WEB_SESSION_SECRET`
- `BTB_WEB_SESSION_TTL_HOURS` (default: 8) — session token lifetime; tokens include a timestamp and nonce signed with HMAC-SHA256
- `BTB_CREDENTIALS_PASSPHRASE` (for encrypted credential store)

A `.env` file in the project root is automatically sourced by `run_web_ui.sh`.

**Security requirements:**
- `BTB_WEB_PASSWORD` **must** be set before starting the web server when auth is enabled. The server will refuse to start with a clear error if it is missing. Set `BTB_WEB_AUTH_ENABLED=0` only for fully private local-only deployments.
- `BTB_CREDENTIALS_PASSPHRASE` **must** be set if `.credentials.enc.json` exists. The bot will raise `RuntimeError` at startup rather than silently using empty API keys.

## Key design constraints

- **`main.py` is intentionally monolithic** — all bot logic lives in one file. Strategy functions are tested by importing `main` directly (see `tests/test_hv5_strategy_adoption.py`).
- **Two config systems coexist** — `config.yaml` (bot) and `web_config.yaml` (UI) are independent; changes to one do not affect the other. The web UI edits `config.yaml` only through the AI Config panel.
- **No lookahead in strategy signals** — indicators must be computed only on candles up to and including the current one. Structural exit logic explicitly drops the current forming candle (`iloc[:-1]`) before EMA calculation. See `reviews/REV-02_REV-04_report.md` for the confirmed audit result.
- **Dry-run is the default** — `general.dry_run: true` in `config.yaml`. Live trading requires explicit opt-out.
- **Config is validated at startup** — `validate_config()` in `main.py` is called inside `load_config()`. It raises `ValueError` on misconfigured risk values (e.g. negative `daily_loss_stop_pct`) before the trading loop starts.
- **`config.yaml` saves are atomic** — the web UI writes to `config.yaml.tmp` then `os.replace()`s it, so the file is never partially written. Secrets are blanked in memory before the file is ever touched.

## Test suite

```bash
source .venv/bin/activate
pytest tests/ -v          # 35 tests
pytest tests/ --cov=. --cov-report=term-missing -q   # coverage baseline: 35%
```

Coverage baseline (2026-04-16): **35% overall** across 3,610 statements. See `coverage_report.txt` for the full breakdown. Main gaps: `backtester.py` (0%), `main.py` trading loop (17%), `webapp/backtest_service.py` (15%).
