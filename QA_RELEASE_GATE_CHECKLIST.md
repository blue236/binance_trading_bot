# QA Release Gate Checklist

## Telegram approval regression
- [ ] BUY signal -> approval request sent
- [ ] APPROVE token -> order executed
- [ ] DENY token -> order blocked
- [ ] timeout -> default deny
- [ ] approval events logged in trades + audit

## Telegram runtime command regression
- [ ] /setrisk range validation
- [ ] /setmaxpos range validation
- [ ] /setcooldown range validation
- [ ] /mode safe|normal|aggressive validation
- [ ] confirm token required and expires
- [ ] non-requester cannot confirm/cancel
- [ ] audit log: issued/applied/denied/expired

## Web UI/mobile regression
- [ ] tabs/buttons/log panels usable on <=768px
- [ ] config edit/save works on mobile viewport
- [ ] log download works on mobile/desktop

## Security baseline regression
- [ ] unauthenticated routes blocked (target config)
- [ ] cookie policy documented and applied
- [ ] HTTP->HTTPS redirect verified
