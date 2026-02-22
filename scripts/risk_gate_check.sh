#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
OUT_LOG="$LOG_DIR/risk_gate_2026-02-22.log"
REPORT="$ROOT_DIR/RISK_GATE_VERIFY_REPORT_2026-02-22.md"

mkdir -p "$LOG_DIR"
status="PASS"

if {
  echo "[risk-gate] started: $(date -Iseconds)"
  echo "[risk-gate] root: $ROOT_DIR"
  cd "$ROOT_DIR"

  if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
  else
    PY="python3"
  fi

  "$PY" - <<'PY'
import re
import tempfile
import os
from pathlib import Path

pass_count = 0
fail_count = 0


def check(name, ok, detail=""):
    global pass_count, fail_count
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name} :: {detail}")
    if ok:
        pass_count += 1
    else:
        fail_count += 1

source = Path("main.py").read_text(encoding="utf-8")

# 1) pause 상태에서 진입 차단 (guard + continue ordering)
pause_idx = source.find('if state.get("bot_paused", False):')
entry_idx = source.find('# entry checks')
continue_near = source.find('continue', pause_idx, entry_idx if entry_idx != -1 else None)
check(
    "Pause guard exists before entry checks",
    pause_idx != -1 and entry_idx != -1 and pause_idx < entry_idx and continue_near != -1,
    f"pause_idx={pause_idx}, entry_idx={entry_idx}, continue_idx={continue_near}",
)

# 2) max position 초과 시 신규 진입 차단 (allow_entries gate + break)
allow_idx = source.find('allow_entries = len(state["positions"]) < cfg["risk"]["max_concurrent_positions"]')
break_idx = source.find('if not allow_entries:\n                    break')
check(
    "Max position gate exists (allow_entries + break)",
    allow_idx != -1 and break_idx != -1 and allow_idx < break_idx,
    f"allow_idx={allow_idx}, break_idx={break_idx}",
)

# 3) owner-only 명령 비소유자 차단 (behavioral mock: /setrisk)
import main

cfg = {
    'general': {'symbols': ['BTC/USDT'], 'aggressive_mode': False, 'dry_run': True},
    'risk': {'per_trade_risk_pct': 0.5, 'daily_loss_stop_pct': 3.0, 'max_concurrent_positions': 2, 'cooldown_hours': 8},
    'alerts': {'enable_trade_approval': True, 'telegram_owner_user_id': '42'},
    'aggressive': {'risk': {'per_trade_risk_pct': 1.2, 'max_concurrent_positions': 4, 'cooldown_hours': 2}},
    'logging': {'csv_dir': './logs', 'tz': 'Europe/Berlin'}
}
state = {'positions': {}, 'bot_paused': False}

tmp = tempfile.NamedTemporaryFile(delete=False)
state_path = tmp.name
tmp.close()

sent = []
updates = []


def fake_send(_tg, msg):
    sent.append(msg)


def fake_post(_token, method, _params):
    if method == 'getUpdates':
        return {'result': updates.copy()}
    return {'ok': True, 'result': {}}


main.send_telegram = fake_send
main._tg_post = fake_post

tg = {'token': 'x', 'chat_id': '123'}
updates.append({
    'update_id': 1,
    'message': {
        'chat': {'id': '123'},
        'text': '/setrisk 0.4',
        'from': {'id': '99', 'username': 'not_owner'}
    }
})
main.poll_telegram_commands(tg, None, cfg, state, 1000.0, 'USDT', state_path)

msg = sent[-1] if sent else ''
check("Owner-only command denies non-owner /setrisk", 'Owner-only command.' in msg, msg)
check("Non-owner /setrisk does not create pending change", not bool(state.get('pending_change')), str(state.get('pending_change')))

try:
    os.unlink(state_path)
except Exception:
    pass

print(f"TOTAL: pass={pass_count}, fail={fail_count}")
print("RESULT:", "PASS" if fail_count == 0 else "FAIL")

if fail_count:
    raise SystemExit(1)
PY

  echo "[risk-gate] checks: PASS"
} 2>&1 | tee "$OUT_LOG"; then
  status="PASS"
else
  status="FAIL"
fi

{
  echo "# Risk Gate Verify Report (2026-02-22)"
  echo
  echo "- Script: \`scripts/risk_gate_check.sh\`"
  echo "- Log: \`logs/risk_gate_2026-02-22.log\`"
  echo "- Executed at: $(date -Iseconds)"
  echo
  echo "## Scope"
  echo "1. Max position 초과 시 신규 진입 차단"
  echo "2. pause 상태에서 진입 차단"
  echo "3. owner-only 명령(/setrisk) 비소유자 차단"
  echo
  echo "## Execution Log"
  echo '```text'
  cat "$OUT_LOG"
  echo '```'
  echo
  echo "## Gate Decision"
  echo "- Result: **$status**"
  echo
  echo "## Remaining Risks"
  echo "- max position/pause는 소스 가드+흐름 검증 중심이며, 거래소 연동 포함 E2E는 별도 필요"
} > "$REPORT"

if [[ "$status" == "PASS" ]]; then
  echo "[risk-gate] completed: PASS"
  exit 0
else
  echo "[risk-gate] completed: FAIL"
  exit 1
fi
