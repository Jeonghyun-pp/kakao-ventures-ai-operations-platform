---
name: fund
description: 심사역이 슬랙 스레드에 "○○ 제안서" + 노션 제안서 DB 링크 + 공고 서식 hwp 를 올리면, 노션 원문을 그 서식으로 옮긴 한글 파일을 같은 스레드에 올리고, 노션이 바뀔 때마다 다시 올리고, ✅ 시점 제출본을 정답으로 남긴다. 투심 레인과 같은 뼈대. 모델 호출 없음 — 글자는 노션 원문 그대로.
---

# 펀드제안서(한글) 요청 레인

2026-09-26 에 만들었다. 8월 KIF(제출됨, item 스크립트 + 게이트)와 9월 LP 성장펀드(서식 맞추기에 이틀, 결국 사람이 씀) 두 실전에서 굳은 절차를 코드로 옮겼다.
0단계 실측(LP 성장펀드 DB 13페이지·643블록): **14초**에 표 25개·문장 260개·셀 2,967개를 옮기고 원문 대조 누락 0·불일치 0, 레코드 정합성 이상 없음.

## 🔴 밖으로 나가는 것 — 스레드 글·파일뿐, 문은 **닫혀 있다**

```
constraints.yaml  fund.outbound: "off"     off = 기록만(actions:dry) · slack = 스레드에 올린다. on 은 없다
  ack       접수 답글(DB 링크·서식 hwp·그림 있나)                    SLACK_KINDS 허용 목록 — 그 밖의 종류는 기록만
  deliverN  vN.hwp 올리기(files:write 필요)
  noteN     원문 대조 결과 · 판단 필요 목록 · (v1) 이 서식으로 읽었다 표
request_channel: test   실채널은 사용자가 정한 날 slack.register 한 줄 + 이 값
요청자 = people.yaml 의 slack_id + fund.extra_requesters. 명단 밖은 조용히 무시(기본 거부)
```
노션에는 쓰지 않는다. **모델을 부르지 않는다** — 심사위원이 원문과 대조하는 문서라 글자 하나도 지어내거나 다듬지 않는다(이관 규칙 #5).
판단이 필요한 것(슬래시로 나열한 셀 · 미확보 그림 · 목차 밖 항목 · em dash)은 고치지 않고 안내글과 보고서에 적는다.

## 하루

스케줄은 아직 frontmatter 에 선언하지 않았다(투심·비플랜과 같다 — 설치는 사용자 결정). 켤 때 `schedule: [{cmd: followup, when: 10분마다}]` 를 넣고 `./kv install`.

```
10분마다 followup   채널 최상위 글에 "제안서" + 명단 안 사람 → 요청(key = 글 ts). 제목 = 요청 문구 앞
                    → [ack] 스레드에서 노션 DB 링크(32자 id)·서식 hwp(사람이 올린 첫 .hwp)·그림(png/jpg)을 찾아 있나 없나 답한다
                    → 둘 다 있으면 v1: 노션 DB 행(항목번호·항목명·대섹션) + 본문 행의 블록 전부 → ~/kv-data/fund/requests/<ts>/notion.json
                      → 문서 모델(convert) → 서식 프로필(profile.json, 처음 한 번 서식에서 배움) → hwp(build) → 검수(audit) → [deliverN·noteN]
                    → 노션 본문 행의 last_edited 가 마지막 빌드보다 뒤면 v+1 (max_versions 까지)
                    → 스레드 글은 feedback 으로 기록만(서식 피드백 = profile.json 손질 재료). 사람이 hwp 를 올리면 그게 최신 기준
                    → 심사역 ✅ → 완료. 그 시점 hwp(사람이 올린 제출본이 있으면 그것) = 정답 → judgments human
12:55 health        ops 가 본다(오늘 요청이 있었는데 접수가 없나)
18:30 digest        하루 보고서: 🚩 제출본에서만 고친 문장 · 서식 실측 vs 프로필 · 서식 피드백 · 판단 필요 합계
```

## 무엇을 어떻게 옮기나 (convert.py)

| 노션 | 문서 |
|---|---|
| DB 행 `대섹션` / `항목명` "1-2. 운용 전략 / 나. …" | 대분류(쪽나눔 뒤) / 항목 제목 / 소제목(페이지 안에 같은 제목이 없을 때만) |
| 항목명이 `N-N.` 꼴이 아닌 행(작성 지침·PT·실사 제출분) | **본문 밖** — 옮기지 않고 "밖에 둔 항목"으로 보고 |
| heading_1~4 | 가. / 1) / 가) / (1) — 크기 사다리는 프로필 |
| 문단 · 불렛(중첩 깊이) · `**볼드**` | 본문 · ○ / - 글머리 문단 · 런 볼드 |
| 표(머리행 여부) | 표 1:1(행·열·셀). 셀 안 줄나눔은 문단으로 분할(내어쓰기 규칙 #1) |
| `[표 제목]` · `(단위 …)` · `* 주석` | 볼드+밑줄 가운데 · 오른쪽 정렬 · 주석 글꼴 9pt |
| `📁 HWP 삽입 파일: …png` | 그 줄은 옮기지 않고, 스레드에 올린 같은 이름의 그림을 본문 폭·원본 비율로 넣는다. 없으면 미확보로 보고 |
| 토글과 그 안 · 콜아웃 · ✅❓[보완필요] 줄 · 삽입 예정 자리표시 | 옮기지 않는다. **지운 것은 전부 findings 에** |

## 서식은 어디서 오나 (build.py · profile.json)

서식 파일에서 **배우는 것**: 글꼴 · 줄간격 · 표 머리행 색 · 셀 여백 · 셀 테두리 가족(머리행·둘째행·본문·마지막행 × 좌중우 BorderFill id) · 쪽 여백(검사 프리셋).
constraints `fund.*` 에서 오는 **기본값**: 크기 사다리(16/15/13/12/11 · 본문 11 · 표 10 · 주석 9) · 불렛(○ 5pt 견고딕 3100/-3100 · - 5100/-2000) · 그림 폭 170mm.
요청마다 `profile.json` 으로 복사되어 **사람이 손질하는 눈금**이다(v2 부터 그 값으로). v1 안내글의 "이 서식으로 읽었다" 표를 한 번 훑는 것이 사람 몫 — 여기서 틀리면 뒤가 다 틀린다(9/9 실측).
✅ 시점 제출본을 실측(format-learn)해 프로필과 다른 값을 보고서에 적는다 — 다음 공고 기본값 재료.

빌드 순서(둘 다 실전에서 굳음): 서식 복사 → 본문 비우기(DocInfo 유지) → op 한 벌(rhwp 전체 직렬화) → 표 글꼴·머리행 볼드 → 셀 줄나눔 분할 → 불렛 내어쓰기(문단 번호로) → 바이트 패치(글꼴 슬롯·테두리·줄캐시·상속 indent, CFB 한 번 쓰기) → 검사 셋.
**파일은 늘 새 이름 vN.hwp** — 한글에 열린 파일에 쓰면 반영이 안 된다(8~9월 열 번).

## 검수 (audit.py + 엔진 검사기) — 전부 통과해야 올린다

| 검사 | 무엇 | 어디서 |
|---|---|---|
| 원문 대조 | 노션 문장·제목·불렛이 hwp 에 전부(공백·볼드 마커 무시, 그 외 완전일치) · 표 셀 1:1 · 작업용 문구 0 | audit.transfer |
| 레코드 정합성 | CFB·FAT·레코드·압축 꼬리·병합 폭 — 한컴이 거부하는 모순 | engine/hwp/audit_structure.py |
| 서식 프리셋 | 쪽 여백·줄간격·문단간격·자간·표 머리행·셀 여백·헤딩 앞 빈 문단·본문 크기 | engine/hwp/audit_format.js |
| 사람 | **한글에서 열리는가·보기 좋은가**는 정적 검사가 보장하지 않는다 | 안내글이 매번 부탁한다 |

## 명령

```bash
cd ~/kv
./kv fund followup [--dry-run]                                              # 요청 채널 한 바퀴
./kv fund run --title "LP성장펀드" --db <노션 DB id 또는 링크> --form <서식.hwp> [--images <폴더>]   # 슬랙 없이 한 건 → ~/kv-data/fund/requests/local-…/vN.hwp
./kv fund status | report
```

## 구성

통로는 `kv_transport/hwp.py`(엔진 스크립트를 띄우는 유일한 자리) + `kv_transport/notion.py` 끝의 `page_blocks`·`download`. 엔진은 `skills/fund/engine/`(`hwp/` = 들여온 rhwp 스크립트·vendor, `patch.mjs` = 바이트 손질 셋).
`constraints.yaml fund.*`: outbound · request_channel · request_pattern · extra_requesters · max_versions · report_* · backup_keep · font · font_note · sizes · bullets · image_*. `hwp.timeout_sec`. **근거는 그 주석에**.
DB `~/kv-data/fund/db/fund.sqlite`(actions · judgments), 요청 폴더 `~/kv-data/fund/requests/<ts>/`(in/ · notion.json · profile.json · vN.hwp · vN/ops.json).

## 사람 몫

슬랙 봇 `files:read`·`files:write`(투심과 같음) · 스케줄 설치(`./kv install`, 사용자 결정) · v1 의 서식 표 한 번 훑기 · 미확보 그림 올리기 · 슬래시 셀 판단 · 제출.
안 하는 것: 목차 밖 항목(PT·실사 제출분)은 요청이 따로 와야 한다(같은 DB, 항목명 규칙을 바꾸는 일이라 사용자 결정) · 셀 병합(양식이 요구하는 격자 병합은 아직 사람) · 페이지 나눔 후가공.
