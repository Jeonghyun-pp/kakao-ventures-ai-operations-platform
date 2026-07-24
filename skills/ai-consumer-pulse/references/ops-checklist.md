# Phase D — 사람 검수 체크리스트 (15~20분)

세션은 이 체크리스트를 순서대로 사용자와 함께 진행하고, 결과를 feedback/<주차>.md에 기록한다.

## 1. 스팟체크 (5분) — 승격 신호 인용-원문 대조
- audit.md의 "③ 스팟체크 대상" 2~3건에 대해 원문 링크를 열고 evidence_quote가 실제로 있는지 확인
- 불일치 발견 시: 해당 claim 폐기 + feedback에 기록 + 같은 소스의 다른 claim도 표본 1건 추가 확인

## 2. NEW: 테마 승인/병합 (3~5분)
- items.jsonl의 `NEW:` 테마 각각에 대해:
  - **승인** (진짜 신규) → registry.jsonl에 `{slug, name, definition, status: "active", added: <날짜>}` append
  - **병합** (기존 테마의 변형) → 기존 slug로 alias 처리, items.jsonl은 다음 주부터 반영
  - 판단 기준: new_theme_rationale이 기존 테마와의 차이를 설득하는가. 못 하면 병합이 기본값
- 금지: 검수 없이 NEW:를 registry에 자동 등재 (파편화의 시작)

## 3. 워치리스트/패스트트랙 경계 (2분)
- themes.json의 `watchlist: true` 항목 훑기 — "다음 주 2번째 org 나오면 승격"으로 다이제스트에 명시
- 단일 org지만 Tier-1 고권위 정식 thesis 장문(a16z thesis 포스트급)이면 패스트트랙 승격 —
  '단일소스·고권위' 플래그 필수

## 4. 애그리게이터-only 훑기 (3분)
- audit.md "② 애그리게이터-only 테마" — 진짜 놓침이면 원문을 세션에 던져 즉석 Pass 1 편입

## 5. Source Health (2분)
- 🔴 침묵 소스: URL을 한 번 열어 사망/개편/실제침묵 판정. 수리는 다음 주 이월이 기본
  (이번 세션에서 어댑터 수리에 빠지지 마라 — 다이제스트가 우선)
- Apify 실과금 vs 추정 확인 (2배 초과였으면 config 단가 교정 태스크 기록)

## 6. feedback 기록 (2분)
- 위 결정사항 + 다이제스트 서술에 대한 수정 지시를 `$DATA/feedback/<주차>.md`에 기록
- 이 파일은 다음 주 Pass 2 컨텍스트에 재주입된다 — "지난주에 말했는데"가 없게

## 격주 추가 (+5분)
- UNCODED 대기열: 4주 5건 클러스터면 9번째 버킷 후보 논의 → 채택 시 codebook v2 + CHANGELOG
## 월 1회 추가 (+20분)
- raw/ 무작위 5건 리콜 샘플링 (Pass 1이 주장을 놓쳤는지 — 추출 누락 감사)
- registry 정리 (dormant 테마 아카이브)
## 반기
- 연간 리포트 신판 수집 → raw/baselines/ → 시드 재캘리브레이션
