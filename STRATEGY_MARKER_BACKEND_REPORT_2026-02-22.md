# Strategy Marker Backend Report (2026-02-22)

## Goal
Provide real strategy-replay markers (not mock) from `config.yaml` strategy parameters in unified backtest API.

## Implemented
- Endpoint: `POST /api/backtest/run`
- Marker schema added per engine result:
  - `markers[]: { ts, index, price, side, reason }`
- Quick mode marker generation:
  - Reads `config.yaml` -> `strategy.ema_fast`, `strategy.ema_slow`
  - Replays SMA-crossover signal over OHLCV (same quick backtest data)
  - Emits BUY/SELL markers on crossover-triggered entry/exit points

## Consistency by mode
- `mode=quick`
  - `markers`: populated with SMA replay signals
- `mode=legacy`
  - `markers`: empty array `[]` (legacy CLI output has no structured marker stream)
- `mode=both`
  - quick result: populated markers
  - legacy result: empty markers

## Error behavior
Unified endpoint error format preserved:

```json
{
  "ok": false,
  "error": {
    "code": "...",
    "message": "..."
  }
}
```

## Validation
- Static compile check:
  - `python3 -m py_compile webapp/app.py webapp/backtest_service.py`
