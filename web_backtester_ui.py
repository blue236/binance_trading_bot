#!/usr/bin/env python3
import argparse
import errno
import html
import mimetypes
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PLOTS_DIR = os.path.join(ROOT_DIR, "plots")


DEFAULTS = {
    "symbols": "BTC/USDT, ETH/USDT",
    "timeframe": "4h",
    "limit": "500",
    "windows": "3, 5, 7",
    "thresholds": "0.02, 0.03, 0.05",
    "fee": "0.001",
    "slippage": "0.0005",
    "spread": "0.0005",
    "volatility_threshold": "0.05",
    "position_split_factor": "0.5",
    "optimize_method": "grid",
    "random_samples": "10",
    "strategy": "mean_reversion",
    "holding_period_bars": "12",
    "max_trades_per_month": "8",
    "buy_threshold": "0.02",
    "sell_threshold": "0.02",
    "min_trade_confidence": "0.65",
    "pivot_lookback": "20",
    "pivot_rebound_pct": "0.012",
    "pivot_pullback_pct": "0.012",
    "pivot_rsi_window": "14",
    "pivot_rsi_low": "30",
    "pivot_rsi_high": "70",
    "pivot_ema_fast": "9",
    "pivot_ema_slow": "21",
    "pivot_adx_window": "14",
    "pivot_adx_threshold": "18",
    "pivot_atr_window": "14",
    "pivot_atr_pct_threshold": "0.01",
    "pivot_split_count": "3",
}


def split_tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[,\s]+", value.strip()) if t]


def list_plot_files() -> list[str]:
    if not os.path.isdir(PLOTS_DIR):
        return []
    files = [
        f
        for f in os.listdir(PLOTS_DIR)
        if os.path.isfile(os.path.join(PLOTS_DIR, f))
        and f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    ]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(PLOTS_DIR, f)), reverse=True)
    return files


def build_command(values: dict) -> list[str]:
    symbols = split_tokens(values["symbols"])
    windows = split_tokens(values["windows"])
    thresholds = split_tokens(values["thresholds"])
    cmd = [
        sys.executable,
        "backtester.py",
        "--symbols",
        *symbols,
        "--timeframe",
        values["timeframe"],
        "--limit",
        values["limit"],
        "--windows",
        *windows,
        "--thresholds",
        *thresholds,
        "--fee",
        values["fee"],
        "--slippage",
        values["slippage"],
        "--spread",
        values["spread"],
        "--volatility-threshold",
        values["volatility_threshold"],
        "--position-split-factor",
        values["position_split_factor"],
        "--optimize-method",
        values["optimize_method"],
        "--random-samples",
        values["random_samples"],
        "--strategy",
        values["strategy"],
        "--holding-period-bars",
        values["holding_period_bars"],
        "--max-trades-per-month",
        values["max_trades_per_month"],
        "--buy-threshold",
        values["buy_threshold"],
        "--sell-threshold",
        values["sell_threshold"],
        "--min-trade-confidence",
        values["min_trade_confidence"],
        "--pivot-lookback",
        values["pivot_lookback"],
        "--pivot-rebound-pct",
        values["pivot_rebound_pct"],
        "--pivot-pullback-pct",
        values["pivot_pullback_pct"],
        "--pivot-rsi-window",
        values["pivot_rsi_window"],
        "--pivot-rsi-low",
        values["pivot_rsi_low"],
        "--pivot-rsi-high",
        values["pivot_rsi_high"],
        "--pivot-ema-fast",
        values["pivot_ema_fast"],
        "--pivot-ema-slow",
        values["pivot_ema_slow"],
        "--pivot-adx-window",
        values["pivot_adx_window"],
        "--pivot-adx-threshold",
        values["pivot_adx_threshold"],
        "--pivot-atr-window",
        values["pivot_atr_window"],
        "--pivot-atr-pct-threshold",
        values["pivot_atr_pct_threshold"],
        "--pivot-split-count",
        values["pivot_split_count"],
    ]
    return cmd


def validate_values(values: dict) -> list[str]:
    errors = []
    if not split_tokens(values["symbols"]):
        errors.append("Symbols are required.")
    if not split_tokens(values["windows"]):
        errors.append("At least one window is required.")
    if not split_tokens(values["thresholds"]):
        errors.append("At least one threshold is required.")

    int_fields = [
        "limit",
        "random_samples",
        "holding_period_bars",
        "max_trades_per_month",
        "pivot_lookback",
        "pivot_rsi_window",
        "pivot_ema_fast",
        "pivot_ema_slow",
        "pivot_adx_window",
        "pivot_atr_window",
        "pivot_split_count",
    ]
    float_fields = [
        "fee",
        "slippage",
        "spread",
        "volatility_threshold",
        "position_split_factor",
        "buy_threshold",
        "sell_threshold",
        "min_trade_confidence",
        "pivot_rebound_pct",
        "pivot_pullback_pct",
        "pivot_rsi_low",
        "pivot_rsi_high",
        "pivot_adx_threshold",
        "pivot_atr_pct_threshold",
    ]
    for field in int_fields:
        try:
            int(values[field])
        except ValueError:
            errors.append(f"{field} must be an integer.")
    for field in float_fields:
        try:
            float(values[field])
        except ValueError:
            errors.append(f"{field} must be a number.")
    return errors


def render_page(values: dict, output: str = "", error: str = "", plots: list[str] | None = None) -> bytes:
    plots = plots or []
    def field_value(name: str) -> str:
        return html.escape(values.get(name, DEFAULTS[name]))

    def is_selected(name: str, value: str) -> str:
        return "selected" if values.get(name, DEFAULTS[name]) == value else ""

    output_html = html.escape(output)
    error_html = html.escape(error)
    plot_cards = []
    for plot in plots[:12]:
        plot_cards.append(
            f"""
            <figure class="plot-card">
              <img src="/plots/{html.escape(plot)}" alt="{html.escape(plot)}" />
              <figcaption>{html.escape(plot)}</figcaption>
            </figure>
            """
        )
    plots_html = "\n".join(plot_cards) or "<p class=\"muted\">No plots found yet.</p>"

    html_content = f"""
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Backtester Control Room</title>
    <style>
      :root {{
        --bg: #f4f0e8;
        --bg-accent: #f7c59f;
        --ink: #1e1b18;
        --ink-soft: #5e5348;
        --accent: #c45b00;
        --accent-2: #0f4c5c;
        --panel: #fffdfa;
        --border: #d6c7b2;
        --shadow: rgba(44, 31, 20, 0.15);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: "Space Grotesk", "IBM Plex Sans", "Segoe UI", sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top, rgba(196, 91, 0, 0.15), transparent 50%),
          radial-gradient(circle at 80% 20%, rgba(15, 76, 92, 0.2), transparent 45%),
          linear-gradient(135deg, var(--bg), #fff8f0 60%);
        min-height: 100vh;
      }}
      header {{
        padding: 32px 24px 12px;
        text-align: center;
        animation: slideDown 0.7s ease-out;
      }}
      header h1 {{
        font-size: clamp(2rem, 3vw, 3rem);
        margin: 0 0 8px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }}
      header p {{
        margin: 0;
        color: var(--ink-soft);
        font-size: 1rem;
      }}
      main {{
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 20px;
        padding: 16px 24px 40px;
      }}
      @media (min-width: 980px) {{
        main {{
          grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
          align-items: start;
        }}
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 20px;
        box-shadow: 0 12px 30px var(--shadow);
        animation: fadeUp 0.7s ease-out;
      }}
      .panel h2 {{
        margin: 0 0 16px;
        font-size: 1.2rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
      }}
      form {{
        display: grid;
        gap: 14px;
      }}
      .grid-2 {{
        display: grid;
        gap: 12px;
      }}
      @media (min-width: 700px) {{
        .grid-2 {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }}
      }}
      label {{
        display: block;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--ink-soft);
        margin-bottom: 6px;
      }}
      input, select, textarea {{
        width: 100%;
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: #fff;
        font-size: 0.95rem;
        font-family: inherit;
      }}
      textarea {{
        min-height: 120px;
        resize: vertical;
      }}
      .actions {{
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
      }}
      button {{
        background: var(--accent);
        color: white;
        border: none;
        border-radius: 999px;
        padding: 12px 22px;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }}
      button.secondary {{
        background: var(--accent-2);
      }}
      button:hover {{
        transform: translateY(-2px);
        box-shadow: 0 6px 14px rgba(196, 91, 0, 0.3);
      }}
      .muted {{
        color: var(--ink-soft);
      }}
      .output {{
        background: #12110f;
        color: #f0e7d8;
        border-radius: 12px;
        padding: 14px;
        font-family: "JetBrains Mono", "Fira Code", monospace;
        white-space: pre-wrap;
      }}
      .plot-grid {{
        display: grid;
        gap: 12px;
      }}
      @media (min-width: 600px) {{
        .plot-grid {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }}
      }}
      .plot-card {{
        background: #fff7ef;
        border-radius: 12px;
        border: 1px solid var(--border);
        overflow: hidden;
      }}
      .plot-card img {{
        width: 100%;
        display: block;
        cursor: zoom-in;
      }}
      .plot-card figcaption {{
        padding: 8px 10px;
        font-size: 0.8rem;
        color: var(--ink-soft);
      }}
      .lightbox {{
        position: fixed;
        inset: 0;
        background: rgba(18, 16, 14, 0.85);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 1000;
        padding: 24px;
      }}
      .lightbox.open {{
        display: flex;
      }}
      .lightbox-panel {{
        background: #fffaf3;
        border-radius: 16px;
        padding: 16px;
        max-width: min(90vw, 1100px);
        max-height: 90vh;
        display: grid;
        gap: 12px;
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.3);
      }}
      .lightbox-controls {{
        display: flex;
        gap: 8px;
        justify-content: flex-end;
      }}
      .lightbox-controls button {{
        padding: 8px 14px;
        font-size: 0.85rem;
      }}
      .lightbox-image-wrap {{
        overflow: auto;
        max-height: 70vh;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: #fff;
      }}
      .lightbox-image {{
        display: block;
        transform-origin: center center;
      }}
      .error {{
        background: #ffe3dd;
        color: #8c2c1c;
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid #f3b8aa;
      }}
      .tooltip {{
        position: fixed;
        background: #1e1b18;
        color: #f7f2e9;
        padding: 8px 10px;
        border-radius: 8px;
        font-size: 0.8rem;
        max-width: 240px;
        pointer-events: none;
        opacity: 0;
        transform: translateY(6px);
        transition: opacity 0.12s ease, transform 0.12s ease;
        z-index: 1100;
      }}
      .tooltip.show {{
        opacity: 1;
        transform: translateY(0);
      }}
      @keyframes fadeUp {{
        from {{ opacity: 0; transform: translateY(16px); }}
        to {{ opacity: 1; transform: translateY(0); }}
      }}
      @keyframes slideDown {{
        from {{ opacity: 0; transform: translateY(-10px); }}
        to {{ opacity: 1; transform: translateY(0); }}
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>Backtester Control Room</h1>
      <p>Configure arguments for <code>backtester.py</code> and inspect results.</p>
    </header>
    <main>
      <section class="panel">
        <h2>Run Setup</h2>
        {"<div class='error'>" + error_html + "</div>" if error else ""}
        <form method="post" action="/run">
          <div data-group="common">
            <label>Symbols (comma or space separated)</label>
            <input name="symbols" value="{field_value("symbols")}" data-help="Symbols to backtest, e.g., BTC/USDT ETH/USDT." />
          </div>
          <div class="grid-2" data-group="common">
            <div>
              <label>Timeframe</label>
              <input name="timeframe" value="{field_value("timeframe")}" data-help="Candle timeframe: 1h, 4h, 1d, etc." />
            </div>
            <div>
              <label>Limit</label>
              <input name="limit" value="{field_value("limit")}" data-help="Number of candles to fetch per symbol." />
            </div>
          </div>
          <div class="grid-2" data-group="mean_reversion">
            <div>
              <label>Windows</label>
              <input name="windows" value="{field_value("windows")}" data-help="Moving average window sizes to test." />
            </div>
            <div>
              <label>Thresholds</label>
              <input name="thresholds" value="{field_value("thresholds")}" data-help="Deviation thresholds like 0.03 for 3%." />
            </div>
          </div>
          <div class="grid-2" data-group="mean_reversion">
            <div>
              <label>Optimize Method</label>
              <select name="optimize_method" data-help="Grid tests all combos; random samples combos.">
                <option value="grid" {is_selected("optimize_method", "grid")}>Grid</option>
                <option value="random" {is_selected("optimize_method", "random")}>Random</option>
              </select>
            </div>
            <div>
              <label>Random Samples</label>
              <input name="random_samples" value="{field_value("random_samples")}" data-help="How many random parameter combos to test." />
            </div>
          </div>
          <div class="grid-2" data-group="common">
            <div>
              <label>Strategy</label>
              <select name="strategy" id="strategy" data-help="Pick mean reversion, ML pattern, or pivot reversal strategy.">
                <option value="mean_reversion" {is_selected("strategy", "mean_reversion")}>Mean Reversion</option>
                <option value="ml" {is_selected("strategy", "ml")}>ML Pattern</option>
                <option value="pivot_reversal" {is_selected("strategy", "pivot_reversal")}>Pivot Reversal</option>
              </select>
            </div>
          </div>
          <div class="grid-2" data-group="ml">
            <div>
              <label>Holding Period Bars</label>
              <input name="holding_period_bars" value="{field_value("holding_period_bars")}" data-help="Number of bars to hold before forcing exit." />
            </div>
            <div>
              <label>Max Trades / Month</label>
              <input name="max_trades_per_month" value="{field_value("max_trades_per_month")}" data-help="Throttle new entries per rolling 30 days." />
            </div>
          </div>
          <div class="grid-2" data-group="ml">
            <div>
              <label>Buy Threshold</label>
              <input name="buy_threshold" value="{field_value("buy_threshold")}" data-help="Label buy when future return exceeds this." />
            </div>
            <div>
              <label>Sell Threshold</label>
              <input name="sell_threshold" value="{field_value("sell_threshold")}" data-help="Label sell when future return is below this." />
            </div>
          </div>
          <div class="grid-2" data-group="ml">
            <div>
              <label>Min Trade Confidence</label>
              <input name="min_trade_confidence" value="{field_value("min_trade_confidence")}" data-help="Minimum model probability to enter/exit trades." />
            </div>
          </div>
          <div class="grid-2" data-group="pivot_reversal">
            <div>
              <label>Pivot Lookback</label>
              <input name="pivot_lookback" value="{field_value("pivot_lookback")}" data-help="Rolling lookback window to detect recent highs/lows." />
            </div>
            <div>
              <label>Split Count</label>
              <input name="pivot_split_count" value="{field_value("pivot_split_count")}" data-help="Number of split entries/exits for scaling in/out." />
            </div>
          </div>
          <div class="grid-2" data-group="pivot_reversal">
            <div>
              <label>Rebound %</label>
              <input name="pivot_rebound_pct" value="{field_value("pivot_rebound_pct")}" data-help="Percent rebound from rolling low to trigger buys." />
            </div>
            <div>
              <label>Pullback %</label>
              <input name="pivot_pullback_pct" value="{field_value("pivot_pullback_pct")}" data-help="Percent pullback from rolling high to trigger sells." />
            </div>
          </div>
          <div class="grid-2" data-group="pivot_reversal">
            <div>
              <label>RSI Window</label>
              <input name="pivot_rsi_window" value="{field_value("pivot_rsi_window")}" data-help="RSI window for reversal timing." />
            </div>
            <div>
              <label>RSI Low / High</label>
              <input name="pivot_rsi_low" value="{field_value("pivot_rsi_low")}" data-help="RSI oversold threshold for buys." />
              <input name="pivot_rsi_high" value="{field_value("pivot_rsi_high")}" data-help="RSI overbought threshold for sells." style="margin-top: 8px;" />
            </div>
          </div>
          <div class="grid-2" data-group="pivot_reversal">
            <div>
              <label>EMA Fast / Slow</label>
              <input name="pivot_ema_fast" value="{field_value("pivot_ema_fast")}" data-help="Fast EMA window for momentum confirmation." />
              <input name="pivot_ema_slow" value="{field_value("pivot_ema_slow")}" data-help="Slow EMA window for momentum confirmation." style="margin-top: 8px;" />
            </div>
            <div>
              <label>Sideways Filter</label>
              <input name="pivot_adx_threshold" value="{field_value("pivot_adx_threshold")}" data-help="ADX below this is considered sideways." />
              <input name="pivot_atr_pct_threshold" value="{field_value("pivot_atr_pct_threshold")}" data-help="ATR%% below this is considered sideways." style="margin-top: 8px;" />
            </div>
          </div>
          <div class="grid-2" data-group="pivot_reversal">
            <div>
              <label>ADX Window</label>
              <input name="pivot_adx_window" value="{field_value("pivot_adx_window")}" data-help="ADX window for sideways detection." />
            </div>
            <div>
              <label>ATR Window</label>
              <input name="pivot_atr_window" value="{field_value("pivot_atr_window")}" data-help="ATR window for sideways detection." />
            </div>
          </div>
          <div class="grid-2" data-group="common">
            <div>
              <label>Fee</label>
              <input name="fee" value="{field_value("fee")}" data-help="Commission per trade (0.001 = 0.1%)." />
            </div>
            <div>
              <label>Slippage</label>
              <input name="slippage" value="{field_value("slippage")}" data-help="Slippage factor applied to trade price." />
            </div>
          </div>
          <div class="grid-2" data-group="common">
            <div>
              <label>Spread</label>
              <input name="spread" value="{field_value("spread")}" data-help="Bid/ask spread factor." />
            </div>
          </div>
          <div class="grid-2" data-group="mean_reversion">
            <div>
              <label>Volatility Threshold</label>
              <input name="volatility_threshold" value="{field_value("volatility_threshold")}" data-help="Trigger partial sizing when volatility exceeds this." />
            </div>
          </div>
          <div data-group="mean_reversion">
            <label>Position Split Factor</label>
            <input name="position_split_factor" value="{field_value("position_split_factor")}" data-help="Fraction of capital/position to trade in high volatility." />
          </div>
          <div class="actions">
            <button type="submit">Run Backtest</button>
            <a href="/" class="muted">Clear output</a>
          </div>
        </form>
      </section>
      <section class="panel">
        <h2>Results</h2>
        <p class="muted">Latest stdout from <code>backtester.py</code>.</p>
        <div class="output">{output_html or "No run yet."}</div>
        <h2 style="margin-top: 24px;">Plots</h2>
        <div class="plot-grid">{plots_html}</div>
      </section>
    </main>
    <div class="lightbox" id="lightbox">
      <div class="lightbox-panel">
        <div class="lightbox-controls">
          <button type="button" id="zoom-out">Zoom -</button>
          <button type="button" id="zoom-reset" class="secondary">Reset</button>
          <button type="button" id="zoom-in">Zoom +</button>
        </div>
        <div class="lightbox-image-wrap">
          <img id="lightbox-image" class="lightbox-image" src="" alt="Plot preview" />
        </div>
      </div>
    </div>
    <div class="tooltip" id="tooltip" role="tooltip"></div>
    <script>
      const lightbox = document.getElementById("lightbox");
      const lightboxImage = document.getElementById("lightbox-image");
      const zoomIn = document.getElementById("zoom-in");
      const zoomOut = document.getElementById("zoom-out");
      const zoomReset = document.getElementById("zoom-reset");
      let zoomLevel = 1;

      function applyZoom() {{
        lightboxImage.style.transform = "scale(" + zoomLevel + ")";
      }}
      function openLightbox(src, alt) {{
        zoomLevel = 1;
        lightboxImage.src = src;
        lightboxImage.alt = alt || "Plot";
        applyZoom();
        lightbox.classList.add("open");
      }}
      function closeLightbox() {{
        lightbox.classList.remove("open");
      }}
      document.querySelectorAll(".plot-card img").forEach((img) => {{
        img.addEventListener("click", () => openLightbox(img.src, img.alt));
      }});
      lightbox.addEventListener("click", (event) => {{
        if (event.target === lightbox) {{
          closeLightbox();
        }}
      }});
      document.addEventListener("keydown", (event) => {{
        if (event.key === "Escape") {{
          closeLightbox();
        }}
      }});
      zoomIn.addEventListener("click", () => {{
        zoomLevel = Math.min(zoomLevel + 0.2, 3);
        applyZoom();
      }});
      zoomOut.addEventListener("click", () => {{
        zoomLevel = Math.max(zoomLevel - 0.2, 0.4);
        applyZoom();
      }});
      zoomReset.addEventListener("click", () => {{
        zoomLevel = 1;
        applyZoom();
      }});
      lightboxImage.addEventListener("wheel", (event) => {{
        event.preventDefault();
        const delta = Math.sign(event.deltaY);
        zoomLevel = delta > 0 ? Math.max(zoomLevel - 0.1, 0.4) : Math.min(zoomLevel + 0.1, 3);
        applyZoom();
      }}, {{ passive: false }});

      const strategy = document.getElementById("strategy");
      function toggleStrategyFields() {{
        const mode = strategy.value;
        document.querySelectorAll("[data-group='ml']").forEach((el) => {{
          el.style.display = mode === "ml" ? "" : "none";
        }});
        document.querySelectorAll("[data-group='mean_reversion']").forEach((el) => {{
          el.style.display = mode === "mean_reversion" ? "" : "none";
        }});
        document.querySelectorAll("[data-group='pivot_reversal']").forEach((el) => {{
          el.style.display = mode === "pivot_reversal" ? "" : "none";
        }});
      }}
      strategy.addEventListener("change", toggleStrategyFields);
      toggleStrategyFields();

      const tooltip = document.getElementById("tooltip");
      function positionTooltip(event) {{
        const padding = 12;
        const rect = tooltip.getBoundingClientRect();
        let left = event.clientX + 12;
        let top = event.clientY + 12;
        if (left + rect.width + padding > window.innerWidth) {{
          left = event.clientX - rect.width - 12;
        }}
        if (top + rect.height + padding > window.innerHeight) {{
          top = event.clientY - rect.height - 12;
        }}
        tooltip.style.left = left + "px";
        tooltip.style.top = top + "px";
      }}
      function showTooltip(event, text) {{
        tooltip.textContent = text;
        tooltip.classList.add("show");
        positionTooltip(event);
      }}
      function hideTooltip() {{
        tooltip.classList.remove("show");
      }}
      document.querySelectorAll("[data-help]").forEach((el) => {{
        el.addEventListener("mouseenter", (event) => showTooltip(event, el.dataset.help));
        el.addEventListener("mousemove", positionTooltip);
        el.addEventListener("mouseleave", hideTooltip);
      }});
    </script>
  </body>
</html>
"""
    return html_content.encode("utf-8")


class BacktesterHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/plots/"):
            self.serve_plot(parsed.path)
            return
        values = DEFAULTS.copy()
        self.respond(render_page(values))

    def do_POST(self):
        if self.path != "/run":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        data = parse_qs(body)
        values = DEFAULTS.copy()
        for key in DEFAULTS:
            if key in data:
                values[key] = data[key][0]

        errors = validate_values(values)
        if errors:
            self.respond(render_page(values, error=" ".join(errors)))
            return

        before = set(list_plot_files())
        cmd = build_command(values)
        proc = subprocess.run(
            cmd,
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
        )
        output = proc.stdout.strip() or proc.stderr.strip()
        if proc.returncode != 0:
            output = f"Command failed with exit code {proc.returncode}.\n\n{output}"
        after = list_plot_files()
        new_plots = [f for f in after if f not in before]
        plots = new_plots or after
        self.respond(render_page(values, output=output, plots=plots))

    def serve_plot(self, path: str) -> None:
        plot_name = path.replace("/plots/", "", 1)
        plot_path = os.path.join(PLOTS_DIR, plot_name)
        if not os.path.isfile(plot_path):
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(plot_path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        with open(plot_path, "rb") as f:
            self.wfile.write(f.read())

    def respond(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI for backtester.py")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument(
        "--shutdown-after",
        type=int,
        default=0,
        help="Auto-shutdown after N seconds (default: 0 = no auto-shutdown)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        server = HTTPServer((args.host, args.port), BacktesterHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {args.port} is already in use. Try a different --port.")
            sys.exit(1)
        raise
    if args.shutdown_after > 0:
        import threading

        timer = threading.Timer(args.shutdown_after, server.shutdown)
        timer.daemon = True
        timer.start()
    print(f"Backtester UI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")


if __name__ == "__main__":
    main()
