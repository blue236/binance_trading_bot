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

try:
    import ccxt  # type: ignore
except ImportError as e:
    raise SystemExit("ccxt library is required; install via `pip install ccxt`." )

import pandas as pd  # type: ignore
import os
import matplotlib.pyplot as plt  # type: ignore


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
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
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
    return filepath


def mean_reversion_backtest(
    prices: List[float],
    window: int,
    threshold: float,
    capital: float = 10_000.0,
    currency_rate: float = 1.1,
) -> Tuple[float, List[int], List[int]]:
    """Simple mean‑reversion backtest.

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

    Returns
    -------
    Tuple[float, List[int], List[int]]
        Final capital in USDT and lists of buy and sell indices.
    """
    # Convert initial capital to USDT
    usdt = capital * currency_rate
    coins = 0.0
    position = False
    buy_indices: List[int] = []
    sell_indices: List[int] = []

    for i, price in enumerate(prices):
        if i + 1 >= window:
            window_prices = prices[i + 1 - window : i + 1]
            ma = sum(window_prices) / window
            deviation = (price - ma) / ma
            if not position and deviation <= -threshold:
                # Buy all with USDT
                coins = usdt / price
                usdt = 0.0
                position = True
                buy_indices.append(i)
            elif position and deviation >= threshold:
                # Sell all coins
                usdt = coins * price
                coins = 0.0
                position = False
                sell_indices.append(i)
    # If still holding at the end, liquidate
    if position:
        usdt = coins * prices[-1]
    return usdt, buy_indices, sell_indices


def run_backtests(
    symbols: List[str],
    timeframe: str,
    limit: int,
    windows: List[int],
    thresholds: List[float],
) -> None:
    """Fetch data for each symbol and run mean‑reversion backtests.

    Prints a summary table with ROI for each parameter combination.
    """
    exchange = ccxt.binance()
    results: List[Dict[str, Any]] = []
    for symbol in symbols:
        df = fetch_ohlcv(exchange, symbol, timeframe=timeframe, limit=limit)
        # Save OHLCV data for each symbol/timeframe/limit combination
        save_ohlcv_to_csv(df, symbol, timeframe, limit)
        closes = df["close"].tolist()
        for w in windows:
            for thresh in thresholds:
                final_usdt, buys, sells = mean_reversion_backtest(closes, w, thresh)
                roi = (final_usdt - 10_000.0 * 1.1) / (10_000.0 * 1.1) * 100
                # Generate plot showing buy/sell points on price data
                plot_trades(df, buys, sells, symbol, w, thresh)
                results.append({
                    "symbol": symbol,
                    "window": w,
                    "threshold": thresh,
                    "roi": roi,
                    "buys": len(buys),
                    "sells": len(sells),
                })
    # Create DataFrame for pretty printing
    results_df = pd.DataFrame(results)
    # Sort by ROI descending
    results_df = results_df.sort_values(by="roi", ascending=False)
    print(results_df.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance mean‑reversion backtester")
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="List of symbols in Binance format, e.g. BTC/USDT ETH/USDT",
    )
    parser.add_argument(
        "--timeframe",
        default="1d",
        help="Timeframe for OHLCV data (default: 1d)",
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
        "--thresholds",
        nargs="+",
        type=float,
        default=[0.02, 0.03, 0.05],
        help="List of deviation thresholds to test (e.g., 0.03 for 3%)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_backtests(args.symbols, args.timeframe, args.limit, args.windows, args.thresholds)


if __name__ == "__main__":
    main()