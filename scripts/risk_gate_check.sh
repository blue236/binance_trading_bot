#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
OUT_LOG="$LOG_DIR/risk_gate_2026-02-22.log"
REPORT="$ROOT_DIR/RISK_GATE_VERIFY_REPORT_2026-02-22.md"

mkdir -p "$LOG_DIR"
status="PASS"

{
  echo "[risk-gate] started: $(date -Iseconds)"
  echo "[risk-gate] root: $ROOT_DIR"
  cd "$ROOT_DIR"

  if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
  else
    PY="python3"
  fi

  set +e
  "$PY" - <<'PY'
import os
import tempfile
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

# 1) pre-trade risk gate function exists
fn_idx = source.find("def evaluate_pretrade_risk_gate(")
check("Mandatory pre-trade risk gate function exists", fn_idx != -1, f"fn_idx={fn_idx}")

# 2) entry loop calls risk gate before trading decisions
entry_idx = source.find("for symbol in symbols:")
call_idx = source.find("evaluate_pretrade_risk_gate(", entry_idx if entry_idx != -1 else 0)
check(
    "Entry loop evaluates risk gate before entry",
    entry_idx != -1 and call_idx != -1 and call_idx > entry_idx,
    f"entry_idx={entry_idx}, call_idx={call_idx}",
)

# 3) reject path audit event exists
reject_idx = source.find("RISK_GATE_REJECT")
check("Risk-gate reject audit event exists", reject_idx != -1, f"reject_idx={reject_idx}")

# 4) owner-only command behavioral check
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
raise SystemExit(0 if fail_count == 0 else 1)
PY
  py_rc=$?
  set -e

  if [[ $py_rc -eq 0 ]]; then
    echo "[risk-gate] checks: PASS"
  else
    echo "[risk-gate] checks: FAIL"
    status="FAIL"
  fi

} 2>&1 | tee "$OUT_LOG"

{
  echo "# Risk Gate Verify Report (2026-02-22)"
  echo
  echo "- Script: \`scripts/risk_gate_check.sh\`"
  echo "- Log: \`logs/risk_gate_2026-02-22.log\`"
  echo "- Executed at: $(date -Iseconds)"
  echo
  echo "## Scope"
  echo "1. Mandatory pre-trade risk gate presence/usage"
  echo "2. Reject audit logging consistency"
  echo "3. owner-only command(/setrisk) non-owner block"
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
  echo "- 실거래소 연동 E2E(네트워크/슬리피지 포함)는 별도 시나리오로 추가 검증 필요"
} > "$REPORT"

if [[ "$status" == "PASS" ]]; then
  echo "[risk-gate] completed: PASS"
  exit 0
else
  echo "[risk-gate] completed: FAIL"
  exit 1
fi
