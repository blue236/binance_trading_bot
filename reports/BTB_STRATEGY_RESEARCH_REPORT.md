# BTB Multi-Symbol Strategy Revalidation Report (OOS-focused)

- Data source: `webapp_state.sqlite` (`ohlcv` table, BTB chart refresh)
- Symbols: BTC/USDT, ETH/USDT, SOL/USDT
- Timeframe: `1d`
- Split: Train 60% / Validation 20% / Test 20% (time-ordered per symbol)
- Cost assumptions: fee `0.10%` + slippage `0.05%` per side

## Data Coverage
| Symbol | Bars | From | To |
|---|---:|---|---|
| BTC/USDT | 1000 | 2023-06-02 | 2026-02-25 |
| ETH/USDT | 1000 | 2023-06-02 | 2026-02-25 |
| SOL/USDT | 1000 | 2023-06-02 | 2026-02-25 |

## Baseline (EMA 50/200)
### Aggregate (equal-weight by symbol)
| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |
|---|---:|---:|---:|---:|---:|
| train | 122.63% | -35.58% | 83.3% | 4 | 671.344 |
| val | 0.00% | 0.00% | 0.0% | 0 | 0.000 |
| test | 0.00% | 0.00% | 0.0% | 0 | 0.000 |

### Test (OOS) by Symbol
| Symbol | Net Return | Max DD | Trades |
|---|---:|---:|---:|
| BTC/USDT | 0.00% | 0.00% | 0 |
| ETH/USDT | 0.00% | 0.00% | 0 |
| SOL/USDT | 0.00% | 0.00% | 0 |

## Candidate Search Space
- `trend_pullback` score=-30.8435
- `mean_reversion_bb` score=8.7328
- `breakout_dynamic_exit` score=-15.231

## Winner: `mean_reversion_bb`
- Selection score: 8.7328
- Params: `{"bb_len": 20, "bb_mult": 1.8, "rsi_len": 14, "atr_len": 14, "adx_len": 14, "adx_max": 24, "rsi_entry": 40, "rsi_exit": 55, "sl_atr_mult": 1.2, "max_hold_bars": 8}`

## Winner Performance
### Aggregate (equal-weight by symbol)
| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |
|---|---:|---:|---:|---:|---:|
| train | 1.20% | -12.98% | 46.7% | 20 | 1.113 |
| val | 12.99% | -8.70% | 70.0% | 10 | 334.200 |
| test | 3.97% | -10.39% | 41.7% | 9 | 10.634 |

### Test (OOS) by Symbol
| Symbol | Net Return | Max DD | Trades |
|---|---:|---:|---:|
| BTC/USDT | 4.20% | -4.25% | 2 |
| ETH/USDT | 18.97% | -7.18% | 4 |
| SOL/USDT | -11.25% | -19.75% | 3 |

## Conclusion
- Aggregate OOS return: baseline `0.00%` vs winner `3.97%`.
- Aggregate OOS max DD: baseline `0.00%` vs winner `-10.39%`.
- Aggregate OOS trades: baseline `0` vs winner `9`.
- Winner was selected with OOS trade-count penalty to avoid sparse-trade overfitting.
