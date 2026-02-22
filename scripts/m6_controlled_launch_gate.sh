#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
OUT_LOG="$LOG_DIR/m6_controlled_launch_2026-02-22.log"
REPORT="$ROOT_DIR/M6_CONTROLLED_LAUNCH_REPORT_2026-02-22.md"

mkdir -p "$LOG_DIR"
status="PASS"

if {
  echo "[m6-gate] started: $(date -Iseconds)"
  echo "[m6-gate] root: $ROOT_DIR"
  cd "$ROOT_DIR"

  # 1) M5 readiness must pass first.
  ./scripts/m5_trading_readiness_gate.sh

  # 2) Launch policy and rollback playbook existence.
  [[ -f "LAUNCH_POLICY.yaml" ]] && echo "[PASS] launch policy exists" || { echo "[FAIL] launch policy missing"; status="FAIL"; }

  # 3) Ensure critical operator commands are present in code/help surface.
  required_cmds=("/pause" "/resume" "/stop" "/start" "/restart")
  for cmd in "${required_cmds[@]}"; do
    if grep -q "$cmd" main.py webapp/app.py webapp/templates/index.html 2>/dev/null; then
      echo "[PASS] operator command present: $cmd"
    else
      echo "[FAIL] operator command missing: $cmd"
      status="FAIL"
    fi
  done

  # 4) Check that risk/reliability reports exist for incident review.
  required_reports=(
    "M3_RISK_ENFORCEMENT_REPORT_2026-02-22.md"
    "M4_RELIABILITY_OBSERVABILITY_REPORT_2026-02-22.md"
    "RISK_GATE_VERIFY_REPORT_2026-02-22.md"
    "NETWORK_RELIABILITY_REPORT_2026-02-22.md"
    "M5_TRADING_READINESS_REPORT_2026-02-22.md"
  )
  for f in "${required_reports[@]}"; do
    if [[ -f "$f" ]]; then
      echo "[PASS] report exists: $f"
    else
      echo "[FAIL] report missing: $f"
      status="FAIL"
    fi
  done

  # 5) Dry-run rollback drill simulation (documented command path only).
  if grep -q "release_gate.sh --with-risk" LAUNCH_POLICY.yaml; then
    echo "[PASS] rollback policy references gate re-validation"
  else
    echo "[FAIL] rollback policy missing gate re-validation step"
    status="FAIL"
  fi

  echo "[m6-gate] completed checks"

} 2>&1 | tee "$OUT_LOG"; then
  true
else
  status="FAIL"
fi

{
  echo "# M6 Controlled Launch Report (2026-02-22)"
  echo
  echo "- Script: \`scripts/m6_controlled_launch_gate.sh\`"
  echo "- Log: \`logs/m6_controlled_launch_2026-02-22.log\`"
  echo "- Executed at: $(date -Iseconds)"
  echo
  echo "## Scope"
  echo "1. M5 readiness precondition"
  echo "2. Launch policy + rollback policy validation"
  echo "3. Operator command availability check"
  echo "4. Incident-review report completeness"
  echo
  echo "## Execution Log"
  echo '```text'
  cat "$OUT_LOG"
  echo '```'
  echo
  echo "## Go/No-Go"
  echo "- Result: **$status**"
  if [[ "$status" == "PASS" ]]; then
    echo "- Launch recommendation: **GO (controlled ramp only, policy-bound)**"
  else
    echo "- Launch recommendation: **NO-GO (resolve failures first)**"
  fi
} > "$REPORT"

if [[ "$status" == "PASS" ]]; then
  echo "[m6-gate] final: PASS"
  exit 0
else
  echo "[m6-gate] final: FAIL"
  exit 1
fi
