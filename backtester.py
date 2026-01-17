#!/usr/bin/env python3
"""
Backtester for Binance spot markets.

This script fetches historical OHLCV data from the Binance exchange and
provides a simple mean‑reversion backtest across multiple symbols and
parameter combinations. It is intended as a starting point for
quantitative research and should be extended to incorporate
commission/slippage, position sizing, and more sophisticated risk
management.

Usage example:

    python backtester.py --symbols BTC/USDT ETH/USDT SOL/USDT \
        --timeframe 1d --limit 365 --windows 3 5 7 --thresholds 0.02 0.03 0.05

This will download up to one year of daily candles for three markets and
evaluate mean‑reversion strategies with the specified windows and
thresholds. Results are printed to stdout.

Prerequisites:
    pip install ccxt pandas

Note: Binance requires an API key for high‑volume requests. For research
purposes the public endpoint without credentials typically suffices.
"""

import argparse
import datetime as dt
from typing import List, Tuple, Dict, Any
import warnings
import sys

try:
    import ccxt  # type: ignore
except ImportError as e:
    raise SystemExit("ccxt library is required; install via `pip install ccxt`." )

import pandas as pd  # type: ignore
import numpy as np  # type: ignore
import os
import matplotlib.pyplot as plt  # type: ignore
import logging
import ta  # type: ignore

# Set up basic logging configuration. Debug messages will aid troubleshooting.
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# Create a module-level logger
logger = logging.getLogger(__name__)

# Reduce verbose logs from third-party libs and silence known benign warnings.
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)
warnings.filterwarnings(
    "ignore",
    message=r"\[Errno 13\] Permission denied.*joblib will operate in serial mode",
    category=UserWarning,
)


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str = "1d",
    since: int | None = None,
    limit: int = 500,
) -> pd.DataFrame:
    """Fetch OHLCV data for a symbol and return as DataFrame.

    Parameters
    ----------
    exchange : ccxt.Exchange
        An instantiated exchange object.
    symbol : str
        Symbol to fetch, e.g. "BTC/USDT".
    timeframe : str, default "1d"
        Resolution such as '1h', '4h', '1d'.
    since : int | None, default None
        Unix timestamp in milliseconds; data earlier than this will not be fetched.
    limit : int, default 500
        Maximum number of candles to retrieve per request. Most exchanges cap this at 500–1000.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns [timestamp, open, high, low, close, volume].
    """
    # Fetch data from the exchange
    logger.debug(f"Fetching OHLCV for {symbol} (timeframe={timeframe}, limit={limit}, since={since})")
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    logger.debug(f"Fetched {len(df)} candles for {symbol}")
    return df

def save_ohlcv_to_csv(df: pd.DataFrame, symbol: str, timeframe: str, limit: int, output_dir: str = "datasheets") -> str:
    """Save OHLCV DataFrame to a CSV file.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing OHLCV data.
    symbol : str
        Symbol string (e.g., "BTC/USDT") used for filename.
    timeframe : str
        Timeframe string (e.g., "1d") used for filename.
    limit : int
        Number of candles retrieved used for filename.
    output_dir : str, default "datasheets"
        Directory where CSV files will be stored.

    Returns
    -------
    str
        Path to the saved CSV file.
    """
    os.makedirs(output_dir, exist_ok=True)
    # Replace slash in symbol to make file system friendly
    clean_symbol = symbol.replace("/", "_")
    filename = f"{clean_symbol}_{timeframe}_{limit}.csv"
    filepath = os.path.join(output_dir, filename)
    df.to_csv(filepath, index=False)
    logger.debug(f"Saved OHLCV data for {symbol} to {filepath}")
    return filepath

def plot_trades(df: pd.DataFrame, buy_indices: List[int], sell_indices: List[int], symbol: str, window: int, threshold: float, output_dir: str = "plots") -> str:
    """Generate a line plot of closing prices and overlay buy/sell markers.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing at least 'timestamp' and 'close' columns.
    buy_indices : List[int]
        Indices in the DataFrame where buy actions occur.
    sell_indices : List[int]
        Indices in the DataFrame where sell actions occur.
    symbol : str
        Trading pair symbol (e.g., "BTC/USDT").
    window : int
        Moving average window used in the backtest.
    threshold : float
        Deviation threshold used in the backtest.
    output_dir : str, default "plots"
        Directory where plot images will be saved.

    Returns
    -------
    str
        Path to the saved plot image.
    """
    os.makedirs(output_dir, exist_ok=True)
    clean_symbol = symbol.replace("/", "_")
    # Build filename using threshold percentage without decimal point for clarity
    threshold_pct = int(threshold * 100)
    filename = f"{clean_symbol}_win{window}_thr{threshold_pct}.png"
    filepath = os.path.join(output_dir, filename)

    logger.debug(f"Generating trade plot for {symbol}, window={window}, threshold={threshold}")
    plt.figure(figsize=(12, 6))
    # Plot closing prices
    plt.plot(df["timestamp"], df["close"], label="Close Price", color="blue")
    # Scatter buy and sell points
    if buy_indices:
        plt.scatter(df["timestamp"].iloc[buy_indices], df["close"].iloc[buy_indices], marker="^", color="green", label="Buy")
    if sell_indices:
        plt.scatter(df["timestamp"].iloc[sell_indices], df["close"].iloc[sell_indices], marker="v", color="red", label="Sell")
    plt.title(f"{symbol} Close Price with Trades (Window={window}, Threshold={threshold:.2f})")
    plt.xlabel("Date")
    plt.ylabel("Price")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(filepath)
    plt.close()
    logger.debug(f"Saved trade plot for {symbol} to {filepath}")
    return filepath


def build_ml_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build feature set for ML pattern recognition using technical indicators."""
    features = pd.DataFrame(index=df.index)
    features["close"] = df["close"]
    features["return_1"] = df["close"].pct_change()
    features["log_return_1"] = np.log(df["close"]).diff()
    features["rsi_14"] = ta.momentum.rsi(df["close"], window=14)
    features["ema_12"] = ta.trend.ema_indicator(df["close"], window=12)
    features["ema_26"] = ta.trend.ema_indicator(df["close"], window=26)
    features["macd"] = ta.trend.macd(df["close"])
    features["macd_signal"] = ta.trend.macd_signal(df["close"])
    features["atr_14"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    features["bb_high"] = ta.volatility.bollinger_hband(df["close"], window=20)
    features["bb_low"] = ta.volatility.bollinger_lband(df["close"], window=20)
    vol_mean = df["volume"].rolling(20).mean()
    vol_std = df["volume"].rolling(20).std()
    features["volume_z"] = (df["volume"] - vol_mean) / vol_std
    features["price_z"] = (df["close"] - df["close"].rolling(20).mean()) / df["close"].rolling(20).std()
    return features


def label_future_returns(df: pd.DataFrame, holding_period_bars: int, buy_threshold: float, sell_threshold: float) -> pd.Series:
    """Label buy/hold/sell based on future returns over holding period."""
    future_return = df["close"].shift(-holding_period_bars) / df["close"] - 1.0
    labels = pd.Series(0, index=df.index)
    labels[future_return >= buy_threshold] = 1
    labels[future_return <= -sell_threshold] = -1
    return labels


def ml_pattern_backtest(
    df: pd.DataFrame,
    holding_period_bars: int,
    max_trades_per_month: int,
    buy_threshold: float,
    sell_threshold: float,
    min_trade_confidence: float,
    capital: float = 10_000.0,
    currency_rate: float = 1.1,
    fee: float = 0.001,
    slippage: float = 0.0005,
    spread: float = 0.0005,
) -> Tuple[float, List[int], List[int], List[float], List[float]]:
    """ML-based backtest with pattern recognition and trade throttling."""
    try:
        from xgboost import XGBClassifier  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "xgboost is required for the ML strategy; install via `pip install xgboost`."
        ) from exc
    usdt = capital * currency_rate
    coins = 0.0
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    buy_amounts: List[float] = []
    sell_amounts: List[float] = []

    features = build_ml_features(df)
    labels = label_future_returns(df, holding_period_bars, buy_threshold, sell_threshold)

    data = features.copy()
    data["label"] = labels
    data = data.dropna()

    if data.empty or len(data) < 50:
        logger.warning("Not enough data after feature engineering; skipping ML backtest.")
        return usdt, buy_indices, sell_indices, buy_amounts, sell_amounts

    label_map = {-1: 0, 0: 1, 1: 2}
    inv_label_map = {0: -1, 1: 0, 2: 1}
    y = data["label"].map(label_map)
    X = data.drop(columns=["label"])

    split_idx = int(len(data) * 0.7)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=42,
    )
    model.fit(X_train, y_train)

    probs = model.predict_proba(X_test)
    preds = np.argmax(probs, axis=1)

    holding_counter = 0
    entry_times: List[pd.Timestamp] = []
    test_indices = X_test.index.tolist()

    for row_idx, pred_class, prob_row in zip(test_indices, preds, probs):
        timestamp = df.loc[row_idx, "timestamp"]
        price = df.loc[row_idx, "close"]

        if coins > 0:
            holding_counter += 1

        buy_prob = prob_row[2]
        sell_prob = prob_row[0]
        predicted_label = inv_label_map[int(pred_class)]

        if coins == 0 and predicted_label == 1 and buy_prob >= min_trade_confidence:
            recent_entries = [t for t in entry_times if timestamp - t <= pd.Timedelta(days=30)]
            if len(recent_entries) < max_trades_per_month:
                entry_times = recent_entries
                ask_price = price * (1 + spread / 2)
                trade_price = ask_price * (1 + slippage)
                coins_purchased = (usdt / trade_price) * (1 - fee)
                if coins_purchased > 0:
                    coins += coins_purchased
                    usdt = 0.0
                    buy_indices.append(row_idx)
                    buy_amounts.append(coins_purchased)
                    holding_counter = 0
                    entry_times.append(timestamp)
        elif coins > 0:
            force_exit = holding_counter >= holding_period_bars
            model_exit = predicted_label == -1 and sell_prob >= min_trade_confidence
            if force_exit or model_exit:
                bid_price = price * (1 - spread / 2)
                trade_price = bid_price * (1 - slippage)
                usdt += coins * trade_price * (1 - fee)
                sell_indices.append(row_idx)
                sell_amounts.append(coins)
                coins = 0.0
                holding_counter = 0

    if coins > 0:
        final_price = df["close"].iloc[-1]
        bid_price = final_price * (1 - spread / 2)
        trade_price = bid_price * (1 - slippage)
        usdt += coins * trade_price * (1 - fee)
        sell_indices.append(len(df) - 1)
        sell_amounts.append(coins)
        coins = 0.0

    return usdt, buy_indices, sell_indices, buy_amounts, sell_amounts

def mean_reversion_backtest(
    prices: List[float],
    window: int,
    threshold: float,
    capital: float = 10_000.0,
    currency_rate: float = 1.1,
    fee: float = 0.001,
    slippage: float = 0.0005,
    spread: float = 0.0005,
    volatility_threshold: float = 0.05,
    position_split_factor: float = 0.5,
) -> Tuple[float, List[int], List[int], List[float], List[float]]:
    """Mean‑reversion backtest with transaction costs and volatility‑based position sizing.

    Parameters
    ----------
    prices : List[float]
        List of closing prices.
    window : int
        Moving average window length.
    threshold : float
        Relative deviation from moving average to trigger trades. For example,
        0.03 means buy when price is 3% below MA and sell when price is 3% above MA.
    capital : float, default 10_000.0
        Starting capital in euros.
    currency_rate : float, default 1.1
        EUR to USDT conversion rate. Capital will be converted to USDT.
    fee : float, default 0.001
        Commission fee per trade (e.g., 0.001 = 0.1%). Applied on trade value.
    slippage : float, default 0.0005
        Slippage factor applied to trade price (e.g., 0.0005 = 0.05%).
    spread : float, default 0.0005
        Bid/ask spread factor; half of this is applied to buy and sell prices.
    volatility_threshold : float, default 0.05
        Threshold for volatility (absolute percentage change) to trigger partial position sizing.
    position_split_factor : float, default 0.5
        Fraction of capital or position to deploy when volatility exceeds the threshold.

    Returns
    -------
    Tuple[float, List[int], List[int], List[float], List[float]]
        Final capital in USDT, lists of buy/sell indices, and corresponding quantities traded.
    """
    # Convert initial capital to USDT
    usdt = capital * currency_rate
    coins = 0.0
    buy_indices: List[int] = []
    sell_indices: List[int] = []
    # Track quantities of each buy and sell transaction
    buy_amounts: List[float] = []
    sell_amounts: List[float] = []

    logger.debug(f"Starting mean-reversion backtest: window={window}, threshold={threshold}")
    for i, price in enumerate(prices):
        # Only consider signals when enough data points are available
        if i + 1 >= window:
            window_prices = prices[i + 1 - window : i + 1]
            ma = sum(window_prices) / window
            deviation = (price - ma) / ma
            # Compute recent volatility based on previous close
            vol = 0.0
            if i > 0 and prices[i - 1] != 0:
                vol = abs(price - prices[i - 1]) / prices[i - 1]
            # Determine fraction of capital/position to use based on volatility
            invest_fraction = position_split_factor if vol > volatility_threshold else 1.0
            # Buy logic
            if coins == 0 and deviation <= -threshold:
                # Amount of USDT to allocate
                amount_to_invest = usdt * invest_fraction
                if amount_to_invest > 0:
                    # Adjust price for spread and slippage
                    ask_price = price * (1 + spread / 2)
                    trade_price = ask_price * (1 + slippage)
                    # Calculate coins purchased, accounting for fees
                    coins_purchased = (amount_to_invest / trade_price) * (1 - fee)
                    coins += coins_purchased
                    usdt -= amount_to_invest
                    buy_indices.append(i)
                    buy_amounts.append(coins_purchased)
                    logger.debug(
                        f"Buy signal at index {i}: price={price:.5f}, MA={ma:.5f}, deviation={deviation:.5f}, vol={vol:.5f}, invest_fraction={invest_fraction:.2f}, coins_added={coins_purchased:.5f}, USDT_remain={usdt:.2f}"
                    )
            # Sell logic
            elif coins > 0 and deviation >= threshold:
                sell_fraction = position_split_factor if vol > volatility_threshold else 1.0
                # Determine number of coins to sell. Ensure it does not exceed current holdings.
                sell_coins = coins * sell_fraction
                sell_coins = min(sell_coins, coins)
                if sell_coins > 0:
                    # Adjust price for spread and slippage on sell
                    bid_price = price * (1 - spread / 2)
                    trade_price = bid_price * (1 - slippage)
                    usdt += sell_coins * trade_price * (1 - fee)
                    coins -= sell_coins
                    sell_indices.append(i)
                    sell_amounts.append(sell_coins)
                    logger.debug(
                        f"Sell signal at index {i}: price={price:.5f}, MA={ma:.5f}, deviation={deviation:.5f}, vol={vol:.5f}, sell_fraction={sell_fraction:.2f}, coins_sold={sell_coins:.5f}, USDT_now={usdt:.2f}"
                    )
    # Liquidate any remaining coins at final price
    if coins > 0:
        final_price = prices[-1]
        bid_price = final_price * (1 - spread / 2)
        trade_price = bid_price * (1 - slippage)
        usdt += coins * trade_price * (1 - fee)
        sell_indices.append(len(prices) - 1)
        sell_amounts.append(coins)
        coins = 0.0
    # Sanity check: total sold should not exceed total bought
    if sum(sell_amounts) > sum(buy_amounts):
        logger.warning(
            f"Warning: total sold quantity {sum(sell_amounts):.5f} exceeds total bought quantity {sum(buy_amounts):.5f}."
        )
    logger.debug(
        f"Backtest completed: final USDT={usdt:.2f}, buys={len(buy_indices)}, sells={len(sell_indices)}, total_bought={sum(buy_amounts):.5f}, total_sold={sum(sell_amounts):.5f}"
    )
    return usdt, buy_indices, sell_indices, buy_amounts, sell_amounts


def run_backtests(
    symbols: List[str],
    timeframe: str,
    limit: int,
    windows: List[int],
    thresholds: List[float],
    fee: float = 0.001,
    slippage: float = 0.0005,
    spread: float = 0.0005,
    volatility_threshold: float = 0.05,
    position_split_factor: float = 0.5,
    optimize_method: str = "grid",
    random_samples: int = 10,
    strategy: str = "mean_reversion",
    holding_period_bars: int = 12,
    max_trades_per_month: int = 8,
    buy_threshold: float = 0.02,
    sell_threshold: float = 0.02,
    min_trade_confidence: float = 0.65,
) -> None:
    """Fetch data for each symbol and run backtests with parameter optimisation.

    Depending on the optimise method (grid or random), evaluate combinations of moving-average
    windows and thresholds. For each symbol, results are returned and the best parameter
    combination is identified based on highest ROI.
    """
    exchange = ccxt.binance()
    results: List[Dict[str, Any]] = []
    for symbol in symbols:
        logger.debug(f"Running backtests for {symbol}")
        # Determine if data already exists in datasheets
        clean_symbol = symbol.replace("/", "_")
        csv_filename = f"{clean_symbol}_{timeframe}_{limit}.csv"
        csv_path = os.path.join("datasheets", csv_filename)
        if os.path.exists(csv_path):
            # Load existing data instead of fetching
            logger.debug(f"Found cached OHLCV data for {symbol}: {csv_path}, skipping fetch")
            df = pd.read_csv(csv_path)
            # Ensure timestamp column is datetime
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
        else:
            # Fetch fresh data and save
            df = fetch_ohlcv(exchange, symbol, timeframe=timeframe, limit=limit)
            save_ohlcv_to_csv(df, symbol, timeframe, limit)
        closes = df["close"].tolist()
        if strategy == "mean_reversion":
            # Determine parameter combinations based on optimisation method
            param_combinations: List[Tuple[int, float]] = []
            if optimize_method == "grid":
                for w in windows:
                    for thresh in thresholds:
                        param_combinations.append((w, thresh))
            else:
                # Random sampling of provided parameter lists
                import random
                for _ in range(max(1, random_samples)):
                    w = random.choice(windows)
                    thresh = random.choice(thresholds)
                    param_combinations.append((w, thresh))
            for w, thresh in param_combinations:
                final_usdt, buys, sells, buy_amts, sell_amts = mean_reversion_backtest(
                    closes, w, thresh,
                    capital=10_000.0,
                    currency_rate=1.1,
                    fee=fee,
                    slippage=slippage,
                    spread=spread,
                    volatility_threshold=volatility_threshold,
                    position_split_factor=position_split_factor,
                )
                # ROI relative to starting USDT (capital * currency_rate)
                initial_usdt = 10_000.0 * 1.1
                roi = (final_usdt - initial_usdt) / initial_usdt * 100
                # Generate plot showing buy/sell points on price data
                plot_trades(df, buys, sells, symbol, w, thresh)
                # Calculate total quantities traded (in units of the underlying asset)
                total_bought = sum(buy_amts)
                total_sold = sum(sell_amts)
                # Append results
                results.append({
                    "symbol": symbol,
                    "window": w,
                    "threshold": thresh,
                    "roi": roi,
                    "buys": len(buys),
                    "sells": len(sells),
                    "total_bought": total_bought,
                    "total_sold": total_sold,
                })
        else:
            final_usdt, buys, sells, buy_amts, sell_amts = ml_pattern_backtest(
                df,
                holding_period_bars=holding_period_bars,
                max_trades_per_month=max_trades_per_month,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
                min_trade_confidence=min_trade_confidence,
                fee=fee,
                slippage=slippage,
                spread=spread,
            )
            initial_usdt = 10_000.0 * 1.1
            roi = (final_usdt - initial_usdt) / initial_usdt * 100
            plot_trades(df, buys, sells, symbol, holding_period_bars, buy_threshold)
            total_bought = sum(buy_amts)
            total_sold = sum(sell_amts)
            results.append({
                "symbol": symbol,
                "window": holding_period_bars,
                "threshold": buy_threshold,
                "roi": roi,
                "buys": len(buys),
                "sells": len(sells),
                "total_bought": total_bought,
                "total_sold": total_sold,
            })
    # Create DataFrame for pretty printing
    results_df = pd.DataFrame(results)
    # Identify best parameters for each symbol
    summary_rows: List[Dict[str, Any]] = []
    for symbol in results_df["symbol"].unique():
        symbol_df = results_df[results_df["symbol"] == symbol]
        best_idx = symbol_df["roi"].idxmax()
        best_row = symbol_df.loc[best_idx]
        summary_rows.append({
            "symbol": symbol,
            "best_window": int(best_row["window"]),
            "best_threshold": float(best_row["threshold"]),
            "best_roi": float(best_row["roi"]),
            "buys": int(best_row["buys"]),
            "sells": int(best_row["sells"]),
        })
    summary_df = pd.DataFrame(summary_rows)
    # Print detailed results sorted by ROI
    print("Detailed Results:")
    print(results_df.sort_values(by="roi", ascending=False).to_string(index=False))
    print("\nBest Parameters per Symbol:")
    print(summary_df.to_string(index=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance mean‑reversion backtester")
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="List of symbols in Binance format, e.g. BTC/USDT ETH/USDT",
    )
    parser.add_argument(
        "--timeframe",
        default="4h",
        help="Timeframe for OHLCV data (default: 4h)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Number of candles to fetch per symbol (default: 500)",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[3, 5, 7],
        help="List of moving average windows to test",
    )
    parser.add_argument(
        "--strategy",
        choices=["mean_reversion", "ml"],
        default="mean_reversion",
        help="Strategy to run: 'mean_reversion' or 'ml' (default: mean_reversion)",
    )
    parser.add_argument(
        "--holding-period-bars",
        type=int,
        default=12,
        help="Holding period in bars for ML strategy (default: 12)",
    )
    parser.add_argument(
        "--max-trades-per-month",
        type=int,
        default=8,
        help="Max number of new trades per 30-day window for ML strategy (default: 8)",
    )
    parser.add_argument(
        "--buy-threshold",
        type=float,
        default=0.02,
        help="Future return threshold for buy label in ML strategy (default: 0.02)",
    )
    parser.add_argument(
        "--sell-threshold",
        type=float,
        default=0.02,
        help="Future return threshold for sell label in ML strategy (default: 0.02)",
    )
    parser.add_argument(
        "--min-trade-confidence",
        type=float,
        default=0.65,
        help="Minimum predicted probability required to trade in ML strategy (default: 0.65)",
    )
    parser.add_argument(
        "--thresholds",
        nargs="+",
        type=float,
        default=[0.02, 0.03, 0.05],
        help="List of deviation thresholds to test (e.g., 0.03 for 3%%)",
    )
    parser.add_argument(
        "--fee",
        type=float,
        default=0.001,
        help="Commission fee per trade (default: 0.001 = 0.1%%)",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=0.0005,
        help="Slippage factor applied to trade price (default: 0.0005 = 0.05%%)",
    )
    parser.add_argument(
        "--spread",
        type=float,
        default=0.0005,
        help="Bid/ask spread factor (default: 0.0005 = 0.05%%)",
    )
    parser.add_argument(
        "--volatility-threshold",
        type=float,
        default=0.05,
        help="Volatility threshold to trigger partial position sizing (default: 0.05 = 5%%)",
    )
    parser.add_argument(
        "--position-split-factor",
        type=float,
        default=0.5,
        help="Fraction of capital or coins to trade when volatility exceeds threshold (default: 0.5)",
    )
    parser.add_argument(
        "--optimize-method",
        choices=["grid", "random"],
        default="grid",
        help="Parameter search method: 'grid' for exhaustive search or 'random' for random sampling",
    )
    parser.add_argument(
        "--random-samples",
        type=int,
        default=10,
        help="Number of random parameter combinations to evaluate when optimize-method=random",
    )
    return parser


def parse_args() -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args()


def main() -> None:
    if len(sys.argv) == 1:
        build_parser().print_help()
        return
    args = parse_args()
    run_backtests(
        args.symbols,
        args.timeframe,
        args.limit,
        args.windows,
        args.thresholds,
        fee=args.fee,
        slippage=args.slippage,
        spread=args.spread,
        volatility_threshold=args.volatility_threshold,
        position_split_factor=args.position_split_factor,
        optimize_method=args.optimize_method,
        random_samples=args.random_samples,
        strategy=args.strategy,
        holding_period_bars=args.holding_period_bars,
        max_trades_per_month=args.max_trades_per_month,
        buy_threshold=args.buy_threshold,
        sell_threshold=args.sell_threshold,
        min_trade_confidence=args.min_trade_confidence,
    )


if __name__ == "__main__":
    main()
