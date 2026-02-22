# M3_RISK_ENFORCEMENT_REPORT_2026-02-22

## 목표
트레이드 진입 전 mandatory risk gate를 공통 함수로 강제해 진입 차단 조건을 일관 처리.

## 구현 사항
- `main.py`에 공통 게이트 추가:
  - `evaluate_pretrade_risk_gate(cfg, state, symbol, now, equity_now, equity_start)`
- 최소 체크 통합:
  - `bot_paused`
  - `max_concurrent_positions`
  - `daily_loss_stop`
  - `already_in_position`
  - `cooldown_active`
- 거절 기록 공통화:
  - `_record_risk_gate_reject(...)`
  - `audit.log`에 `RISK_GATE_REJECT` 이벤트 기록
  - `trades.csv`에 `event=RISK_GATE_REJECT` 행 기록
- 메인 진입 루프에서 분산된 조건문 대신 공통 게이트 호출로 강제.

## 로그 포맷(거절)
- audit payload: `{scope: pretrade, symbol, reason}`
- trades row: `event=RISK_GATE_REJECT, symbol, side=BUY, reason=<code>`

## 검증
- `python3 -m py_compile main.py` 통과

## Risk Gate Delta
- 기존: 진입 차단 조건이 루프 내 분산(일관 로그/감사 미흡)
- 변경: 단일 게이트 함수 + 거절 사유 표준화 + audit/trades 동시 기록
- 트레이딩 전략/시그널 의미 변경 없음(진입 전 검증 경로 정리 중심)
