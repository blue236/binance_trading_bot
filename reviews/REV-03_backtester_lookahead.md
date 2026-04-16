# REV-03 — ML Lookahead Bias Audit: `backtester.py`

**Reviewer:** Python Code Reviewer (claude-sonnet-4-6)
**Date:** 2026-04-16
**File:** `backtester.py`
**Scope:** ML strategy (`strategy == "ml"`) — lookahead bias and train/test data leakage

---

## Overall Verdict: BLOCK

Two independent BLOCK-level defects are present. The ML backtest results are not meaningful: performance figures reflect the model's ability to memorize the future, not to predict it.

---

## Section 1 — `label_future_returns()`: Forward-looking labels

**Verdict: FAIL (BLOCK)**

```python
# backtester.py line 201–207
def label_future_returns(
    df: pd.DataFrame,
    holding_period_bars: int,
    buy_threshold: float,
    sell_threshold: float,
) -> pd.Series:
    """Label buy/hold/sell based on future returns over holding period."""
    future_return = df["close"].shift(-holding_period_bars) / df["close"] - 1.0
    labels = pd.Series(0, index=df.index)
    labels[future_return >= buy_threshold] = 1
    labels[future_return <= -sell_threshold] = -1
    return labels
```

`shift(-holding_period_bars)` shifts the close price series *backwards* in time, meaning the label at bar `i` encodes `close[i + holding_period_bars]`. The model is trained with the answer to the question it will later be asked to predict. This is a textbook lookahead bias (B-01).

With the default `holding_period_bars=12`, training label for bar `i` encodes the close price 12 bars in the future. An XGBoost classifier trained on these labels achieves artificially high accuracy because it has been shown the future price at fit time.

**Fix:** Replace the forward shift with a backward-looking or same-bar label. Two clean options:

Option A — same-bar return threshold (no lookahead at all):
```python
future_return = df["close"].pct_change()  # current bar return vs. prior bar
```

Option B — trailing structural label (last N-bar return, backward):
```python
future_return = df["close"] / df["close"].shift(holding_period_bars) - 1.0
```
Note: Option B labels based on what *already happened*, so the model learns momentum patterns rather than future prediction. Choose based on research intent.

---

## Section 2 — Train/test boundary contamination

**Verdict: FAIL (BLOCK)**

```python
# backtester.py lines 253–255
split_idx = int(len(data) * 0.7)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
```

The 70/30 split is chronological, which is correct in principle. However, because labels are built with `shift(-holding_period_bars)` before the split, training rows near the boundary have labels derived from close prices that fall *inside the test window*.

Concrete example with `holding_period_bars=12` and a 500-row dataset:
- `split_idx = 350`
- Training row at index 345 has its label derived from `close[357]`
- Index 357 is inside the test set (`X_test` starts at 350)

`dropna()` does not remove these rows — it only removes the last `holding_period_bars` rows at the tail of the series where `shift(-N)` produces NaN. The contaminated training rows at the boundary survive.

**Fix:** After labelling and before splitting, drop the `holding_period_bars` rows immediately preceding `split_idx` from the training set:

```python
split_idx = int(len(data) * 0.7)
# Drop boundary rows from training to prevent label contamination from test window
safe_train_end = split_idx - holding_period_bars
if safe_train_end <= 0:
    logger.warning("Dataset too small for safe train/test split; skipping ML backtest.")
    return usdt, buy_indices, sell_indices, buy_amounts, sell_amounts
X_train = X.iloc[:safe_train_end]
y_train = y.iloc[:safe_train_end]
X_test  = X.iloc[split_idx:]
y_test  = y.iloc[split_idx:]
```

---

## Section 3 — Features computed on full dataset before split

**Verdict: WARN**

```python
# backtester.py lines 237–238 (inside ml_pattern_backtest)
features = build_ml_features(df)
labels = label_future_returns(df, holding_period_bars, buy_threshold, sell_threshold)
```

`build_ml_features()` runs on the full `df` before any split occurs. The individual indicators it computes — RSI, EMA, MACD, ATR, Bollinger Bands, rolling z-scores — are all causal by default in pandas (each window looks only backward). No actual future data enters any training row at present.

However the architecture is fragile:
- A future maintainer adding a `StandardScaler` fitted on the full `X` (pre-split) would silently introduce global-mean and global-variance leakage.
- `volume_z` and `price_z` use `rolling(20).std()` which is causal, but any switch to an expanding global normalizer would break this.

**Fix:** Add a docstring warning to `build_ml_features()` stating that it must only be called on a train slice before being applied to test. Alternatively, restructure `ml_pattern_backtest()` to fit any scalers only on training data:

```python
features_train = build_ml_features(df.iloc[:split_idx])
features_test  = build_ml_features(df.iloc[split_idx:])
```

This costs a small amount of redundant computation but makes the data pipeline correctly isolated.

---

## Section 4 — Train/test split methodology

**Verdict: PASS**

The split at line 253 uses `iloc[:split_idx]` and `iloc[split_idx:]`, which preserves chronological order. This is the correct approach for time-series data. A random split (e.g. `train_test_split(shuffle=True)`) would be a harder BLOCK; the temporal ordering here is correct.

Note: A walk-forward (expanding window) cross-validation scheme would be more robust and is recommended for any production use, but the absence of it is not a blocking defect for research purposes.

---

## Section 5 — `dropna()` behavior

**Verdict: PASS**

```python
# backtester.py lines 240–242
data = features.copy()
data["label"] = labels
data = data.dropna()
```

`dropna()` correctly removes:
- The last `holding_period_bars` rows, where `shift(-N)` produced NaN labels.
- Early warmup rows with NaN feature values (RSI-14, EMA-26, rolling-20 windows).

It does not introduce a biased sample beyond the boundary contamination already identified in Section 2.

---

## Summary Table

| Section | Verdict | Description |
|---------|---------|-------------|
| 1. `label_future_returns()` shift(-N) | FAIL (BLOCK) | Labels encode future close prices; explicit lookahead |
| 2. Train/test boundary contamination | FAIL (BLOCK) | Last N training rows have labels from inside the test window |
| 3. Features computed pre-split | WARN | Causally safe today; architecture is fragile for future changes |
| 4. Split methodology | PASS | Chronological 70/30 split; temporal ordering preserved |
| 5. `dropna()` behavior | PASS | Removes NaN rows correctly; no additional bias introduced |

---

## Recommended Fix Priority

1. **Immediately** remove the `shift(-holding_period_bars)` in `label_future_returns()`. Use a backward-looking return or same-bar return as the label target.
2. **Immediately** drop the boundary-contaminated rows (last `holding_period_bars` training rows) from `X_train`/`y_train` if a forward-label scheme is retained for research.
3. **Before any further development** restructure `build_ml_features()` so it is fit only on training data and applied (without refitting) to test data.
4. **Optional improvement** replace the single 70/30 cut with an expanding-window walk-forward cross-validation loop.

---

*Referenced defect IDs: B-01 (forward-looking labels). Related review: REV-02_REV-04_report.md.*
