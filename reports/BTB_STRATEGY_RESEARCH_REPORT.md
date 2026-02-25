# BTB Strategy Research Report (OOS-focused)

- Data source: `webapp_state.sqlite` (`ohlcv` table populated by BTB web chart refresh)
- Dataset used for optimization: `BTC/USDC`, `1d`, 705 bars (`2024-03-21` ~ `2026-02-23`)
- Cost assumptions: fee `0.10%` + slippage `0.05%` per side (`0.15%` side cost)
- Split: Train 60% / Validation 20% / Test 20% (time-ordered)

## Baseline (current quick baseline)
- Strategy: EMA crossover 50/200

| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |
|---|---:|---:|---:|---:|---:|
| Train | 32.93% | -28.16% | 100.0% | 2 | 999.000 |
| Validation | 0.00% | 0.00% | 0.0% | 0 | 0.000 |
| Test | 0.00% | 0.00% | 0.0% | 0 | 0.000 |
| Full | 18.69% | -28.16% | 50.0% | 2 | 9.824 |

## Candidate Strategies Explored
1. `trend_pullback`
2. `mean_reversion_bb` (BB + RSI + ADX regime)
3. `breakout_dynamic_exit`

## Best Candidate (validation-score winner)
- Strategy: **mean_reversion_bb**
- Params: `bb_len=20, bb_mult=2.0, adx_max=24, rsi_entry=40, rsi_exit=55, sl_atr_mult=1.6, max_hold_bars=14`

| Window | Net Return | Max DD | Win Rate | Trades | Profit Factor |
|---|---:|---:|---:|---:|---:|
| Train | 25.84% | -7.12% | 80.0% | 5 | 4.570 |
| Validation | 9.69% | -3.83% | 100.0% | 3 | 999.000 |
| Test | 0.00% | 0.00% | 0.0% | 0 | 0.000 |

## Interpretation (anti-overfit view)
- Candidate reduced drawdown significantly vs baseline in train/validation.
- However, **test window has no trades** (insufficient signal frequency in latest segment), so OOS conviction is limited.
- Therefore this should be treated as a **research profile only**, not production rollout yet.

## AI model feasibility
- Because OOS test activity/performance is not yet robust, adding AI now is likely premature.
- Recommended next AI scope (after more OOS data):
  - Regime classifier (`trend/range/high-vol`) to gate BB entries.
  - Signal quality scoring model to suppress low-expectancy entries.
- Risk: data size is currently too small for stable ML generalization.

## Code changes in this branch
- Added reproducible research script: `scripts/btb_strategy_research.py`
- Added report artifact: `reports/btb_strategy_research.json`, `reports/BTB_STRATEGY_RESEARCH_REPORT.md`
- Implemented optional strategy mode in runtime: `mean_reversion_bb_regime` in `main.py`
- Added non-live config profile: `config.btb_research_mr.yaml`
