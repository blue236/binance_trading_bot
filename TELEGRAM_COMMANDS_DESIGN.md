# Telegram 운영 명령어 확장 설계/구현안

작성일: 2026-02-19
요청자: Steven

## 목표
운영 중 Telegram에서 리스크/모드/승인 정책을 안전하게 제어하되,
아래 3가지를 모든 변경형 명령에 강제한다.
1) 범위 검증 (입력값 안전성)
2) 확인 프롬프트 (2단계 확정)
3) 감사 로그 (누가/언제/무엇을)

---

## 신규 명령어 목록 (우선순위)
1. `/setrisk <pct>`
2. `/setmaxpos <n>`
3. `/setcooldown <h>`
4. `/mode <safe|normal|aggressive>`
5. `/approve on|off`
6. `/summary`
7. `/health`
8. `/restart` (확인 포함)

---

## 공통 설계 원칙

### A) 명령 타입 분리
- 조회형(read-only): `/summary`, `/health`
- 변경형(state-changing): 나머지 전부

### B) 2단계 확인 프로토콜(변경형 필수)
1. 사용자가 명령 입력
2. 봇이 검증 후 확인 토큰 발급
   - 예: `CONFIRM setrisk 171942`
3. 동일 채팅에서 토큰 확인 시 실제 반영
4. 토큰 TTL(예: 120초) 만료 시 자동 폐기

### C) 감사 로그
- 파일: `logs/audit.log` (JSONL 권장)
- 이벤트: `COMMAND_RECEIVED`, `COMMAND_VALIDATION_FAILED`, `COMMAND_CONFIRM_ISSUED`, `COMMAND_APPLIED`, `COMMAND_CANCELLED`, `COMMAND_EXPIRED`, `COMMAND_DENIED`
- 공통 필드:
  - `ts`, `chat_id`, `user_id`, `username`, `cmd`, `args`, `result`, `reason`, `before`, `after`, `confirm_token`, `request_id`

### D) 권한/채널 제한
- 허용 chat_id 외 요청은 즉시 거부 + 감사로그
- 필요 시 allowlist user_id 추가 (권고)

---

## 명령별 상세 스펙

## 1) /setrisk <pct>
- 목적: `risk.per_trade_risk_pct` 런타임 변경
- 입력 범위(권고): `0.1 <= pct <= 5.0`
- 소수점 1~2자리 허용
- 확인 프롬프트:
  - `현재 0.7% -> 1.0% 변경 예정. CONFIRM setrisk <token> 입력 시 반영(120초)`
- 반영 시:
  - cfg 메모리 반영 + 설정 스냅샷 로그
  - 옵션: `runtime_overrides.json` 저장

## 2) /setmaxpos <n>
- 목적: `risk.max_concurrent_positions` 변경
- 입력 범위(권고): `1 <= n <= 20` (정수만)
- 확인 프롬프트 + 감사로그 동일

## 3) /setcooldown <h>
- 목적: `risk.cooldown_hours` 변경
- 입력 범위(권고): `0 <= h <= 72`
- 0 허용(쿨다운 비활성), 실수 허용 여부는 정책 선택
  - 권고: 정수만 허용해 운영 복잡도 축소

## 4) /mode <safe|normal|aggressive>
- 목적: 운용 모드 프리셋 적용
- 정책 예시:
  - safe: risk 낮춤, maxpos 낮춤, approval on 강제
  - normal: 기본값
  - aggressive: risk 상향(상한 내), approval 정책은 운영 규정 따름
- 확인 프롬프트는 변경 diff 포함:
  - `mode normal -> safe (risk 0.7->0.4, maxpos 3->2, cooldown 4->8)`

## 5) /approve on|off
- 목적: `alerts.enable_trade_approval` 토글
- 입력값: on/off
- 안전 정책:
  - `off`는 고위험 변경으로 별도 경고 문구 + 이중 확인
  - 예: `CONFIRM approve_off <token>`

## 6) /summary
- 목적: 운영 요약 조회 (읽기 전용)
- 출력 항목:
  - 모드, approval 상태, risk/maxpos/cooldown
  - equity 요약(당일 시작/현재/변화율)
  - 포지션 수, 최근 주문/오류 카운트

## 7) /health
- 목적: 런타임 헬스체크 조회 (읽기 전용)
- 출력 항목:
  - bot loop alive 여부(최근 loop timestamp)
  - exchange 연결 상태/최근 API 오류
  - telegram poll 상태
  - state 파일 read/write 상태
  - 프로세스 uptime, 메모리(가능 시)

## 8) /restart
- 목적: 안전 재시작
- 2단계 확인 필수 + 강한 경고
- 흐름:
  1. `/restart`
  2. `재시작 시 일시 중단 발생. CONFIRM restart <token> (60초)`
  3. 확인 시 `restart_requested` 상태 기록 + 감사로그 + graceful restart
- 구현 방식:
  - systemd 사용 시: 재시작 트리거 파일 생성 후 supervisor가 처리
  - 또는 bot 내부에서 종료 코드 기반 재기동

---

## 구현 구조 제안

## 1) 코드 구조
- `main.py`에서 Telegram 명령 처리를 아래로 분리:
  - `telegram_commands.py`
  - `audit_logger.py`
  - `runtime_control.py`

## 2) 상태 저장
- `state` 확장:
  - `pending_confirms`: 토큰, 만료시각, 요청자, 명령, 파라미터, before/after
  - `runtime_overrides`: 실제 적용된 런타임 오버라이드

예시:
```json
{
  "pending_confirms": {
    "171942": {
      "cmd": "setrisk",
      "args": {"pct": 1.0},
      "expires_at": "2026-02-19T15:30:00+01:00",
      "requested_by": 123456789
    }
  }
}
```

## 3) 파서/검증기
- `parse_command(text) -> (cmd, args)`
- `validate_command(cmd, args, cfg, state) -> ok, error`
- 검증 실패 시 반영 금지 + 상세 에러 안내 + 감사로그

## 4) 확인 토큰 처리
- 랜덤 6자리 + 요청 ID
- TTL 만료 배치 정리(폴링 루프마다 정리)
- 동일 사용자/채팅에서만 확인 허용

## 5) 감사로그 구현
- JSONL append (`logs/audit.log`)
- 거래 로그(trades.csv)와 분리하여 감사 추적성 강화

---

## 테스트 계획

### 단위 테스트
- 명령 파싱 정상/오류 케이스
- 입력 범위 경계값 테스트
- 확인 토큰 만료/중복/오사용 테스트

### 통합 테스트
- Telegram update mock으로 전체 명령 흐름 검증
- `/approve off` 이중확인 강제 검증
- `/restart` 확인 없이는 미실행 검증

### 보안 테스트
- 허용 chat_id 외 차단
- 확인 토큰 탈취/재사용 방지
- 감사로그 누락 여부 점검

---

## 점진 도입 순서 (권고)
1. `/summary`, `/health` (read-only)
2. `/setrisk`, `/setmaxpos`, `/setcooldown`
3. `/mode`, `/approve`
4. `/restart` (가장 민감, 마지막)

---

## 회신 요약
- 요청 명령어 8종 모두 설계 가능
- 변경형 명령은 모두 **범위 검증 + 2단계 확인 + 감사로그** 강제
- 구현은 기존 `poll_telegram_commands`를 명령 모듈화하여 안정적으로 확장 가능
