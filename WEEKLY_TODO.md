# Weekly TODO — Binance Trading Bot (Steve Team)

## P1 (Must-do)
1. Telegram 운영 명령어 확장
   - `/setrisk <pct>`
   - `/setmaxpos <n>`
   - `/setcooldown <hours>`
   - `/mode <safe|normal|aggressive>`
   - 요구사항: 범위 검증 + confirm 단계 + 감사 로그

2. 모바일웹 최적화
   - 탭/버튼/로그 패널 반응형 개선
   - 모바일에서 설정 편집/로그 다운로드 UX 검증

3. 외부접속 보안 기준 고정
   - 로그인 강제 경로 점검
   - HTTP/HTTPS별 쿠키 정책 문서화

## P2
4. QA 릴리즈 게이트 문서화
   - approval/deny/timeout 회귀 체크리스트
   - `QA_RELEASE_GATE_CHECKLIST.md` 생성/연결

5. 문서 일관성 마무리
   - `.credentials.enc.json` 표기 통일(UI/README)

6. 최종 PR 준비
   - 스모크 테스트
   - PR 설명: 변경범위/리스크/롤백 절차

---

# Day Plan (Today)
1. 명령어 `/setrisk`, `/setmaxpos` 먼저 구현
2. confirm/감사로그 공통 헬퍼 추가
3. `/setcooldown`, `/mode` 구현
4. 모바일 화면 1차 CSS 개선
5. 명령어/모바일 스모크 테스트 후 푸시
