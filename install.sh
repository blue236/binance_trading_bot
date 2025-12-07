#!/usr/bin/env bash
set -euo pipefail
PKG_DIR="binance_spot_gui_bot"
ZIP_NAME="binance_spot_gui_bot.zip"
echo "==> Binance Spot Auto-Trader (WSL Ubuntu) installer"
cd "$(dirname "$0")"
if [[ -f "$ZIP_NAME" && ! -d "$PKG_DIR" ]]; then
  echo "==> Unzipping package..."
  unzip -o "$ZIP_NAME"
fi
if [[ -d "$PKG_DIR" ]]; then
  cd "$PKG_DIR"
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Run: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi
if [[ ! -d ".venv" ]]; then
  echo "==> Creating Python venv (.venv)"
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
chmod +x run.sh || true
if [[ -n "${DISPLAY-}" ]]; then
  echo "GUI detected. Start with:"
  echo "  source .venv/bin/activate && python gui.py"
else
  echo "No GUI (DISPLAY unset). Start headless:"
  echo "  source .venv/bin/activate && python main.py"
fi
