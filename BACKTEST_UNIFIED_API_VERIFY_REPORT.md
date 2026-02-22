# Backtest Unified API Verify Report

Date: 2026-02-22

## Scope
- Added `POST /api/backtest/run`
- mode: `quick | legacy | both`
- unified response items include:
  - `engine`
  - `summary`
  - `metrics`
  - `trades`
  - `equity_curve`

## Compatibility
- Existing endpoints preserved:
  - `POST /api/backtester/run`
  - `POST /api/legacy/backtester/run`

## Error format
Unified endpoint returns:

```json
{
  "ok": false,
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<human-readable>"
  }
}
```

## Static checks
- `python3 -m py_compile webapp/app.py webapp/backtest_service.py` ✅

## Manual API examples

### quick
```bash
curl -X POST http://127.0.0.1:8080/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"mode":"quick","symbol":"BTC/USDT","fast_window":20,"slow_window":60}'
```

### legacy
```bash
curl -X POST http://127.0.0.1:8080/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"mode":"legacy","symbol":"BTC/USDT"}'
```

### both
```bash
curl -X POST http://127.0.0.1:8080/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"mode":"both","symbol":"BTC/USDT"}'
```
