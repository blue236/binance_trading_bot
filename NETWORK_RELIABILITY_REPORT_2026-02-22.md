# NETWORK_RELIABILITY_REPORT_2026-02-22

## 변경 요약
M4 Reliability & Observability 1차로 네트워크 재시도/백오프 범위를 ticker 외 핵심 조회 경로까지 확대했습니다.

### 적용 범위
- `fetch_tickers` 경로 (기존 적용 유지)
- `fetch_ticker` 경로 (개별 가격 조회)
- `fetch_ohlcv` 경로 (시그널/레짐/트레일 계산용)
- `fetch_balance` 경로 (네트워크 오류 타입에 대한 재시도 보강)

### 정책
- config 연동:
  - `network.retry_count` (default: 3)
  - `network.retry_backoff_sec` (default: 1.0)
- 재시도 방식: 증분 백오프 (`sleep = backoff * attempt`)
- 로깅 정책:
  - 중간 실패: warning 1줄 (traceback 노이즈 없음)
  - 최종 실패: error 요약 1줄

## Observability 추가
- runtime state(`state.json`)에 네트워크 건강도 기록:
  - `runtime_health.network.consecutive_failures`
  - `runtime_health.network.last_error`
  - `runtime_health.network.last_error_at`
  - `runtime_health.network.last_ok_at`
  - `runtime_health.network.last_label`
- Web API 노출:
  - `/api/ai/status` 응답에 `network_health` 포함

## 검증
1) 정적 검증
- `python3 -m py_compile binance_trading_bot/main.py binance_trading_bot/webapp/app.py` 통과

2) 모킹 로그 검증 (venv)
- 2회 일시 실패 후 3회차 성공 시 warning 로그만 출력, 프로세스 계속
- 성공 후 `NETWORK_HEALTH.consecutive_failures=0` 확인
- `last_error/last_ok_at/last_label` 갱신 확인

## Reliability Delta
- ticker 단일 경로 재시도 -> **ticker + ohlcv + balance 핵심 조회 경로**로 확대
- 단순 재시도 -> **runtime health 상태화 + API status 노출**로 관측성 향상
