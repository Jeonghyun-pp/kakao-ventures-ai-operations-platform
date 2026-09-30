---
name: appstore
description: 앱스토어 25장르 × 4차트에서 6개월 이내 신규 한국 앱을 모아 노션에 착지시킨다. 국적 판정을 앱마다 저장하고, 심사역의 노션 판정을 정답으로 끌어와 기준을 깎는다. 메일·링크드인·비플랜과 같은 뼈대(문·판정 저장·보고서·health).
schedule:
  - { cmd: collect, when: "매일 09:10", why: "맥이 확실히 깨어 있는 시각. 자다 깨면 DNS 전에 발화한다" }
  - { cmd: remind,  when: "매일 10:30", why: "2주 회차인 날만 보낸다(코드가 센다). 매일 돌아야 쉬는 날·잠든 날 몫이 다음 평일에 나간다" }
---

# 앱스토어 소싱

2026-09-26 에 메일과 같은 뼈대로 옮겼다(`kvlib` store·gate·report·judgments). **수집·필터·착지 동작은 그대로다** — 골든 dry-run 이 전·후 같은 앱을 고른다.

## 🔴 밖으로 나가는 것은 둘 — 노션 착지 · 격주 리마인드. 문은 **열려 있다**

```
constraints.yaml  appstore.outbound: "on"     ← 살아 있는 시스템이다. off 는 시험·중단용
  land    노션 착지                             off 면 계획만 적는다(actions:dry)
  remind  #그물망 "앱스토어 보세요" (2주마다)      slack 이상이면 나간다. 한 회차 한 번(actions 키 remind:<회차>)
```
`--dry-run` 은 이 실행만 문을 내리는 것. 무엇을 했(을)는지는 `~/kv-data/appstore/db/appstore.sqlite` 의 `actions` 에, **판정은 `judgments` 에** 남는다.
장부(`~/kv-data/_ledger/appstore.jsonl`)는 그대로 한 일의 기록이고 "처음 보는 앱"의 기준이다.
슬랙으로 나가는 것은 review_test 요약(저녁 ops digest 로 묶임)과 #그물망 격주 리마인드 둘. 심사역 카드는 없다 — 심사역은 노션 뷰를 보고, 리마인드가 2주마다 그 뷰를 가리킨다.

## 하루

```
09:10  collect   25장르 × 4차트 → 처음 보는 앱 → 6개월 이내 → 국적 판정(규칙 L1~L7 → 애매하면 모델, criteria.md)
                 · **앱마다 저장**(무엇을 보고 kr/foreign 이라 했나) → One Liner 요약(claude, 40개씩)
                 · 한국·미상 앱만 착지(문 뒤) → 하루 보고서(파일 + review_test 요약)
10:30  remind    2주 회차(기준일 remind_anchor 부터 14일 간격)인 날만. 쉬는 날이면 다음 평일, 다음 회차는 간격 그대로
                 · #그물망에 뷰 링크 + 지난 14일 착지 수 [사용자 결정 2026-09-29 회의 — 뷰는 아무도 안 열면 없는 것과 같다]
12:55  ops health  오늘 보고서 파일이 없으면 review_test 에 한 줄 (skills/ops 가 여섯 스킬을 한 번에)

🔴 왜 9시 이후인가: 맥이 자다 깨면 launchd 가 밀린 작업을 **즉시** 발화하는데, 그때가 DNS 가 아직 안 올라온 순간이다.
   옛 시스템이 그걸로 76요청을 전멸시킨 적이 있다.
```

## 사람이 하는 것 — 기준 깎기

foreign 판정은 노션에 안 간다. **아무도 못 본다.** 그래서 이 스킬의 갈림은 두 종류고 보는 자리가 다르다.

```
억울한 한국 앱   보고서 첫 절 = foreign 표본(foreign_sample 건 · 앱·개발사·도메인·근거). 5초씩 훑는다
정답            노션 `DB로 업데이트` 체크(또는 옛 `판정=Pass`) = "볼 만했다"(pick). label 이 human 으로 적는다.
                모델이 살린 앱이 체크되면 살린 게 맞았다는 뜻. Drop 은 국적과 무관한 결정이라 정답으로 안 센다
                `판정` 칸은 9/22 체크박스로 바뀐 뒤 안 쓴다(마지막 값 9/4 수집분) — 옛 정답을 읽으려고 남겨 둔다
기준            규칙 → skills/appstore/kr_origin.py (고치면 tests/test_appstore_origin_fixture.py 가 유실 0 을 잰다)
                모델 → skills/appstore/criteria.md
확인            run replay [--limit N] [--criteria 파일]   저장된 입력으로 규칙+모델 다시 (애플 0 · 노션 0)
                run diff --b r-<sha>                        무엇이 달라졌나 · 정답과는 어떤가
```

## 명령

```bash
cd ~/kv
./.venv/bin/python -m skills.appstore.run collect [--dry-run] [--limit N] [--genres N]
./.venv/bin/python -m skills.appstore.run remind [--dry-run]
./.venv/bin/python -m skills.appstore.run status | label
./.venv/bin/python -m skills.appstore.run replay | diff --b r-<sha>
./.venv/bin/python tools/kr_origin_eval.py [--verbose]          # 규칙만 · 고정 정답 199건 건별로
```

## 착지 이후는 사람과 노션이 한다 (2026-09-23, 9/30 심사역 피드백 반영)

```
앱스토어 DB "[주차] 유입 추이" 뷰   심사역·인턴이 훑는다. 필터 Captured At ≥ 2026-09-08(고정 — 사용자 결정 9/30, 그대로 둔다)
                                  체크해도 뷰에서 안 사라진다(9/28 체크박스 조건 뺌). 서비스명 옆에 Chart·Chart Rank
"DB로 업데이트" 체크              노션 자동화가 즉시 극초기 스타트업 DB 에 행을 만든다
                                  발굴자 = 체크한 사람 · 발굴 = 앱스토어 · Status = 1. 미팅여부 결정전
극초기 DB "🌊 그물망" 뷰           Service Central 링크 뷰 = 최근 2주(오늘 기준 상대 날짜) · 슬랙 발송 전 · Status ≠ 6. 드랍완료
                                  원본 DB 뷰는 따로다(8/1 이후 고정). 상대 날짜는 API 로 못 쓴다 — 노션 화면에서만
안 볼 앱                          Status 를 6. (소프트 포함) 드랍완료 로. `패스` 체크박스는 9/30 에 지웠다(36행은 Status 로 옮김)
논의 포인트(극초기 DB)             사람이 쓴다. 형식 = 사업·팀 한두 줄 + 왜 만나보고 싶은지(에반 의견). 압축 메모·화살표 금지(9/30 피드백)
```
**One Liner 는 claude 요약이다(9/30).** "누구를 위해 무엇을 하는 서비스인지" 40자 안팎. 설명 원문은 `Raw Text` 에 그대로.
요약이 실패한 앱만 설명 앞 120자(줄바꿈 제거). 극초기 DB `한 줄 설명` 으로도 이 값이 넘어간다. 9/8 이후 행은 9/30 에 일괄 교체했다.
🔴 **`port` 는 수동 명령으로만 남아 있다.** 자동화와 겹치면 극초기 DB 에 두 줄씩 생긴다. 자동화가 죽었을 때만 손으로.
**픽(오늘의 앱 고르기)·슬랙 카드는 2026-09-07 에 없앴고 코드도 9/26 에 지웠다.**

## 구성

```
run.py         collect · 판정 저장 · One Liner 요약(one_liners) · 착지 · 보고서 · label/replay/diff · 진입점
kr_origin.py   국적 규칙 L1~L7(도메인·법인 표기·문자·한글 비율) + 모델 호출(criteria.md 를 붙인다)
criteria.md    모델이 볼 국적 기준 — 사람이 깎는다
fixtures/      kr_origin_w34.json 고정 정답 199건
```
`constraints.yaml appstore.*`: outbound · report_channel(test) · report_slack · foreign_sample · backup_keep · 차트 천장 · remind_*. 공휴일은 `calendar.holidays_kr`(kvlib/workday). **근거는 그 주석에**.

## 지키는 규율

```
착지 먼저, 장부 나중     순서가 반대면 착지 실패한 것이 "처리됨"으로 남아 영영 안 잡힌다
빈 배열은 측정된 0       카지노 유료 차트는 애플의 정답이다. 결손이 아니다
미상은 0건이 아니다      망이 죽으면 unknown 이고 종료코드가 0이 아니다
쓴 것은 되읽는다        200 은 저장을 뜻하지 않는다
옵션은 라이브에서       코드 상수를 믿으면 사람이 추가한 옵션을 되돌린다
보고는 장부에서         claude 기억이 아니라 줄 수를 센다
한 방향으로만 틀린다    확실하지 않으면 kr. 한국 앱 유실 0 이 통과선, 외국 제거율은 그 다음
```

## 알아둘 것

**한국팀 여부를 정규식으로 맞히려 하지 마라.** 옛 시스템이 실측했다 — 넓게 잡으면 424개 중 225개가 한국으로 찍히는데 절반이 오탐이고,
좁게 잡으면 실제 123개 중 100개를 놓친다. 그래서 규칙은 확실한 것만 가르고 나머지는 모델이, 도메인은 노션에 그대로 남겨 사람이 0.5초에 본다.

**`genre=36` 단독으로 쓰지 마라.** 신규진입 29건 중 24건이 홈플러스·배민·라인의 시즌성 재진입이었다. 25개 카테고리로 쪼개야 극초기 앱이 보인다.

**최신출시 피드는 genre 를 무시한다.** 장르별로 부르면 같은 응답을 25번 받는다. 코드가 그 호출을 거부한다.
