#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

HOST="${BTB_WEB_HOST:-127.0.0.1}"
PORT="${BTB_WEB_PORT:-8080}"

uvicorn webapp.app:app --host "$HOST" --port "$PORT"
