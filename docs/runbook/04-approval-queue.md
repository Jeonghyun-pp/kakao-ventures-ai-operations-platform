# 런북 04 — 승인 큐 이상 (카드 안 뜸 / ✅ 반영 안 됨 / 초안 중복)

## A. 카드가 슬랙에 안 뜬다
증상: 승인 요청은 생겼는데(`approval list` 에 보임) 채널에 카드가 없음.
```bash
python -m kv_core.approval list                      # 큐 상태
tail -20 ~/kv-data/_approvals/launchd.log | grep -i slack
```
원인/복구:
1. 봇 토큰 만료·앱 재설치 → `🔴 slackbot.post 실패: invalid_auth` → 토큰 갱신(.env `SLACK_BOT_TOKEN`)
2. 봇이 채널에서 제거됨 → `channel_not_found`/`not_in_channel` → 슬랙에서 `/invite @kv-automation`
3. **어느 경우든 승인은 CLI 로 가능**: `python -m kv_core.approval approve <key>`
4. W9 이후엔 발송 실패가 웹훅으로 에스컬레이션된다(`🔴 승인 카드 발송 실패 N건`)

## B. ✅ 를 눌렀는데 반영이 안 된다
```bash
python -m kv_core.approval show <key> | grep -E 'decision|slack_ts'
python -m kv_core.approvals_tick        # 수동 1틱 (10분 기다리지 않으려면)
```
원인/복구:
1. 아직 틱 전 — 최대 10분 대기가 정상
2. `slack_ts` 가 없다 = 카드가 안 나갔다 → A 항목으로
3. 이모지 이름이 다름 — ✅(`white_check_mark`)·❌(`x`) 계열만 인식
4. `SLACK_APPROVAL_USER` 가 설정돼 있으면 **그 사람의 ✅만** 인정(거부는 누구나)

## C. Gmail 초안이 중복 생성됐다
```bash
python -m kv_core.approval show <key> | grep -E 'executing_at|draft_id'
```
- `executing_at` 은 있는데 `draft_id` 가 없으면 이전 틱이 초안 직후 죽은 것 — 다음 틱이 경고를 남긴다
- 복구: Gmail 임시보관함에서 중복 초안 수동 삭제(발송 전이라 무해)
- 감수 사항: create_draft ↔ 레코드 저장 사이의 수백 ms 창(W8 리스크 메모)

## 재발 방지
- 승인 정체가 상한을 넘으면 `announce-backlog` 알림이 뜬다(W9)
- 큐 파일은 감사 기록이라 삭제하지 않는다 — 종결건은 pending 에서 자동 제외

## MTTR (예상)
A: **5분**(토큰 갱신) · B: **0~10분**(틱 대기) · C: **2분**(초안 삭제)
