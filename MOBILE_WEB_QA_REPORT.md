# Mobile Web QA Report (Initial)

Date: 2026-02-21

## Target viewports
- 390x844 (iPhone 12/13)
- 430x932 (large phone)
- 768x1024 (tablet portrait)

## Checks
- [x] Tabs reachable without horizontal scroll
- [x] Buttons/input controls >=44px touch-friendly
- [x] AI logs / Backtest output panels scroll independently
- [x] Config editor usable in single-column layout
- [x] Plot images constrained to viewport width

## Applied changes
- Sticky tab bar on mobile
- Tab buttons split 2-column responsive width
- Output panel max-height reduced for smaller screens
- Credential filename label unified to `.credentials.enc.json`

## Follow-up
- Run Playwright mobile snapshot regression in CI
- Add offline/slow-network UX checks (3G throttling)
