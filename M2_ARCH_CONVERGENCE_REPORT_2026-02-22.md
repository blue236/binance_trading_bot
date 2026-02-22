# M2 Architecture Convergence Report (2026-02-22)

## 목표
백테스트/전략 데이터 계약을 webapp 전반에서 더 일관된 스키마로 수렴.

## 현재 (Before)
- `webapp/backtest_service.py`에 quick/legacy별 dict 반환 포맷이 사실상 계약 역할.
- 공통 구조(`summary`, `metrics`, `markers`)는 있었지만 타입 강제는 느슨함.
- both(quick+legacy) 병합 결과를 명시적으로 표현하는 타입/헬퍼 부재.

## 변경 (After)
### 1) `webapp/models.py`
통합 스키마 타입 추가:
- `BacktestMarker`
- `BacktestMetrics`
- `BacktestSummary`
- `BacktestCurve`
- `UnifiedBacktestResult` (engine: quick|legacy|both)
- `UnifiedBacktestBundle` (quick+legacy 병합 뷰)

핵심: markers/metrics/summary를 공통 타입으로 명시.

### 2) `webapp/backtest_service.py`
정규화/수렴 헬퍼 정리:
- `_normalize_markers`, `_normalize_curve`, `_normalize_metrics`
- `to_unified_quick(...)`
- `to_unified_legacy(...)`
- `to_unified_both(...)` 신규

핵심: quick/legacy/both 결과를 동일 계약 기반으로 반환.

## 호환성
- 기존 `run_sma_crossover(...)`의 반환 구조는 유지.
- 기존 `to_unified_quick`, `to_unified_legacy` 함수명/호출 경로 유지.
- app.py 직접 수정 없이 서비스 레이어에서 수렴하도록 설계.

## 검증
- 실행: `python3 -m py_compile webapp/models.py webapp/backtest_service.py`
- 결과: PASS

## 남은 갭
1. app/api 응답에서 `UnifiedBacktestResult`를 직접 사용하도록 엔드포인트 타입 힌트 정리(선택)
2. legacy 파서(`_extract_float/_extract_int`) 패턴 보강 및 테스트 케이스 확장
3. both 결과를 실제 API 응답에 노출하는 라우트(필요시) 추가

## Convergence Delta
- 기존: 암묵적 dict 계약 중심
- 변경: 명시적 Pydantic 스키마 + quick/legacy/both 정규화 헬퍼 도입
- 효과: 스키마 일관성/리뷰 용이성/향후 API 통합 비용 감소
