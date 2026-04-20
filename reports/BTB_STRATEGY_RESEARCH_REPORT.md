# BTB Multi-Symbol Strategy Revalidation Report (OOS-focused)

- Data source: `webapp_state.sqlite` (`ohlcv` table, BTB chart refresh)
- Symbols: BTC/USDC
- Timeframe: `1h`
- Split: Train 60% / Validation 20% / Test 20% (time-ordered per symbol)
- Cost assumptions: fee `0.10%` + slippage `0.05%` per side

## Data Coverage
| Symbol | Bars | From | To |
|---|---:|---|---|
| BTC/USDC | 1000 | 2026-03-06 | 2026-04-17 |

## Baseline (EMA 50/200)
### Aggregate (equal-weight by symbol)
| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |
|---|---:|---:|---:|---:|---:|
| train | -6.76% | -11.63% | 0.0% | 2 | 0.000 |
| val | 0.00% | 0.00% | 0.0% | 0 | 0.000 |
| test | 0.00% | 0.00% | 0.0% | 0 | 0.000 |

### Test (OOS) by Symbol
| Symbol | Net Return | Max DD | Trades |
|---|---:|---:|---:|
| BTC/USDC | 0.00% | 0.00% | 0 |

## Candidate Search Space
- `trend_pullback` score=-25.7836
- `mean_reversion_bb` score=-22.0732
- `breakout_dynamic_exit` score=-12.7279
- `h_v5_breakout` score=-39.0

## Winner: `breakout_dynamic_exit`
- Selection score: -12.7279
- Params: `{"don_len": 10, "ema_slow": 100, "adx_len": 14, "atr_len": 14, "adx_min": 14, "trail_atr_mult": 2.0, "init_sl_atr_mult": 1.2, "cooldown_bars": 2}`

## Winner Performance
### Aggregate (equal-weight by symbol)
| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |
|---|---:|---:|---:|---:|---:|
| train | -4.95% | -6.29% | 22.2% | 9 | 0.171 |
| val | -0.74% | -2.26% | 40.0% | 5 | 0.661 |
| test | -1.24% | -2.85% | 16.7% | 6 | 0.467 |

### Test (OOS) by Symbol
| Symbol | Net Return | Max DD | Trades |
|---|---:|---:|---:|
| BTC/USDC | -1.24% | -2.85% | 6 |

## Conclusion
- Aggregate OOS return: baseline `0.00%` vs winner `-1.24%`.
- Aggregate OOS max DD: baseline `0.00%` vs winner `-2.85%`.
- Aggregate OOS trades: baseline `0` vs winner `6`.
- Winner was selected with OOS trade-count penalty to avoid sparse-trade overfitting.
