---
name: ops
description: 살림 — kv-data 스냅샷 백업(7일)·보존 규칙 집행·일곱 스킬 health 하나·하루 요약 한 통·현황판(dashboard.html)·`kv` 입구. 옛 kv-automation 스케줄러의 backup·retention 을 2026-09-26 에 옮겨 왔다.
schedule:
  - { cmd: backup,    when: "매일 12:45", why: "맥이 깨어 있는 낮. 스킬들의 sqlite 일일 사본(db/backup/)이 오전 실행 뒤에 생기므로 그 뒤" }
  - { cmd: retention, when: "월 13:00",  why: "주 1회면 충분(하루 수십 MB). 백업 뒤 — 오늘 백업이 없으면 스스로 아무것도 안 지운다" }
  - { cmd: health,    when: "매일 12:55", why: "잡은 자기가 안 돌았다고 말할 수 없다. 일곱 스킬의 오늘 보고서·스케줄 설치·백업 나이를 한 번에 보고 막힌 것만 한 줄. 스킬별 health 여섯(12:00~12:40)을 이것 하나로 묶었다(09-26)" }
  - { cmd: digest,    when: "매일 18:30", why: "하루가 끝난 뒤 한 통. 스킬별 하루 요약 슬랙 다섯을 이것 하나로 묶었다(report.daily). 현황판 html 도 이때 다시 쓴다" }
---

# 살림 (ops)

```
backup     ~/kv-data → ~/kv-data-backup/<날짜>/   안 변한 파일은 전날 스냅샷에 하드링크. 7일 보관. 열린 sqlite(*/db/*.sqlite) 는 제외 —
           각 스킬이 매일 db/backup/ 에 뜨는 일관된 사본이 대신 들어간다
retention  constraints.yaml ops.retention 의 glob **만**(허용 목록) 보관일수 지나면 지운다. 🔴 오늘 백업이 성공했을 때만. glob 이 루트 밖이면 중단
health     일곱 스킬(오늘 보고서·스케줄 설치·투심 접수 지연) + 백업 나이 — 막힌 것만 review_test 한 줄. 스킬별 health 여섯을 이것 하나로(09-26)
digest     하루 한 통(review_test) + 현황판 ~/kv-data/_reports/dashboard.html. 스킬별 하루 요약 슬랙 다섯을 이것 하나로(report.daily · ops.digest_slack)
status     현황 한 화면 — 문 · 오늘 판정/행동 · 정답 누적 · 보고서 · 스케줄(선언↔설치) · 마지막 실행
```
밖으로 나가는 것은 review_test 뿐이다(health 한 줄 · 하루 한 통 · 백업 실패). 스킬별 실패 경보는 각자 그대로 낸다.

## 알아둘 것

**같은 디스크다.** 맥·디스크 고장은 못 막는다. 오프사이트 1벌은 서버 이관 때 해결한다(옛 스케줄러도 못 했다).
**원본이 비어 보이면 중단한다** — 마운트 실패·경로 오타로 빈 백업이 멀쩡한 스냅샷을 밀어내지 않게. 파일 수가 원본보다 적어도 실패로 남긴다(.last-success 를 안 쓴다).
**백업 없이 지우는 경로는 없다.** retention 은 .last-success 가 오늘 것일 때만 움직인다.
**돈 낸 데이터는 규칙에 안 넣는다** — `linkedin/raw`(Apify 원본), `_archive/`(옛 시스템 데이터: net-sourcing raw 161MB 포함) 는 영구. 규칙에 없는 glob 은 건드릴 수 없다.

```bash
cd ~/kv
./kv status | health | digest | backup | retention [--dry-run]      # kv 는 리포 루트의 입구. 스킬은 ./kv <스킬> <명령…>
```
