# BTB 업데이트 구현안 (Backtest 통합 + AI 전략 매매시점 시각화)

작성일: 2026-02-20
요청자: Steven
범위: Web UI Backtest 탭, AI config 기반 시뮬레이션/차트, 인터랙티브 확장형 차트

---

## 0) 요구사항 정리
1. Backtest 탭에서 **Quick ROI Backtester + Legacy backtester**를 실질 통합
   - 한 흐름에서 전략 선택/실행/결과 비교 가능
2. AI Trading의 `config.yaml` 전략 기준으로 과거 데이터를 다운로드하여
   - **매수/매도 시점**을 차트에 시각화
3. 차트는 향후 기능 확장 가능한 인터랙티브 구조
   - hover / crosshair / tooltip / zoom / drag / marker detail 확장

---

## 1) 스프린트 작업 분해 (2주)

## Epic A — Backtest 경험 통합 (Unified Flow)

### A1. 통합 도메인 모델 설계
- [ ] `BacktestRunRequest` 공통 스키마 설계
  - `engine`: `quick|legacy|ai_strategy`
  - `symbol`, `timeframe`, `date_range`, `fees`, `slippage`, `capital`
  - `strategy_params` (엔진별 파라미터)
- [ ] `BacktestRunResult` 공통 응답 스키마 설계
  - KPI(ROI, MDD, winrate, trades, pnl)
  - 시계열(equity, drawdown)
  - 이벤트(buy/sell markers)

### A2. 백엔드 라우트 통합
- [ ] 기존 `/api/backtester/run`, `/api/legacy/backtester/run`를
      `/api/backtest/run` (통합 엔드포인트)로 오케스트레이션
- [ ] 엔진 어댑터 계층 추가
  - `QuickEngineAdapter`
  - `LegacyEngineAdapter`
  - `AIStrategyReplayAdapter` (신규)
- [ ] `/api/backtest/compare` 추가
  - 동일 입력으로 다중 엔진 결과 비교

### A3. UI 통합
- [ ] Backtest 탭을 단일 흐름으로 개편
  - 전략/엔진 선택 → 파라미터 입력 → 실행 → 비교 카드/차트
- [ ] 비교 테이블 + 겹침 차트(ROI/equity/trade markers)

---

## Epic B — AI config 기반 과거 매매 시점 시각화

### B1. 데이터 수집 파이프라인
- [ ] `HistoricalDataService` 추가
  - ccxt로 OHLCV 수집 (`symbol`,`timeframe`,`since`,`until`)
  - 로컬 캐시(SQLite 혹은 parquet/CSV)
- [ ] 증분 다운로드
  - 이미 있는 구간은 skip, 누락 구간만 fetch
- [ ] 데이터 품질 검증
  - 정렬, 중복 제거, 결측 처리, 타임존 정규화(UTC)

### B2. AI 전략 리플레이 엔진 (신규)
- [ ] `config.yaml` 파싱 + `aggressive_mode`/risk 파라미터 로딩
- [ ] 기존 `main.py`의 시그널 논리(레짐/신호/진입/청산) 재사용 가능한 함수로 분리
  - 목표: 실거래 로직과 백테스트 로직의 계산 일관성
- [ ] 리플레이 출력 표준화
  - `signals`: 후보 시그널
  - `orders`: BUY/SELL 체결 이벤트
  - `positions`: 포지션 상태 시계열

### B3. 차트 마커 시각화
- [ ] 캔들 위에 BUY/SELL 마커 오버레이
- [ ] 마커 클릭 시 상세 툴팁
  - 이유(signal/regime), 가격, 수량, SL/TP, 리스크 설정
- [ ] 동일 기간 내 엔진별 마커 비교 레이어

---

## Epic C — 확장형 인터랙티브 차트 아키텍처

### C1. 차트 라이브러리 레이어 추상화
- [ ] 현재 Chart.js 단순 line에서 확장 가능한 어댑터 구조로 전환
  - `ChartProvider` 인터페이스
  - 기본 구현: Chart.js(+zoom/crosshair plugin) 또는 lightweight-charts(권고)
- [ ] 시리즈 타입 표준화
  - candlestick, line, histogram, markers, annotations

### C2. 인터랙션 요구 구현(1차)
- [ ] hover crosshair
- [ ] tooltip (OHLC + marker detail)
- [ ] zoom in/out (wheel/pinch)
- [ ] drag pan
- [ ] reset zoom 버튼

### C3. 확장 포인트 설계
- [ ] marker metadata schema versioning
- [ ] plugin hook (`onMarkerClick`, `onRangeChange`, `onCompareToggle`)
- [ ] 향후 지표(EMA/BB/ATR) 오버레이 슬롯

---

## 2) 아키텍처 제안

## 2.1 백엔드 구조
- `webapp/backtest_orchestrator.py` (신규)
  - 요청 검증/엔진 분배/결과 통합
- `webapp/engines/quick_engine.py` (기존 BacktestService 래핑)
- `webapp/engines/legacy_engine.py` (기존 CLI legacy runner 래핑)
- `webapp/engines/ai_replay_engine.py` (신규)
- `webapp/services/historical_data_service.py` (신규)
- `webapp/services/result_normalizer.py` (신규)

## 2.2 프론트엔드 구조
- `BacktestUnifiedPage`
  - `StrategySelector`
  - `ParameterPanel`
  - `RunControlBar`
  - `ResultCompareTable`
  - `InteractiveChartPanel`

## 2.3 API 스펙(초안)
- `POST /api/backtest/run`
- `POST /api/backtest/compare`
- `POST /api/backtest/data/sync`  (기간 데이터 다운로드)
- `GET /api/backtest/result/{run_id}`

---

## 3) UI 시안 (텍스트 와이어프레임)

```
[ Backtest Unified ]
┌───────────────────────────────────────────────────────────────┐
│ Symbol [BTC/USDT] TF [1h] Period [2024-01-01 ~ 2025-12-31]   │
│ Engine [Quick | Legacy | AI Strategy]  Compare [☑Quick ☑AI]  │
│ Capital [10000] Fee [0.1%] Slippage [0.05%]  [Run] [Compare] │
├───────────────────────────────────────────────────────────────┤
│ Strategy Params (engine-aware dynamic form)                  │
│ - Quick: fast/slow                                            │
│ - Legacy: windows/threshold/strategy                          │
│ - AI: config snapshot + override(optional)                    │
├───────────────────────────────────────────────────────────────┤
│ KPI Compare: ROI | MDD | WinRate | Trades | Sharpe(optional) │
├───────────────────────────────────────────────────────────────┤
│ Interactive Chart (candles + buy/sell markers + equity pane) │
│ - hover crosshair / zoom / drag / marker tooltip              │
├───────────────────────────────────────────────────────────────┤
│ Event Timeline (BUY/SELL entries with reason + params)        │
└───────────────────────────────────────────────────────────────┘
```

---

## 4) 데이터 파이프라인

1. 사용자 실행 요청
2. `HistoricalDataService`가 기간 데이터 캐시 확보
3. 선택 엔진 실행
   - quick: SMA crossover
   - legacy: 기존 backtester.py 실행 + 결과 파싱
   - ai_strategy: config 기반 리플레이
4. `result_normalizer`로 공통 포맷 변환
5. UI에 KPI + 차트 series + marker events 전달
6. run_id 기준 저장(재조회/비교)

### 데이터 저장
- `market_ohlcv` (symbol, timeframe, ts, ohlcv)
- `backtest_runs` (run_id, engine, params, kpi, created_at)
- `backtest_events` (run_id, ts, type, price, qty, metadata)

---

## 5) 수용 기준 (Acceptance Criteria)

## 기능
- [ ] Backtest 탭에서 Quick/Legacy/AI 전략을 하나의 화면에서 실행 가능
- [ ] 최소 2개 엔진 결과를 동일 기간/심볼로 비교 가능
- [ ] AI 전략 기반 BUY/SELL 시점이 차트에 마커로 표시됨

## UX
- [ ] hover/crosshair/tooltip/zoom/drag 정상 동작
- [ ] 마커 클릭 시 상세정보(이유/가격/수량/리스크 파라미터) 표시
- [ ] 비교 ON/OFF 시 차트 레이어 즉시 반영

## 성능
- [ ] 1년 1h 데이터 기준 최초 로드 < 4초(캐시 미스), 재실행 < 1.5초(캐시 히트)
- [ ] 차트 상호작용 프레임 드랍 체감 없음(일반 노트북 기준)

## 정확성
- [ ] AI replay 진입/청산 이벤트가 재실행 시 결정론적으로 동일
- [ ] config.yaml 변경 시 결과 변경이 추적 가능(config snapshot 저장)

## 품질
- [ ] 단위/통합 테스트 통과
- [ ] 레거시 실행 실패 시 사용자 친화적 에러 표시 + 로그 연결

---

## 6) 구현 순서 (권장)
1. 공통 스키마 + 오케스트레이터
2. Unified Backtest 탭 UI(기본 실행/결과 비교)
3. HistoricalDataService + 캐시
4. AI Strategy Replay + 매수/매도 마커
5. 인터랙티브 차트 고도화(zoom/crosshair/tooltip/drag)
6. 회귀/성능 테스트 및 문서화

---

## 7) 리스크 및 대응
- 리스크: legacy 결과 포맷 불규칙
  - 대응: parser normalization + 실패 fallback 메시지 표준화
- 리스크: main.py 로직 중복으로 불일치 발생
  - 대응: 신호 계산 로직 공용 모듈로 분리
- 리스크: 대용량 데이터로 차트 렌더링 지연
  - 대응: 다운샘플링 + viewport lazy rendering

---

## 8) 산출물
- 설계 문서: 본 문서
- API 명세 초안
- UI 와이어프레임
- 엔진/데이터 파이프라인 코드 + 테스트
- 데모 시나리오(Quick vs AI 비교, 마커 시각화)
