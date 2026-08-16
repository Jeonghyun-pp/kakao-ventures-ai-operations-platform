# 0% 컬럼 22개 — 성격별 분류 (2026-08-16 실측)

행이 있는 DB 6벌에서 **채워진 행이 0인 컬럼이 22개**다(행 0인 `dedup_bplan2` 제외).

"안 쓰는 칸이니 지우자"는 결론으로 가면 안 된다. 실측해 보니 **0%가 나오는 이유가 네 가지**이고,
그중 둘은 **정상**이다. 이유를 안 나누면 정상인 칸을 지우고 고장인 칸을 남기게 된다.

측정: `docs/contracts/notion/contract.json` 의 `filled == 0` + 리포 전체(py·yaml 4,637본)에
그 이름이 문자열로 등장하는지 대조.

---

## A. 선언상 정상 — 그 칸을 채우는 일이 **원래 안 도는 것으로 정해져 있다** (4개)

| DB | 칸 | 타입 | 0/행 | 근거 |
|---|---|---|---|---|
| base_주간소싱 | `Tier` | select | 0/1470 | `judge_cli._targets(include_base=False)` — **base 는 판정 대상이 아니다** |
| base_주간소싱 | `AI Reason` | rich_text | 0/1470 | 위와 같음(판정이 안 도니 사유도 안 쓴다) |
| base_주간소싱 | `Last Seen` | date | 0/1470 | 착지가 채널 DB 로 가므로 base 행은 갱신되지 않는다 |
| ch_앱스토어 | `판정` | select | 0/2671 | `config.judge.channels = [스레드, 링크드인]` — **앱스토어는 선언에서 빠져 있다** |

🔴 **앱스토어에는 함정이 하나 있다.** `config/contracts.yaml` 에 `judge-appstore`
계약(`Pass|Drop`, `min_records: 1`)이 **살아 있는데 그 스테이지를 부르는 선언이 없다.**
계약만 있고 호출이 없는 상태라, "계약이 있으니 돌고 있겠지"로 읽으면 틀린다.
둘 중 하나여야 한다 — 채널 선언에 넣거나, 계약에 "미가동"이라고 적거나.

---

## B. 배선 안 됨 — 칸은 있는데 **코드가 그 이름을 모른다** (5개)

리포 전체 문자열 검색에서 **0회** 등장한다.

| DB | 칸 | 타입 |
|---|---|---|
| base_주간소싱 | `프로필 점수` · `점수 근거` | number · rich_text |
| ch_링크드인 | `프로필 점수` · `점수 근거` · `판단 이유` | number · rich_text · rich_text |

이 다섯 + `판단 점수` 는 **점수 모드**의 칸이다. 그 분기는 `wip/score-mode` 브랜치에만
있고 `main` 에는 없다 — 그래서 칸이 있어도 아무도 안 쓴다.

📌 **[2026-08-16 확인] 무장해제가 유지되고 있다.** 라이브 스키마에서 그 칸의 실제 이름은
`판단 점수 (재빌드 후 도입)` 이다(사람이 이름을 바꿔 무장해제한 것). 그리고 `main` 의
`judge_cli._verdict_field` 는 `("Tier", "판정")` 만 찾으므로, 라이브에 대고 돌리면
`칸='Tier' 허용값=['Catch','Trend','Wait','Cut']` 를 반환한다(실행해 확인).
⚠️ `notion/contract.json` · `baseline.md` 의 `판단 점수` 표기는 **이름 변경 전 덤프**다.
그 파일들을 근거로 "칸 이름이 아직 그대로"라고 읽으면 안 된다.

---

## C. 배선은 됐는데 **아직 한 번도 안 돌았다** (10개)

코드가 이름을 알고 쓰는 경로가 있는데 값이 0이다. = 그 도구/레인이 이 DB 를 상대로
실행된 적이 없다. **여기가 진짜 "미가동" 목록이다.**

| DB | 칸 | 쓰는 곳 |
|---|---|---|
| ch_링크드인 | `빌더` (checkbox) | `tools/mark_builders.py` — 사람이 손으로 돌리는 도구 |
| ch_링크드인 | `Sourcing Method` (select) | `core/fold.py` |
| ch_링크드인 | `Followers` (number) | `tools/backfill_bio.py` |
| ch_링크드인 | `API Query` (rich_text) | `core/notion.py` |
| ch_링크드인 | `Author Display Name` (rich_text) | 착지 경로 |
| ch_링크드인 | `Last Checked` (date) | `tools/backfill_bio.py` · `backfill_tenure.py` |
| ch_스레드 | `변화 요약` (rich_text) | `tools/recheck.py` · `adapters/linkedin.py` |
| ch_스레드 | `재검토` (checkbox) · `재검토 사유` (select) | `main.py` · `tools/notion_migrate.py` |
| ch_스레드 | `Korea Signal` (multi_select) | `core/judge_cli.py` · `core/notion.py` |
| ch_스레드 | `Last Checked` · `Last Seen` (date) | 리텐션 레인 |

🔴 **`config.retention` 이 비어 있다.** 스레드의 `재검토`·`변화 요약`·`Last Checked`·
`Last Seen` 은 전부 "다시 본다" 레인의 칸인데, 그 레인의 선언이 없다. 칸이 있으니
레인이 있는 것처럼 보이지만 **없다.** 이건 A(선언상 정상)가 아니라 C(미가동)다 —
선언을 지운 게 아니라 애초에 안 쓴 것이기 때문이다.

---

## D. 행이 0이라 분모가 없는 DB (분류 제외)

`dedup_bplan2`(Bplan 2.0 DB (1)) 는 **행 0 · 칸 14개 전부 0%** 다. 이건 "안 채워진"
게 아니라 **안 쓰는 DB** 다(사람 확인 완료). `dedup_db_ids` 에서 이미 뺐다.

---

## 그래서 무엇을 하나

```
A(4)  아무것도 안 한다. 단, 앱스토어는 계약과 선언이 어긋나 있으니 한쪽으로 맞춘다.
B(5)  칸을 지우지 않는다 — `wip/score-mode` 가 들어오면 쓸 칸이다. 지금은 "미배선"으로
      계약에 적어두는 것으로 충분하다. 지우면 브랜치 병합 때 조용히 값이 버려진다.
C(10) 여기만이 결정거리다. 레인을 켤 것인가, 칸을 접을 것인가.
      특히 `config.retention` 이 빈 것은 **칸 6개가 걸린 하나의 결정**이다.
D     이미 처리됨.
```

⚠️ 이 문서의 판정 근거는 **문자열 등장 여부**다. 동적으로 이름을 만드는 코드
(`f"{prefix} 점수"` 같은)가 있으면 B 를 과다 계상한다. 22개 전부를 눈으로 확인했고
그런 형태는 없었으나, 새 칸을 판정할 때는 이 한계를 기억하라.
