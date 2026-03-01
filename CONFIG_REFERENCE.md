# `config.yaml` Reference

This document explains the meaning of each value currently present in [`config.yaml`](/home/blue236/.openclaw/workspace/binance_trading_bot/config.yaml).

## How config is applied

Runtime load order in [`main.py`](/home/blue236/.openclaw/workspace/binance_trading_bot/main.py):

1. `config.yaml` is loaded.
2. Missing secrets can be filled from the encrypted credentials store via `credentials.py`.
3. Environment variables override file values:
   - `BINANCE_API_KEY`
   - `BINANCE_API_SECRET`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. If both `general.dry_run: true` and `general.aggressive_mode: true`, the `aggressive` section is deep-merged into the main config.

Important:

- Aggressive overrides are only applied in `dry_run` mode.
- `config.yaml` is the trading bot config. `web_config.yaml` is a separate web UI config.

## `general`

### `general.symbols`

List of spot markets the bot scans for entries and manages for exits.

Current values:

- `BTC/USDC`
- `ETH/USDC`
- `SOL/USDC`

### `general.timeframe_signal`

Candlestick timeframe used for entry and exit signal generation.

Current value:

- `4h`

### `general.timeframe_regime`

Higher timeframe used to classify the market as trend, range, or none.

Current value:

- `1d`

### `general.exchange`

CCXT exchange id used by `connect_exchange()`.

Current value:

- `binance`

### `general.dry_run`

If `true`, orders are simulated and not sent to Binance. The strategy loop, logging, alerts, and state updates still run.

Current value:

- `true`

### `general.aggressive_mode`

Enables the `aggressive` override set, but only when `dry_run` is also `true`.

Current value:

- `false`

### `general.base_currency`

Quote/base accounting currency used for equity tracking, free balance checks, and session PnL reporting.

Current value:

- `USDC`

### `general.min_notional_usdc`

Minimum order value required before entering or partially exiting a position.

The runtime currently checks this exact key name in [`main.py`](/home/blue236/.openclaw/workspace/binance_trading_bot/main.py). Because your markets are `*/USDC`, this naming matches current runtime behavior.

Current value:

- `10.0`

Note:

- [`config.template.yaml`](/home/blue236/.openclaw/workspace/binance_trading_bot/config.template.yaml) still uses `min_notional_usdt`.
- [`gui.py`](/home/blue236/.openclaw/workspace/binance_trading_bot/gui.py) also reads and writes `min_notional_usdt`.
- If you use the GUI, this field can drift from what the runtime actually uses.

## `risk`

### `risk.per_trade_risk_pct`

Percent of total equity risked per new trade when position size is calculated from ATR stop distance.

Current value:

- `0.5`

Example meaning:

- `0.5` means target risk is 0.5% of equity per entry.

### `risk.daily_loss_stop_pct`

Maximum allowed session drawdown before the bot pauses trading for one hour.

Current value:

- `2.0`

Example meaning:

- If session equity falls 2% or more below the session start equity, the daily guard triggers.

### `risk.max_concurrent_positions`

Maximum number of open positions allowed at the same time.

Current value:

- `2`

### `risk.cooldown_hours`

Minimum wait time after trading a symbol before the bot can enter that symbol again.

Current value:

- `8`

## `strategy`

These values drive the signal engine in [`main.py`](/home/blue236/.openclaw/workspace/binance_trading_bot/main.py).

### `strategy.adx_len`

ADX lookback period.

Current value:

- `14`

### `strategy.trend_adx_threshold`

ADX threshold used by the regime filter.

Meaning:

- Above this threshold with positive slow-EMA slope: market is treated as `trend`.
- At or below this threshold: market is treated as `range`.

Current value:

- `20`

### `strategy.ema_slow`

Slow EMA period used in both regime filtering and trend-entry confirmation.

Current value:

- `1`

Important:

- A value of `1` effectively makes the slow EMA almost equal to price, which is much more reactive than the template default.

### `strategy.donchian_len`

Lookback window for Donchian breakout highs in trend entries.

Current value:

- `20`

### `strategy.ema_fast`

Fast EMA period used for trend-entry confirmation against `ema_slow`.

Current value:

- `5`

### `strategy.rsi_len`

RSI lookback period.

Current value:

- `14`

### `strategy.rsi_overheat`

Maximum RSI allowed for trend-following entries.

Meaning:

- Trend entry is skipped if RSI is already above this level.

Current value:

- `80`

### `strategy.atr_len`

ATR lookback period used for stop-loss sizing and trailing calculations.

Current value:

- `14`

### `strategy.atr_sl_trend_mult`

ATR multiplier used to place the initial stop-loss for trend trades.

Current value:

- `1.8`

### `strategy.atr_trail_mult`

ATR multiplier used for trailing stops on trend trades after entry.

Current value:

- `3.0`

### `strategy.bb_len`

Bollinger Band lookback period used for range/mean-reversion logic.

Current value:

- `20`

### `strategy.bb_mult`

Bollinger Band standard deviation multiplier.

Current value:

- `2.0`

### `strategy.atr_sl_mr_mult`

ATR multiplier used to place the stop-loss for range or mean-reversion trades.

Current value:

- `1.2`

### `strategy.rsi_mr_threshold`

RSI threshold for range/mean-reversion long entries.

Meaning:

- Lower RSI is required before buying perceived weakness in range conditions.

Current value:

- `35`

### `strategy.mean_reversion_time_stop_hours`

Maximum holding time for a mean-reversion position before the bot exits it.

Current value:

- `24`

### `strategy.loop_sleep_seconds`

Delay between main trading loop iterations.

Current value:

- `60`

## `aggressive`

This section contains override values that are merged into the main config when:

- `general.dry_run` is `true`
- `general.aggressive_mode` is `true`

The idea is to paper-trade a more aggressive profile without affecting the default config.

### `aggressive.general.timeframe_signal`

Override signal timeframe in aggressive dry-run mode.

Current value:

- `1h`

### `aggressive.general.min_notional_usdc`

Override minimum order notional in aggressive dry-run mode.

Current value:

- `10.0`

### `aggressive.risk.per_trade_risk_pct`

Override per-trade risk in aggressive dry-run mode.

Current value:

- `0.9`

### `aggressive.risk.daily_loss_stop_pct`

Override daily loss stop in aggressive dry-run mode.

Current value:

- `4.0`

### `aggressive.risk.max_concurrent_positions`

Override maximum simultaneous positions in aggressive dry-run mode.

Current value:

- `3`

### `aggressive.risk.cooldown_hours`

Override symbol cooldown in aggressive dry-run mode.

Current value:

- `1`

### `aggressive.strategy.adx_len`

Aggressive-mode ADX lookback override.

Current value:

- `14`

### `aggressive.strategy.trend_adx_threshold`

Aggressive-mode regime threshold override.

Current value:

- `18`

### `aggressive.strategy.ema_fast`

Aggressive-mode fast EMA override.

Current value:

- `8`

### `aggressive.strategy.ema_slow`

Aggressive-mode slow EMA override.

Current value:

- `6`

### `aggressive.strategy.donchian_len`

Aggressive-mode Donchian window override.

Current value:

- `20`

### `aggressive.strategy.rsi_overheat`

Aggressive-mode max RSI for trend entries.

Current value:

- `85`

### `aggressive.strategy.atr_sl_trend_mult`

Aggressive-mode trend stop ATR multiplier.

Current value:

- `1.6`

### `aggressive.strategy.atr_trail_mult`

Aggressive-mode trend trailing ATR multiplier.

Current value:

- `3.5`

### `aggressive.strategy.bb_len`

Aggressive-mode Bollinger lookback.

Current value:

- `20`

### `aggressive.strategy.bb_mult`

Aggressive-mode Bollinger deviation multiplier.

Current value:

- `2.0`

### `aggressive.strategy.atr_sl_mr_mult`

Aggressive-mode mean-reversion stop ATR multiplier.

Current value:

- `1.2`

### `aggressive.strategy.rsi_mr_threshold`

Aggressive-mode RSI threshold for mean-reversion entries.

Current value:

- `35`

### `aggressive.strategy.mean_reversion_time_stop_hours`

Aggressive-mode time stop for mean-reversion trades.

Current value:

- `12`

### `aggressive.loop_sleep_seconds`

Intended aggressive-mode loop delay.

Current value:

- `60`

Important:

- This key is currently placed at `aggressive.loop_sleep_seconds`, not `aggressive.strategy.loop_sleep_seconds`.
- The runtime sleep value is read from `cfg["strategy"]["loop_sleep_seconds"]`.
- Because of that, this aggressive override is currently not used by the trading loop.

## `alerts`

### `alerts.enable_telegram`

Enables Telegram integration for notifications and command polling.

Current value:

- `true`

### `alerts.telegram_bot_token`

Telegram bot token used to send alerts and receive commands.

Current value:

- empty string

Note:

- The runtime can also fill this from secure credentials storage or `TELEGRAM_BOT_TOKEN`.

### `alerts.telegram_chat_id`

Telegram chat id used as the destination for messages.

Current value:

- empty string

Note:

- The runtime can also fill this from secure credentials storage or `TELEGRAM_CHAT_ID`.

### `alerts.enable_trade_approval`

If `true`, buy and sell orders require Telegram approval before execution.

Current value:

- `true`

### `alerts.approval_timeout_sec`

How long the bot waits for a Telegram approval response before treating the action as denied.

Current value:

- `180`

### `alerts.telegram_owner_user_id`

Telegram user id allowed to run owner-only commands such as risk changes, cooldown changes, mode changes, and restart.

Current value:

- `8333351103`

## `logging`

### `logging.csv_dir`

Directory where the bot writes:

- CSV trade logs
- CSV equity snapshots
- `audit.log`
- `bot.log`

Current value:

- `./logs`

### `logging.state_file`

JSON file used to persist runtime state such as open positions, cooldowns, session state, and runtime health.

Current value:

- `./state.json`

### `logging.tz`

Timezone used for timestamps, session rollover, and some Telegram/audit timestamps.

Current value:

- `CET`

### `logging.level`

Python logging level for the bot logger.

Typical values:

- `DEBUG`
- `INFO`
- `WARNING`
- `ERROR`

Current value:

- `DEBUG`

## `network`

### `network.retry_count`

Number of retries for exchange network calls wrapped by the bot retry helper.

Current value:

- `3`

### `network.retry_backoff_sec`

Base wait time between retry attempts for retryable exchange/network errors.

Current value:

- `1.0`

## `credentials`

### `credentials.api_key`

Binance API key used for authenticated exchange access.

Current value:

- empty string

Note:

- The runtime can fill this from secure credentials storage or `BINANCE_API_KEY`.

### `credentials.api_secret`

Binance API secret used for authenticated exchange access.

Current value:

- empty string

Note:

- The runtime can fill this from secure credentials storage or `BINANCE_API_SECRET`.

## Practical summary

If you change only a few values regularly, these are the highest-impact ones:

- `general.symbols`: which markets are traded
- `general.timeframe_signal`: how often entry signals are evaluated
- `risk.per_trade_risk_pct`: position sizing aggressiveness
- `risk.max_concurrent_positions`: portfolio exposure cap
- `risk.daily_loss_stop_pct`: session loss guardrail
- `strategy.ema_fast` / `strategy.ema_slow`: trend sensitivity
- `strategy.atr_sl_trend_mult` / `strategy.atr_sl_mr_mult`: stop distance
- `alerts.enable_trade_approval`: manual approval gate

## Known config quirks in the current repo

These are worth keeping in mind when editing `config.yaml`:

1. Runtime expects `general.min_notional_usdc`, but the template and GUI still use `min_notional_usdt`.
2. `aggressive.loop_sleep_seconds` is currently not applied because the runtime reads `strategy.loop_sleep_seconds`.
3. Aggressive overrides only activate when both `dry_run=true` and `aggressive_mode=true`.
