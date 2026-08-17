---
name: linkedin
description: 링크드인에서 극초기 창업자를 주 1회 수집해 Tier 를 판정하고, 매일 상위 5명씩 노션에 착지시킨다.
schedule:
  - { cmd: collect, when: "월 09:10", why: "이때만 돈이 나간다. 축별로 차례인 것만 돈다" }
  - { cmd: card,    when: "매일 11:00", why: "정현님이 훑은 결과가 반영된 뒤에 나간다" }
---

# 링크드인 소싱

## 하루

```
월 09:10  collect   축별로 차례인 것만 → Apify → 원본 아카이브 → 장부로 중복 제거
                    claude 배치 판정(Catch/Trend/Wait/Cut) → **전량** 노션 착지
매일       (사람)     정현님이 노션에서 훑는다. 아니면 Tier 를 고친다
매일 11:00 card      🔴 노션에서 Tier 를 **다시 읽어**, 아직 안 나간 사람 좋은 순 5명
                    Catch 는 장부에 meeting_candidate 만 남긴다 — 메일은 별도 스킬 몫
```

**전량 착지하는 이유**: 심사역이 노션을 직접 열 수 있어야 한다. 슬랙엔 그중 하루 5명만 간다.

## 첫 실호출 (2026-08-17)

```
posted-early 1페이지 · $0.20 · 25명
판정 25/25 (DLQ 0) · 착지 25/25 (되읽기 전부 통과) · Catch 6
```

착지된 회사에 `cPlat AI`·`orbt`·`TraceWeave` 가 있었다 — 옛 설정 문서가
*"서버측 필터를 켜니 회사명이 cPlat AI·TraceWeave·Anymorph·orbt 로 바뀐다"* 고
적어둔 네 이름 중 셋이다. **필터가 문서대로 작동한다는 뜻이다.**

카드는 `review-test` 채널로 나간다. 판정 기준이 아직 초안이라 심사역 채널엔 안 보낸다.

## 명령

```bash
cd ~/kv
./.venv/bin/python -m skills.linkedin.run collect [--axis <축>] [--pages N] [--dry-run]
./.venv/bin/python -m skills.linkedin.run card [-n 5] [--dry-run]
./.venv/bin/python -m skills.linkedin.run status      # 축별 마지막 실행일·다음 페이지
./.venv/bin/python tools/replay_linkedin.py           # 원본 1,865행으로 파서 검증 (돈 0·망 0)
```

`--dry-run` 은 **돈을 안 쓴다.** 계획과 예상 비용만 찍는다.

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
