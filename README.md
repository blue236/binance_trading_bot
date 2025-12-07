# Binance Spot Auto-Trading Bot (with GUI Config)

**Features**
- Spot-only (no futures), Binance via `ccxt`
- Regime-aware strategy (Trend/Range) with ATR-based risk
- GUI (`gui.py`) to edit `config.yaml`, test Telegram, and **Start/Stop** the bot
- CSV logging to `./logs`, optional Telegram alerts

## Quick Start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python gui.py
```
1) In the GUI, fill API keys (withdrawal OFF, IP whitelist recommended), set parameters.
2) Click **Save Config** then **Start Bot** (starts `main.py` as a background process).
3) Start with **Dry-Run** ON. After several days, switch to live.

## Notes
- Requires Python 3.9+
- On servers without display, use `xvfb` or edit `config.yaml` manually and run `python main.py`.
- See comments in `config.yaml` for all parameters.
