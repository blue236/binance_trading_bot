#!/usr/bin/env bash
set -euo pipefail

# Prefer python3.12 — pinned dependencies (pandas 2.2.2, numpy 1.26.4) require
# Python <=3.12 and have no pre-built wheels for Python 3.13+.
if command -v python3.12 &>/dev/null; then
  PYTHON=python3.12
elif python3 --version 2>&1 | grep -qE '^Python 3\.(9|10|11|12)\.'; then
  PYTHON=python3
else
  echo "ERROR: Python 3.9–3.12 is required. Found: $(python3 --version 2>&1)" >&2
  exit 1
fi

"$PYTHON" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${BTB_WEB_HOST:-127.0.0.1}"
PORT="${BTB_WEB_PORT:-8080}"

uvicorn webapp.app:app --host "$HOST" --port "$PORT"
