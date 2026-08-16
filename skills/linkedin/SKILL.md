---
name: linkedin
description: 링크드인에서 극초기 창업자를 주 1회 수집해 Tier 를 판정하고, 매일 상위 5명씩 노션에 착지시킨다.
# schedule 은 collect 가 완성된 뒤에 붙인다.
# 🔴 여기 미리 적으면 "선언은 있는데 발동 안 하는" 상태가 된다 — schedule.py check 가 그걸 잡는다.
---

# 링크드인 소싱

## 지금 상태 — **절반만 지어졌다**

```
✅ profile.py   URN 정규화 · 현재직장 병합 · 지문        원본 1,865행으로 검증 (돈 0 · 망 0)
✅ apify.py     돈이 나가는 통로 — 선반영 · 천장 · 아카이브 · 지출 장부
🔴 run.py       collect · land · card                아직 없다
🔴 criteria.md  Tier 판정 기준                        아직 없다
```

**실호출을 아직 한 번도 안 했다.** 회당 $1.6~5.2 이고 월 천장이 $29 라,
사람이 보고 있을 때 첫 호출을 한다.

## 검증

```bash
./.venv/bin/python tools/replay_linkedin.py   # 저장된 원본 1,865행으로 파서 검증
```

리플레이가 [0] 실측을 그대로 재현한다 — URN 100% · publicIdentifier 99.5% ·
endDate 문자열 0건 · currentPosition 이 놓치는 사람 37%.

## 알아둘 것

**`currentPosition` 만 보면 안 된다.** 진행 중 경력을 통째로 누락한다(1,167명 중 396명).
창업 직함이 `experience` 쪽에만 있는 사람은 판정에서 통째로 사라진다.

**`endDate` 는 dict 다.** `endDate == "Present"` 로 짜면 **전건 False** 가 되고
위 보정이 통째로 무효가 된다. 조용히 0이 되는 종류의 버그다.

**URL 을 인코딩하지 마라.** 한글 vanity 가 raw 로 온다(6%). 통일하면 같은 사람이 두 키가 된다.

**점수 매겨서 줄 세우지 마라.** 옛 시스템이 해봤고 스스로 이렇게 적었다 —
*"97명 실측으로 랭킹 정보량이 거의 0. 최상위 밴드 정밀도 21.7%인데 전체 평균이 20.6%."*
판정은 claude 가 한다.
