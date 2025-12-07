#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi
if [[ -n "${DISPLAY-}" ]]; then
  echo "DISPLAY detected -> launching GUI (gui.py)"
  exec python gui.py
else
  echo "No DISPLAY -> launching headless mode (main.py)"
  exec python main.py
fi
