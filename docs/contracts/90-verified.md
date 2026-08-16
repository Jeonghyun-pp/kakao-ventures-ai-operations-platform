# 에이전트 발견 중 메인 세션이 **기계로 재검증한 것**

에이전트 보고를 그대로 믿지 않는다(빌더≠파인더는 에이전트에게도 적용된다).
아래는 직접 돌려서 확인한 결과다. **승격 2건 · 강등 1건.**

---

## ✅ 확정 — `jobs/*.py` 가 두 스킬에서 리포에 안 들어간다

```
git check-ignore -q skills/<스킬>/project/jobs/_probe.py   (종료코드 0 = 무시됨)

  net-sourcing        1  추적됨   ✅
  mail-harness        1  추적됨   ✅
  investment-memo     1  추적됨   ✅  (루트 .gitignore:48 `!skills/*/project/jobs/*.py`)
  ai-consumer-pulse   0  무시됨   🔴  (skills/ai-consumer-pulse/.gitignore:34  `jobs/`)
  appstore-rank       0  무시됨   🔴  (skills/appstore-rank/.gitignore:34      `jobs/`)
```

2026-08-12 커밋 `bdcace3`(*"소스 jobs/ 가 gitignore 에 먹혀 새 잡 파일이 조용히 누락됐다"*)가
루트·net-sourcing·mail-harness 는 고쳤는데 **자기 `.gitignore` 에 `jobs/` 줄을 가진 두 스킬은
더 깊은 규칙이 이겨서 그대로다.**

**실패 시나리오**: 그 두 스킬에 잡을 새로 만들면 **로컬에서는 돌고 리포에는 없다.**
다른 머신에서 clone 하면 스케줄러가 "러너 미배선"으로 loud 스킵한다 — 원인이 코드가 아니라
`.gitignore` 라 찾는 데 오래 걸린다. 8/12 에 이미 한 번 겪은 사고다.

**수정**: 두 `.gitignore` 의 `jobs/` 뒤에 `!project/jobs/*.py` 추가(net-sourcing 과 동형).

⚠️ **방법 주의**: `git check-ignore -v` 는 **부정 패턴도 출력**하므로 출력 유무로 판정하면
전부 "무시됨"으로 보인다(내가 처음에 그렇게 오판했다). **종료코드로 판정해야 한다.**
→ 계약 축 테스트에 넣을 때 이 함정을 주석으로 남길 것.

---

## ⬇️ 강등 — 링크드인 비용 "12런" 은 **문서 낙후이지 가드 결함이 아니다**

에이전트 보고: *"config.yaml:232 근거가 12런인데 실제 27런 → preflight 가 2.25배 헐거워진다."*

실측:
```
축 6개 · pages 합계 27  (flow 10 · stealth-kr 4 · stealth-us 6 · early-kr 3 · gap 2 · posted 2)
config.yaml:232 주석    "12런(축 12개 × pages 1)"   ← 축 수도 pages 도 둘 다 틀림

그러나 estimate_cost.py 는 keywords.yaml 에서 **런타임에 계산**한다:
  $ tools/estimate_cost.py --channel linkedin
  · 주기 미도래 축 3개 제외 — flow-new-founder, stealth-us-krschool, gap-practitioner
  링크드인 profile_search   런 9 · 아이템 225 · 추정 $1.80 · 천장 $2.34
  ✅ 추정 $1.80 ≤ preflight_max_usd $12.00
```

**게이트는 정확하다.** 주석의 "12런"을 읽는 코드가 없다. 최악(6축 전부 도래)이어도
27런 × $0.20 = $5.40, 천장 $7.02 < 차단선 $16 로 안전 구간이다.

→ **심각도 HIGH → LOW(문서 정정)로 내린다.** 다만 주석이 거짓인 것은 사실이고,
이 리포의 지배적 실패 모드가 "주석을 믿고 임계를 조정하는 것"이므로 고쳐야 한다.

---

## 검증 규율 (재빌드에도 적용)

에이전트가 "게이트가 헐겁다"고 하면 **게이트를 돌려본다.** 코드를 읽고 추론하지 않는다.
오늘 이 한 건으로 HIGH 하나가 LOW 가 됐다 — 반대 방향(LOW→HIGH)도 같은 빈도로 일어난다고
가정해야 한다.

---

## ⬇️ 강등 → 계약 항목 — `endDate` 는 dict 다 (코드는 이미 맞다)

에이전트 보고: *"`endDate` 는 문자열이 아니라 dict(14,902 dict / 0 str). `endDate == "Present"` 로
짜면 전건 False 가 되어 33.9% 보정이 통째로 무효화된다."*

실측(액터 원본 전수, `_is_present` 를 직접 임포트해 돌림):
```
experience 총 14,913 · endDate 타입 {dict: 14,902, None: 11}
_is_present(e) == True : 3,531 (23.7%)

adapters/linkedin.py:1226-1230
    ed = pos.get("endDate")
    if isinstance(ed, dict):
        return str(ed.get("text") or "").strip().lower() == "present"
    return not ed and bool(pos.get("startDate"))
```

**현행 코드는 정확하다.** dict 형태를 이미 처리하고, `endDate` 자체가 없는 11건도
`startDate` 가 있으면 진행 중으로 본다.

→ **버그 아님. 그러나 재빌드 계약에 반드시 들어가야 할 항목이다.**
같은 파일의 독스트링(:1173, :1205)이 `endDate=Present` 라고 **줄여 적어** 있어서,
그 문장만 읽고 다시 쓰면 문자열 비교로 짜게 된다. 그러면 전건 False → 현재 포지션 0 →
`tenure_months`·`창업 개월`·`공백 개월`·지문이 **동시에** 무너진다.

**계약 문구**:
```
제약    harvestapi 액터의 experience[].endDate 는 **dict** `{"text": "Present"|"May 2024"}` 다
증거    액터 원본 전수 14,913건 중 dict 14,902 · str 0 · None 11 (2026-08-16 실측)
대응    _is_present() 가 dict/None 두 형태를 모두 처리
어기면  문자열 비교(`== "Present"`)로 짜면 전건 False → 현재 포지션 0 →
        tenure·창업개월·공백개월·지문이 동시에 무너지고, 그 값은 노션 착지까지 그대로 간다
```

## 📌 확인 — `publicIdentifier` 99.5% 는 **이미 추적 중인 상태**

에이전트: 1,856/1,865(99.5%). 빠진 9건은 `id` 접두 `ACwAA` + `linkedinUrl` 이 URN 형태,
전부 2026-07-14T1220~1223.

`SKILL.md` 의 **[정비] 결함 행 뷰**가 *"`/in/AC` 13건 · 전부 07-27 이전"* 을 기대치로 적어두고
*"07-27 이후 날짜로 뜨면 Short 폴백이 부활한 것"* 이라고 감시 조건까지 걸어놨다.
→ **새 사고가 아니라 선언된 부채다.** 계약에는 "Short 모드가 vanity 를 안 주므로
URL 이 사람이 못 읽는 형태가 될 수 있다"를 남기고, 감시 조건도 함께 옮긴다.

---

## ❌ 반려 — bplan 장부 2본은 "현역"이 아니라 **은퇴 잔재가 맞다**

에이전트 C 보고: *"`mail-harness/bplan/{sync,drop}_ledger.json` 은 은퇴가 아니라 **현역**(29건/35건)."*

실측:
```
drop_ledger.json  4,846B  mtime 2026-07-30
sync_ledger.json  1,503B  mtime 2026-08-02      ← 둘 다 08-13 재빌드 **이전**

쓰는 코드:  skills/_archive/mail-harness-bplan-legacy/bplan/main.py:36-37   ← 아카이브 전용
읽는 코드:  skills/mail-harness/project/core/selfcheck.py:116  변수명이 `_dead`
```

**내용이 있다 ≠ 살아 있다.** 현행 코드 중 이 파일을 쓰는 것은 하나도 없고, selfcheck 는
그것을 "죽은 장부"로 이름 붙여 경고만 낸다. 기존 판단(은퇴 잔재)이 옳다.

→ 재빌드 계약: **이 두 파일은 이관 대상이 아니다. 다만 지우지도 않는다** —
비플랜 롤백이 "옛 코드 복귀"가 아니라 "손으로 돌아가는 것"이라, 그때의 복구 근거일 수 있다.

## ✅ 확정 — 존재하지 않는 상태 파일 3종 (초안 A 정정)

```
net-sourcing/threads_layer_state.json   🔴 없음
mail-harness/triage/processed.json      🔴 없음
_approvals/decisions/                   🔴 없음
```

셋 다 **정상이고, 각각 다른 사실을 말한다:**

| | 뜻 | 재빌드 함의 |
|---|---|---|
| `threads_layer_state` 없음 | 스레드 층 주기 상태가 비어 있다 → config 주석대로 *"비어 있으면 전 층이 한 번은 돈다"* | 첫 실행이 **최악주 요금**이다. 재빌드 후 첫 스레드 수집 전에 이 사실을 알고 있어야 한다 |
| `triage/processed.json` 없음 | `state.mark` 는 `apply` 에서만 불리는데 **apply 는 HARD-GATE 로 막혀 있다** | 기대된 부재다. 컷오버 전까지 트리아지가 매번 같은 메일을 다시 계획하는 것도 정상(plan 파일 12본이 그 증거) |
| `_approvals/decisions/` 없음 | `decide()` 가 프로덕션에서 **한 번도 성공 경로를 안 탔다** | 승인 후반부는 "옮기는 것"이 아니라 **처음 켜보는 것** |

## 📌 정정 — `_usage` ok=true 120/120 vs `gate_warn` 은 불일치가 아니다

에이전트 C: *"`_usage` 120/120 `ok=true` 로 실패 기록 0건인데 같은 날 `gate_warn(claude)` 알림은 떠 있다 — 두 신호 불일치."*

`gate.check_calls` 는 **줄 수**(`calls_today`)를 경고선 30 과 비교한다. `ok` 필드는 보지 않는다.
`ok=true` 만 있는데 30줄을 넘으면 경고가 뜨는 것이 **정상 동작**이다.
→ 불일치 아님. 다만 계약에는 *"`ok` 는 인증 상태 신호이고, 게이트는 줄 수만 본다 — 두 축이다"* 를 남긴다.

---

## 🔴🔴 내 판단을 뒤집는 발견 — 점수 모드는 "장전"이 아니라 **격발 직전**이다

**내가 어제 한 말(틀렸다)**: *"지금은 노션에 `판단 점수` 칸이 없어서 구 경로로 돈다.
사람이 칸을 만드는 순간 코드 변경 0으로 터진다."*

**라이브 스키마 실측(2026-08-16)**:
```
ch_링크드인   판단 점수(number) ✅ 존재 · 판단 이유(rich_text) ✅ · 프로필 점수(number) ✅ · 점수 근거(rich_text) ✅
base_주간소싱  프로필 점수 ✅ · 점수 근거 ✅
                                                          ← 데이터는 4칸 전부 0건
```

**칸은 이미 만들어져 있다.** 오늘 judge 가 Tier 모드로 돈 이유는 칸이 없어서가 아니라
**커밋된 `judge_cli.py` 에 점수 분기 자체가 없기 때문**이다(그 코드는 `wip/score-mode` 브랜치에 있다).

즉 방아쇠는 **이미 당겨져 있고**, 남은 것은 브랜치 병합 하나다. 병합하면 즉시:

| | 실측 근거 |
|---|---|
| `_verdict_field()` 가 점수 모드 반환 | `판단 점수` type == number ✅ |
| `judge-tier` 계약이 "7" 을 전량 격리 → 기입 0 → RuntimeError | contracts.yaml enum 미수정 |
| export 필터 `판단 점수 is_empty` → **1,151행 전량 재큐잉** | 판단 점수 filled **0/1,151** · Tier filled **1,149(99.8%)** |
| meeting-mail 신규 유입 0 | `Tier=="Catch"` 로 모으는데 새 Tier 가 안 생김(기존 Catch 206건만 남음) |

→ **`wip/score-mode` 브랜치의 "병합 금지" 경고는 이 사실 때문에 더 강해져야 한다.**
커밋 메시지에 *"칸을 만들면"* 이라고 적었는데 **이미 만들어져 있다**로 정정해야 한다.

## 🔴 새 발견 — 승격 경로에 조용한 데이터 유실이 장전돼 있다

```
Tier 허용값
  base_주간소싱   Catch Trend Wait Cut
  ch_스레드      Catch Trend Wait Cut     ← Cut 518건 실사용
  ch_링크드인     Catch Trend Wait Cut     ← Cut 239건 실사용
  dedup_극초기    Catch Trend Wait         🔴 Cut 없음
```

노션은 **모르는 select 옵션을 조용히 버리면서 200 을 준다**(`kv_core/notion.py` write 독스트링).
`Cut` 행 **757건** 중 하나라도 극초기 DB 로 승격하면 **Tier 가 소리 없이 사라진다.**
로그는 "✅ 적용", 노션은 빈칸. 이 리포의 단골 실패형 그대로다.

지금은 승격 코드(`notion.stage`)가 미배선이라 미도달이지만, **W11 승격을 켜는 순간 도달한다.**
→ 계약: *"승격 목적지 DB 의 select 허용값은 출발지의 상위집합이어야 한다. 배선 전 기계 검증."*
