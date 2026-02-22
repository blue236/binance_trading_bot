#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
OUT_LOG="$LOG_DIR/release_gate_2026-02-22.log"
REPORT="$ROOT_DIR/VERIFICATION_REPORT_AUTOGATE_2026-02-22.md"
RUN_RISK=0

for arg in "$@"; do
  case "$arg" in
    --with-risk) RUN_RISK=1 ;;
  esac
done

mkdir -p "$LOG_DIR"

status="PASS"
if {
  echo "[release-gate] started: $(date -Iseconds)"
  echo "[release-gate] root: $ROOT_DIR"

  cd "$ROOT_DIR"

  if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
  else
    PY="python3"
  fi

  "$PY" - <<'PY'
import os
import re
import sys
import tempfile
from datetime import datetime

pass_count = 0
fail_count = 0
lines = []


def check(name, ok, detail=""):
    global pass_count, fail_count
    status = "PASS" if ok else "FAIL"
    lines.append(f"[{status}] {name} :: {detail}")
    if ok:
        pass_count += 1
    else:
        fail_count += 1

# ---------- Auth regression ----------
os.environ['BTB_WEB_AUTH_ENABLED'] = '1'
os.environ['BTB_WEB_USERNAME'] = 'admin'
os.environ['BTB_WEB_PASSWORD'] = 'pass123'
os.environ['BTB_WEB_SESSION_SECRET'] = 'release-gate-secret'
os.environ['BTB_WEB_COOKIE_SECURE'] = '1'

from fastapi.testclient import TestClient
from webapp.app import app, _make_session_token, _session_cookie_name

client = TestClient(app)

r = client.get('/', allow_redirects=False)
check("Auth unauth GET / redirects to /login", r.status_code == 302 and r.headers.get('location') == '/login', f"status={r.status_code}, location={r.headers.get('location')}")

r = client.get('/api/health', allow_redirects=False)
body_ok = False
try:
    j = r.json()
    body_ok = (j.get('ok') is False and j.get('error') == 'unauthorized')
except Exception:
    body_ok = False
check("Auth unauth GET /api/health returns 401", r.status_code == 401 and body_ok, f"status={r.status_code}")

r = client.post('/login', data={'username': 'admin', 'password': 'pass123'}, allow_redirects=False)
set_cookie = r.headers.get('set-cookie', '')
cookie_flags_ok = ('HttpOnly' in set_cookie and 'SameSite=lax' in set_cookie and 'Secure' in set_cookie)
check("Auth login success sets secure session cookie", r.status_code == 302 and cookie_flags_ok, f"status={r.status_code}")

cookie = {_session_cookie_name(): _make_session_token('admin')}
r = client.get('/api/health', cookies=cookie, allow_redirects=False)
health_ok = False
try:
    health_ok = (r.json().get('ok') is True)
except Exception:
    health_ok = False
check("Auth authenticated GET /api/health returns 200", r.status_code == 200 and health_ok, f"status={r.status_code}")

# ---------- Telegram command regression ----------
import main

cfg = {
    'general': {'symbols': ['BTC/USDT'], 'aggressive_mode': False, 'dry_run': True},
    'risk': {'per_trade_risk_pct': 0.5, 'daily_loss_stop_pct': 3.0, 'max_concurrent_positions': 2, 'cooldown_hours': 8},
    'alerts': {'enable_trade_approval': True, 'telegram_owner_user_id': '42'},
    'aggressive': {'risk': {'per_trade_risk_pct': 1.2, 'max_concurrent_positions': 4, 'cooldown_hours': 2}},
    'logging': {'csv_dir': './logs', 'tz': 'Europe/Berlin'}
}
state = {'positions': {'BTC/USDT': {'qty': 0.1, 'entry_price': 100, 'sl': 95}}, 'bot_paused': False}

fh = tempfile.NamedTemporaryFile(delete=False)
state_path = fh.name
fh.close()

sent = []
updates = []
restart_called = []


def fake_send(_tg, msg):
    sent.append(msg)


def fake_post(_token, method, _params):
    if method == 'getUpdates':
        return {'result': updates.copy()}
    return {'ok': True, 'result': {}}


def fake_execv(_exe, _args):
    restart_called.append(True)
    raise RuntimeError('RESTART_CALLED')


main.send_telegram = fake_send
main._tg_post = fake_post
main.os.execv = fake_execv

tg = {'token': 'x', 'chat_id': '123'}


def run_cmd(text, uid='42'):
    updates.clear()
    sent.clear()
    updates.append({
        'update_id': 1,
        'message': {
            'chat': {'id': '123'},
            'text': text,
            'from': {'id': uid, 'username': 'tester'}
        }
    })
    try:
        main.poll_telegram_commands(tg, None, cfg, state, 1000.0, 'USDT', state_path)
        return 'OK', (sent[-1] if sent else '')
    except RuntimeError as e:
        return str(e), (sent[-1] if sent else '')

st, msg = run_cmd('/summary', uid='42')
check("TG /summary response", st == 'OK' and msg.startswith('📌 Summary'), msg[:120])

st, msg = run_cmd('/health', uid='42')
check("TG /health response", st == 'OK' and msg.startswith('🩺 Health'), msg[:120])

st, msg = run_cmd('/restart', uid='99')
check("TG /restart non-owner denied", st == 'OK' and 'Owner-only command.' in msg, msg)

st, msg = run_cmd('/restart', uid='42')
tok_match = re.search(r'/confirm\s+([0-9a-f]+)', msg or '')
check("TG /restart owner issues confirm token", st == 'OK' and tok_match is not None, msg)

if tok_match is not None:
    token = tok_match.group(1)
    st, msg = run_cmd(f'/confirm {token}', uid='42')
    check("TG /confirm restart executes restart path", st == 'RESTART_CALLED' and 'Restarting bot process' in msg and bool(restart_called), msg)
else:
    check("TG /confirm restart executes restart path", False, 'token missing')

try:
    os.unlink(state_path)
except Exception:
    pass

print("\n=== RELEASE GATE SUMMARY ===")
for ln in lines:
    print(ln)
print(f"TOTAL: pass={pass_count}, fail={fail_count}")

report_status = "PASS" if fail_count == 0 else "FAIL"
print(f"RESULT: {report_status}")

if fail_count != 0:
    sys.exit(1)
PY

  echo "[release-gate] python checks: PASS"

} 2>&1 | tee "$OUT_LOG"; then
  status="PASS"
else
  status="FAIL"
fi

# report generation
{
  echo "# Verification Report — Auto Gate (2026-02-22)"
  echo
  echo '- Script: `scripts/release_gate.sh`'
  echo '- Log: `logs/release_gate_2026-02-22.log`'
  echo "- Executed at: $(date -Iseconds)"
  echo
  echo "## Scope"
  echo "1. Auth gate regression"
  echo "2. Telegram extended command regression (/summary, /health, /restart confirm flow)"
  echo
  echo "## Execution Log (excerpt)"
  echo '```text'
  tail -n 80 "$OUT_LOG"
  echo '```'
  echo
  echo "## Gate Decision"
  echo "- Result: **$status**"
  echo
  echo "## Remaining Risks"
  echo "- HTTPS redirect/HSTS는 앱 외부 프록시 환경에서 별도 검증 필요"
  echo "- 모킹 기반 Telegram 검증으로, 실 Telegram API 연동 E2E는 별도 수행 필요"
} > "$REPORT"

if [[ "$RUN_RISK" == "1" ]]; then
  echo "[release-gate] running optional risk gate (--with-risk)"
  if ! "$ROOT_DIR/scripts/risk_gate_check.sh"; then
    status="FAIL"
  fi
fi

if [[ "$status" == "PASS" ]]; then
  echo "[release-gate] completed: PASS"
  exit 0
else
  echo "[release-gate] completed: FAIL"
  exit 1
fi
