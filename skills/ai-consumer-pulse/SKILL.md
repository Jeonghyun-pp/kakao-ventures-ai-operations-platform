---
name: ai-consumer-pulse
description: >-
  카카오벤처스 AI Consumer Thesis 트랙 2 — 검증된 40+ 소스(VC 블로그·팟캐스트·유튜브·X·뉴스레터)를
  주 1회 수집·분석해 "핵심 주장 / 신규 신호 / 빈도 변화" 다이제스트를 만든다. 수집(무LLM)과
  분석(LLM 2-pass)이 분리돼 있고, org 단위 교차검증으로 VC 에코챔버를 걸러낸다. v1 산출물은
  digests/<주차>.md 로컬 파일 (Notion/Slack 발행은 v2).
  Triggers on: "펄스", "주간 신호", "컨슈머 펄스", "consumer AI 다이제스트", "주간 다이제스트 돌려줘", "/ai-consumer-pulse".
  Do NOT trigger for: 그물망 스타트업 소싱(→ net-sourcing), 메일 트리아지(→ hello-mail),
  앱스토어 순위 보드(→ appstore-rank), 소스 목록 자체의 갱신(일반 세션에서 kol-source-list.md 편집).
---

# AI Consumer Pulse

주간 다이제스트는 "이번 주 요약"이 아니라 **"검증 가능한 주장의 원장"**이다 — 모든 항목이
원문 인용과 딥링크를 갖고, 수치는 결정적 스크립트가 계산하며, 사람은 검수와 승인만 한다.

> **상태(2026-07-17)**: v1 빌드 완료. Apify 액터 2종(X·LinkedIn)은 스모크 미실행(UNVERIFIED) —
> 첫 유료 실행에서 입력 스키마 확정 필요. hnrss는 간헐 502(자체 회복). 발행은 로컬 파일까지.

<HARD-GATE>
Phase C(LLM 분석) 시작 전 반드시: (1) `raw/<이번주차>/`에 이번 실행분이 존재하고
(2) `prefilter`가 queue.json을 생성했어야 한다. 수집 없이 분석하지 않는다. 예외 없음.
Phase B(유료 수집) 실행 전 반드시: 예상 비용을 출력하고 사용자 승인을 받는다. 예외 없음.
</HARD-GATE>

## Project 위치

```bash
PROJ="$HOME/.claude/skills/ai-consumer-pulse/project"
PY="$PROJ/.venv/bin/python"          # 항상 절대경로 venv 파이썬
DATA="$HOME/Desktop/ai-consumer"     # sources.yaml·raw/·digests/ (데이터는 여기)
cd "$PROJ" && export PYTHONPATH="$PROJ"
```

## Non-Negotiable Rules

<CRITICAL>
**#1 — 유료 실행 승인 게이트**: Apify(`apify_x`, `apify_linkedin`)는 `--estimate`로 예상 비용을
출력하고 사용자 y/n 없이 `--run` 금지. net-sourcing에서 2026-07-10 무승인 6런이 $5.94를 태워
월 한도를 넘긴 사고가 원인이다. 대신: 항상 estimate → 승인 → run 순서. 계정은 net-sourcing과
공유($29/월)이므로 preflight 경고가 뜨면 실행하지 않는다.

**#2 — evidence_quote 없는 주장 게재 금지**: Pass 1 산출의 claim에 원문 인용이 없으면 그 claim은
무효, 다이제스트에 못 올라간다. 인용이 환각 방지이자 사람 검수의 최소 단위다. 대신: 인용을
찾을 수 없으면 claim을 버려라 — 지어내지 마라.

**#3 — 수치는 aggregate.py만 계산**: 다이제스트의 카운트·rising/cooling·org distinct를 LLM이
재계산하지 마라. 세션마다 숫자가 달라지면 시계열이 무너진다. 대신: `themes.json`의 수치를
그대로 인용하고 LLM은 해석·서사만 쓴다.

**#4 — UNCODED 억지 분류 금지**: 8버킷에 안 맞는 주장을 우겨넣지 마라. UNCODED가 커지는 것
자체가 핵심 신호다(9번째 버킷 후보). 대신: bucket=UNCODED + uncoded_reason 한 줄.

**#5 — 자동자막 인용은 패러프레이즈 표기**: 아이템 메타 `quote_verbatim: false`(유튜브 ASR)면
다이제스트에서 따옴표 인용하지 마라 — 오인식 문장을 인용으로 굳히게 된다. 대신:
"(자막 기반 패러프레이즈)"를 붙여 렌더링.

**#6 — light 트랙 승격 금지**: TBPN·bensbites·neuron은 제목/도입부만 수집한다. full로 올리면
주당 토큰이 수백 k 폭증한다(TBPN 혼자 주 15시간 분량). 대신: light 산출물은 교차검증 표가
아니라 "애그리게이터-only 감사"의 입력으로만 쓴다.
</CRITICAL>

→ 상세: `references/index.md` (소스별 함정·검수 기준은 필요할 때만 로드)

## 서브커맨드

| 커맨드 | 하는 일 | 부수효과 |
|---|---|---|
| `bash "$PROJ/collect.sh"` | Phase A: 무료 수집(rss→scrape→youtube→podcast→ycoss) + manifest | raw/ 저장, seen/health 갱신. **과금 없음** |
| `"$PY" -m adapters.apify_x` | X 비용 추정만 (기본값) | 없음 |
| `"$PY" -m adapters.apify_x --run` | X 28계정 수집 | **과금 ~$0.3** — 룰 #1 승인 필수 |
| `"$PY" -m adapters.apify_linkedin --run` | LinkedIn 4곳 수집 | **과금 ~$0.05** — 룰 #1 승인 필수 |
| `"$PY" -m adapters.gmail_newsletter` | 이메일 전용 뉴스레터 수집 | Gmail 읽기만 (mutation 없음) |
| `"$PY" -m core.prefilter` | Pass 0: full/light/skip 큐 생성 | analysis/큐 저장 |
| `"$PY" -m core.aggregate` | 결정적 집계 → themes.json | trends 원장 갱신 (재실행 멱등) |
| `"$PY" -m core.audit` | 침묵 소스·놓침 후보·스팟체크 대상 | analysis/audit.md |

## 주간 실행 절차 (Phase A→E)

### Phase A — 무료 수집 (사람 0분)
1. `bash "$PROJ/collect.sh"` 실행. manifest의 "신규 N건 / 추정 토큰 / 침묵 소스"를 사용자에게 보고.
2. 침묵 소스가 있으면 audit 결과와 함께 Phase D에서 다룰 것을 예고만 (여기서 수리 시도 금지 — 수집을 막지 마라).

### Phase B — 유료·계정 수집 (사람 승인 2분)
1. `apify_x` + `apify_linkedin`을 **estimate 모드로 먼저** 실행 → 합계 비용 표시 → **사용자 y/n**.
2. 승인 시 `--run`. 실과금 delta가 추정의 2배를 넘으면 즉시 보고.
3. `gmail_newsletter` 실행 (무과금, 승인 불필요).

### Phase C — LLM 분석 (사람 0분)
1. `prefilter` 실행 → queue.json의 full 목록 확인. 첫 실행 백로그가 아니라면 주당 30~60건 수준.
2. **Pass 1**: full 큐 아이템을 서브에이전트 배치로 추출. 프롬프트 = `$DATA/prompts/pass1_extract.md`
   + `$DATA/codebook/v2.yaml` + registry slug 목록 + 아이템 본문. 산출은 `analysis/<주차>/items.jsonl`에
   아이템당 1줄 append (중단돼도 이어서 재개 가능). 배치당 5~10 아이템, 병렬 서브에이전트 허용.
3. `aggregate` → `audit` 순차 실행.

### Phase D — 사람 리뷰 (15~20분)
`references/ops-checklist.md`를 로드해 그 체크리스트로 진행: 스팟체크 2~3건(인용-원문 대조),
NEW: 테마 승인/병합, 워치리스트 판단, 애그리게이터-only 훑기, 침묵 소스 확인.
수정 지시는 `$DATA/feedback/<주차>.md`에 기록 (다음 주 Pass 2에 재주입).

### Phase E — 다이제스트 (사람 2분)
1. **Pass 2**: `$DATA/prompts/pass2_digest.md` + items.jsonl + themes.json + registry + 지난 다이제스트
   + feedback으로 다이제스트 작성 → `$DATA/digests/<주차>.md` 저장.
2. 사용자에게 파일 전달. (v1은 여기서 종료 — Notion/Slack 발행은 v2)

## Quick Reference — 임계값

| 항목 | 값 | 위반 시 |
|---|---|---|
| 핵심 주장 승격 상한 | 7 | 하드캡 — 넘기지 마라 |
| 신규 신호 게이트 | 3주 내 독립 org ≥2 + 상이한 event_ref | 미달 시 워치리스트 |
| rising | 4주 평균×2 이상 AND 절대 ≥3 AND org ≥2 | aggregate가 판정 |
| 일간 소스 캡 | org×테마 주간 기여 ≤3 | aggregate가 적용 |
| Apify 1회 차단선 | $1.5 (config.apify.budget_usd) | 초과 시 자동 차단 |
| Pass 1 배치 | 아이템 5~10개/서브에이전트 | 초과 시 추출 품질 저하 |
| 주 1회 실행 | TBPN RSS 15개 한도 | 격주 실행 시 TBPN 유실 |

## 주의 / 한계

- 첫 실행(2026-W29)은 피드 백로그 포함 559건 — 정상 주간은 30~60건. 백로그 주간엔 Pass 1을
  published 날짜 최근 2주로 제한하거나 사용자와 범위 합의.
- Apify 액터 입력 스키마 UNVERIFIED — 첫 `--run`에서 0건이면 `raw/_actor/` 원본과 에러 본문으로
  payload 교정 (룰 #1의 승인 하에서만).
- Minus One 유튜브 자막 매칭 미구현(RSS 설명으로 폴백 중), LinkedIn JSON-LD 무료 교차검증 미구현 — v1.1 후보.
- 코드북 개정은 `codebook/` v2 복사 + CHANGELOG 기록. 과거 재분석 여부는 격주 검수에서 결정.
