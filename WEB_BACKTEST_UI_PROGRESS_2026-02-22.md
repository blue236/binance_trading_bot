# WEB Backtest UI Progress - 2026-02-22

작성자: Hana 요청 반영 (즉시 착수)

## 작업 요약

프론트엔드 `webapp/templates/index.html` 중심으로 Backtest 탭을 통합 UX로 개편했습니다.

### 1) 통합 모드 실행/비교 UI
- `Mode` 선택 추가: `quick | legacy | both`
- 단일 실행 버튼 `Run Unified`로 `/api/backtest/run` 호출
- 엔진 비교 테이블 추가
  - Engine / ROI% / MDD% / Trades / Final Equity / Status
- KPI 카드 그리드 추가(상단 요약)

### 2) 전략 마커 차트 기반 구조
- Backtest 전용 차트 `#btUnifiedChart` 추가
- marker overlay plugin 추가 (BUY/SELL 점 + 라벨 B/S)
- 현재 백엔드 trades 마커 계약 미완성 대비:
  - `result.trades` 있으면 우선 사용
  - 없으면 mock marker 생성(`buildMockMarkers`)로 graceful fallback
- 데이터 없을 때 mock equity + marker로 시각 구조 유지

### 3) 모바일 레이아웃 기본 점검
- `#btUnifiedChart` 모바일 높이 제한 적용
- KPI grid 모바일 2열 전환
- 기존 반응형 스타일과 충돌 없이 동작하도록 보강

### 4) 분리/확장 가능성
- 차트 마커 렌더링 로직을 함수 단위 분리:
  - `markerPlugin(markers)`
  - `buildMockMarkers(labels, values)`
  - `renderUnifiedChart(result)`
  - `renderCompare(results)`
- 추후 Mina API 계약 확정 시 `result.trades` 스키마 매핑만으로 결합 가능

## 수정 파일
- `binance_trading_bot/webapp/templates/index.html`

## UI 동작 확인 포인트
1. Backtester 탭 진입 → Mode 변경 가능
2. Run Unified 클릭 시 `/api/backtest/run` 응답 출력
3. 결과 테이블/요약 카드 렌더
4. 차트 렌더 + marker overlay 노출
5. 데이터 부족 시 fallback(mock) 차트 정상 표시

## Pass-2 (interactive + 운영 사용성 개선)

### 1) Marker tooltip 구현
- `#btMarkerTooltip` 요소 추가
- 차트 hover 시 최근접 marker hit-test로 tooltip 표시
- 표시 필드: `time / price / side / reason`
- 실데이터 marker가 없으면 mock marker reason(`mock_signal`) 사용

### 2) 비교 테이블 정렬/강조 보강
- 컬럼 추가/강화: WinRate, ROI, MDD
- 헤더 클릭 정렬(`desc/asc` 토글): WinRate/ROI/MDD
- 강조 규칙:
  - 최고 ROI = `best`
  - 최저 ROI = `worst`
  - 최고 WinRate = `best`
  - 최저(가장 양호) MDD = `best`

### 3) 모바일 overflow 개선
- 표 셀 `white-space: nowrap` + table wrapper scroll 유지
- 모바일에서 버튼 간격/배치 보강(`.card .row button` 간격)
- 기존 반응형과 결합해 버튼/표 깨짐 최소화

## 남은 연계 작업(백엔드 계약 이후)
- `result.trades` 표준 스키마 확정(index/price/side/ts/reason)
- Marker tooltip 상세(reason/qty/sl/tp/regime) 확장
- Both 모드에서 엔진별 토글 레이어(quick vs legacy) 표시 강화
