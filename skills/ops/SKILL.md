---
name: ops
description: 살림 — kv-data 스냅샷 백업(7일)·보존 규칙 집행·그 둘의 health. 옛 kv-automation 스케줄러의 backup·retention 을 2026-09-26 에 옮겨 왔다.
schedule:
  - { cmd: backup,    when: "매일 12:45", why: "맥이 깨어 있는 낮. 스킬들의 sqlite 일일 사본(db/backup/)이 오전 실행 뒤에 생기므로 그 뒤" }
  - { cmd: retention, when: "월 13:00",  why: "주 1회면 충분(하루 수십 MB). 백업 뒤 — 오늘 백업이 없으면 스스로 아무것도 안 지운다" }
  - { cmd: health,    when: "매일 12:55", why: "backup 은 자기가 안 돌았다고 말할 수 없다. 이틀 넘게 스냅샷이 없으면 review_test 한 줄" }
---

# 살림 (ops)

```
backup     ~/kv-data → ~/kv-data-backup/<날짜>/   안 변한 파일은 전날 스냅샷에 하드링크. 7일 보관. 열린 sqlite(*/db/*.sqlite) 는 제외 —
           각 스킬이 매일 db/backup/ 에 뜨는 일관된 사본이 대신 들어간다
retention  constraints.yaml ops.retention 의 glob **만**(허용 목록) 보관일수 지나면 지운다. 🔴 오늘 백업이 성공했을 때만. glob 이 루트 밖이면 중단
health     .last-success 가 backup_stale_days 보다 오래됐으면 review_test 한 줄
```
밖으로 나가는 것은 review_test 한 줄(health·백업 실패)뿐이다.

## 알아둘 것

**같은 디스크다.** 맥·디스크 고장은 못 막는다. 오프사이트 1벌은 서버 이관 때 해결한다(옛 스케줄러도 못 했다).
**원본이 비어 보이면 중단한다** — 마운트 실패·경로 오타로 빈 백업이 멀쩡한 스냅샷을 밀어내지 않게. 파일 수가 원본보다 적어도 실패로 남긴다(.last-success 를 안 쓴다).
**백업 없이 지우는 경로는 없다.** retention 은 .last-success 가 오늘 것일 때만 움직인다.
**돈 낸 데이터는 규칙에 안 넣는다** — `linkedin/raw`(Apify 원본), `_archive/`(옛 시스템 데이터: net-sourcing raw 161MB 포함) 는 영구. 규칙에 없는 glob 은 건드릴 수 없다.

```bash
cd ~/kv
./.venv/bin/python -m skills.ops.run backup | retention [--dry-run] | health
```
