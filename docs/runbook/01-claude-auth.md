# 런북 01 — claude 인증 만료

## 증상
- 슬랙: `🔴 claude 세션 연속 3회 실패(empty_output) — 인증 만료 의심`
- judge/meeting-mail 이 매일 실패, `_usage/claude/<날짜>.jsonl` 꼬리가 `"ok": false`
- 다이제스트에 `⚠️ judge: 시도는 있으나 성공 0`

## 즉시 확인
```bash
tail -5 ~/kv-data/_usage/claude/$(date -u +%F).jsonl   # err 종류 확인
claude -p "reply with exactly: OK"                      # 대화형 확인(로그인 상태)
grep -c "빈 출력" ~/kv-data/_scheduler/launchd.log
```

## 원인 후보
1. **구독 세션 만료**(가장 흔함) — 맥 재부팅·장기 sleep 후
2. claude CLI 가 PATH 에 없음 → `err: FileNotFoundError` (plist PATH 문제)
3. 권한 프롬프트 대기 → `err: timeout`

## 복구
1. 터미널에서 `claude` 실행 → 로그인 안내가 뜨면 로그인
2. 확인: `claude -p "reply with exactly: OK"` → `OK`
3. 강제 재실행(다음 4h 틱을 기다리지 않으려면):
   `launchctl kickstart gui/$(id -u)/com.kv.scheduler`
4. judge 는 attempt-suffix(-r2·-r3)가 있어 **같은 날 재시도**된다

## 재발 방지
- 밤샘 sleep 후 인증 유지가 ADR 0003 의 남은 검증 항목 — 실패가 반복되면 tmux 상주(방식 ③) 재검토
- PATH 문제면 `config/launchd/com.kv.scheduler.plist` 의 PATH 에 `~/.local/bin` 확인

## MTTR (예상)
인지 후 **3분** (로그인 1분 + kickstart 즉시). 인지까지는 최대 4h(다음 틱) 또는 아침 다이제스트.
