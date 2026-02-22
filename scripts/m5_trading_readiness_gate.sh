#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
OUT_LOG="$LOG_DIR/m5_trading_readiness_2026-02-22.log"
REPORT="$ROOT_DIR/M5_TRADING_READINESS_REPORT_2026-02-22.md"

mkdir -p "$LOG_DIR"
status="PASS"

if {
  echo "[m5-gate] started: $(date -Iseconds)"
  echo "[m5-gate] root: $ROOT_DIR"

  cd "$ROOT_DIR"

  # 1) Core release/risk gates must pass.
  ./scripts/release_gate.sh --with-risk

  # 2) Required capability artifacts must exist.
  required_files=(
    "M2_ARCH_CONVERGENCE_REPORT_2026-02-22.md"
    "M3_RISK_ENFORCEMENT_REPORT_2026-02-22.md"
    "M4_RELIABILITY_OBSERVABILITY_REPORT_2026-02-22.md"
    "STRATEGY_MARKER_BACKEND_REPORT_2026-02-22.md"
    "RISK_GATE_VERIFY_REPORT_2026-02-22.md"
    "NETWORK_RELIABILITY_REPORT_2026-02-22.md"
  )
  for f in "${required_files[@]}"; do
    if [[ -f "$f" ]]; then
      echo "[PASS] artifact exists: $f"
    else
      echo "[FAIL] missing artifact: $f"
      status="FAIL"
    fi
  done

  # 3) Lightweight readiness quality checks from bot log (if present).
  if [[ -f "logs/bot.log" ]]; then
    err_count=$(grep -c "ERROR Loop error" logs/bot.log || true)
    echo "[INFO] bot.log loop error count: $err_count"
    if [[ "$err_count" -le 5 ]]; then
      echo "[PASS] loop error count within readiness threshold (<=5)"
    else
      echo "[FAIL] loop error count exceeds readiness threshold (>5)"
      status="FAIL"
    fi
  else
    echo "[INFO] logs/bot.log not found, skipping runtime error-count check"
  fi

  # 4) Unified backtest API and system health endpoint are required for readiness.
  if grep -q "@app.post(\"/api/backtest/run\")" webapp/app.py; then
    echo "[PASS] unified backtest endpoint present"
  else
    echo "[FAIL] unified backtest endpoint missing"
    status="FAIL"
  fi

  if grep -q "@app.get(\"/api/system/health\")" webapp/app.py; then
    echo "[PASS] system health endpoint present"
  else
    echo "[FAIL] system health endpoint missing"
    status="FAIL"
  fi

  echo "[m5-gate] completed checks"

} 2>&1 | tee "$OUT_LOG"; then
  true
else
  status="FAIL"
fi

# report
{
  echo "# M5 Trading Readiness Report (2026-02-22)"
  echo
  echo "- Script: \`scripts/m5_trading_readiness_gate.sh\`"
  echo "- Log: \`logs/m5_trading_readiness_2026-02-22.log\`"
  echo "- Executed at: $(date -Iseconds)"
  echo
  echo "## Scope"
  echo "1. Release + risk gates re-run (must pass)"
  echo "2. M2/M3/M4 + marker/reliability artifacts existence"
  echo "3. Runtime readiness sanity checks (loop error threshold)"
  echo "4. Unified backtest + system health endpoint presence"
  echo
  echo "## KPI/Threshold Policy"
  echo "- Release gate: PASS required"
  echo "- Risk gate: PASS required"
  echo "- Loop error threshold: <= 5 recent occurrences in bot.log"
  echo "- Required endpoints: /api/backtest/run, /api/system/health"
  echo
  echo "## Execution Log"
  echo '```text'
  cat "$OUT_LOG"
  echo '```'
  echo
  echo "## Decision"
  echo "- Result: **$status**"
  if [[ "$status" == "PASS" ]]; then
    echo "- Readiness: **GO for controlled launch preparation (M6)**"
  else
    echo "- Readiness: **NO-GO until failed checks are resolved**"
  fi
} > "$REPORT"

if [[ "$status" == "PASS" ]]; then
  echo "[m5-gate] final: PASS"
  exit 0
else
  echo "[m5-gate] final: FAIL"
  exit 1
fi
