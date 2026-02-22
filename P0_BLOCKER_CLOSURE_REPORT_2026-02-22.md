# P0 Blocker Closure Report (2026-02-22)

## Scope
1) BTB auth enforcement code completion
2) Production-safe operations commands completion (/summary, /health, /restart with 2-step confirm + owner-only)

## Implemented
### 1) Auth enforcement (Web)
- Added session auth middleware in `webapp/app.py`
  - Protects `/` and all `/api/*`
  - Unauthenticated web route -> `302 /login`
  - Unauthenticated API route -> `401 {ok:false,error:"unauthorized"}`
- Added login flow
  - `GET /login`, `POST /login`
  - HttpOnly cookie session token
- Added logout API
  - `POST /api/auth/logout`
- Added login template
  - `webapp/templates/login.html`

### 2) Telegram commands (production-safe)
- Added read-only ops commands
  - `/summary`, `/health`
- Added restart flow
  - `/restart` issues confirm token
  - `/confirm <token>` required within TTL
  - requester-only token confirmation
  - on apply: audit log + process re-exec (`os.execv`)
- Owner-only enforcement for state-changing commands
  - `/setrisk`, `/setmaxpos`, `/setcooldown`, `/mode`, `/pause`, `/resume`, `/start`, `/stop`, `/restart`
- Added config field
  - `alerts.telegram_owner_user_id`

## Test results
### A. Static sanity
- `python3 -m py_compile binance_trading_bot/main.py binance_trading_bot/webapp/app.py` ✅

### B. Auth behavior (TestClient via venv)
- GET `/` unauthenticated => `302 /login` ✅
- GET `/api/health` unauthenticated => `401` ✅
- POST `/login` valid creds => `302` + session cookie ✅
- GET `/api/health` authenticated => `200` ✅

## Go / No-Go
- **GO (conditional)**
  - 조건1: 운영 환경에 `BTB_WEB_PASSWORD`, `BTB_WEB_SESSION_SECRET` 설정
  - 조건2: `config.yaml`의 `alerts.telegram_owner_user_id` 설정
  - 조건3: HTTPS/secure-cookie 운영값 적용 (`BTB_WEB_COOKIE_SECURE=1` 권장)

## Residual risk
- Telegram owner ID 미설정 시 owner-only 명령은 모두 차단됨(안전측면 의도).
- restart는 프로세스 재실행 방식이므로 supervisor/systemd 정책과 함께 검증 권장.
