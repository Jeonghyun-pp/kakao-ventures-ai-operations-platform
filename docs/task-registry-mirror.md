# Task Registry — 노션 미러 (DRAFT)

> 진실의 출처: 노션 `인턴 업무 자동화` DB (`collection://00000000-0000-0000-0000-e67d1f746034`).
> 이 문서는 그 DB를 코드 세계로 미러링한 것. **노션에 업무가 추가되면 여기 슬롯이 하나 뜬다.**
> registry는 "무슨 업무가 존재하나"(노션)와 "그게 자동화됐나"(코드 status)를 **분리**해 추적한다.
> 생성: 2026-08-03 · 결정 1~12 반영.
> **구현 상태 (2026-08-03):** W4 플랫폼 골격 빌드 완료 — `kv_core.{cadence,registry,scheduler,approval,gmail}` 신설,
> `job.py` 3 seam(run_pipeline·resource_slots·watermark_reader). 다단계 kill-9 재개 실증 통과(스테이지 스킵+항목 재개+멱등),
> 스케줄러 due·catch-up·워터마크 검증, 승인 브릿지 dry(발송 0). 스킬 실 배선(sourcing/bplan/triage)은 유예.

## 범례
- ✅ 완료(코드 존재·검증) · 🔶 부분/컷오버전 · 🔴 미구현(슬롯만) · ⚪ 별도 완료(스킬 미편입)
- **스케줄 task** = cadence 있음(W4 catch-up 대상) · **온디맨드** = 사람이 트리거(catch-up 무관)

---

## 확정 스코프 (2026-08-03, 사용자 결정)

**IN:** 메일관리 · 비플랜(+스코어링+후속미팅) · 그물망(+소싱판단+미팅요청메일) · 투심 · 앱스토어 · IVC · 리서치 · 딜검토
**OUT/보류:** 뷰티(beauty-mail) · 리크루팅 · NPR · 타팀(뉴스레터·뉴스클리핑·브런치) · consumer-pulse · 그물망 퀵얼라인(중단)

핵심 구조 변경: **판단·미팅메일을 별도 task로 두지 않고 부모 스킬에 스테이지로 흡수** (사용자 결정).
- 그물망 소싱판단·미팅요청메일 → **net-sourcing 스테이지**
- 비플랜 스코어링·후속미팅요청 → **bplan 스테이지**

## 공통 서비스 — 승인 브릿지 (kv-core, 로드맵 W8) 🆕

> **모든 판단 결과·발송 대상은 실행 전 심사역에게 초안으로 간다. 심사역이 메시지의 버튼을 눌러야 제품이 발송한다(승인 발송, 자동 발송 아님).**

**승인 방식 (결정 8):** 버튼 발송. 초안 전문을 메시지에 보이고 아래 버튼. 안전장치:
- **멱등 발송** — 두 번 눌러도 1회(events 가드) · **감사 로그** — 누가·언제·무엇 · **초안-only 먼저 출시 → 검증 후 발송 버튼 활성화**(`_assert_no_send` 걷는 순간 통제). 완전 무인 발송은 W10.

**채널 (결정 9, 혼합):** 발송할 메일(미팅요청·드랍) → **Gmail 초안** / 판단·요약 → **Slack**.

**라우팅 (결정 10, 출처 기반):**
- **시스템이 시작**(우리가 발굴/접수: 그물망 소싱판단·비플랜 신규접수) → **팀 슬랙방**(#Squad-비플랜)
- **특정 심사역이 트리거**(노션 Status에 담당 명시: `메일작성(앤)`, 드랍 지시) → **그 심사역 개인**

자원: `slack`·`gmail` 공유 → 자원 락 대상. 기존 `_assert_no_send`와 정합.

## A. 스케줄 task (cadence · W4 catch-up 대상)

| task | 스킬 | 스테이지(파이프라인) | cadence | catch-up·watermark | 자원 | status |
|---|---|---|---|---|---|---|
| `sourcing` | net-sourcing | collect→**judge-sourcing(판단)**→land→**미팅요청 메일초안**→승인브릿지 | weekly | coalesce·last_success | apify·notion·gmail | 🔶 판단 L0만·미팅메일 노션에만 |
| `sourcing-followup` | net-sourcing | DB 인물 재확인 | 3–4개월 | coalesce·last_success | notion | 🔶 골격 |
| `appstore-snapshot` | net-sourcing/board | 랭킹 스냅샷 | daily | **backfill**·last_success | 없음($0) | ✅ 운영중 |
| `mail-triage` | mail-harness | 인박스 sweep→분류→초안→승인브릿지 | daily | coalesce·last_attempt | gmail-inbox | 🔶 컷오버전(W5) |
| `bplan` | mail-harness | sync(접수·배분·기입)→**scoring(판단)**→드랍초안/후속미팅초안→승인브릿지 | daily | coalesce·last_attempt | gmail·sheet-A1·notion-bplan | 🔶 sync/drop✅·scoring/후속🔴 |

## B. 온디맨드 task

| task | 스킬 | 트리거 | status |
|---|---|---|---|
| `investment-memo` | investment-memo | 회사 발생 시 | ✅ 완성(초안) |
| `research` (리서치) | 신규 | 사람 요청 | 🔴 슬롯 |
| `deal-review` (딜검토) | 신규 | 사람 요청 | 🔴 슬롯 |
| `ivc` | 신규 | 매달 | 🔴 슬롯 (**사용자 구축**) |

## C. 스코프 밖 / 보류
`뷰티(beauty-mail)` · `리크루팅` · `NPR` · `타팀 콘텐츠(뉴스레터·뉴스클리핑·브런치)` · `consumer-pulse` · `그물망 퀵얼라인`

---

## 확정된 설계 판단 (2026-08-03)
- **판단은 부모 스킬의 스테이지** — judge-sourcing은 net-sourcing에, judge-bplan은 bplan에 흡수. 편향이 반대(살림 vs 거름)라 기준은 도메인별, **판단 골격**(티어링·evidence·사람검수·오탐 실측)만 kv-core 공유. **제품 완성 후에도 지속 튜닝**.
- **승인 브릿지가 모든 판단·발송의 공통 출구** — 심사역 Slack/메일 초안. (W8)
- **뷰티·consumer-pulse 스코프 제외.**

## 부록: 전체 인턴 업무 지도 (마스터 = "가이드라인 모음집")

TF DB 24개는 **자동화 TF 스코프(부분집합)**였다. 인턴 업무 전체(가이드라인 모음집 2026 하반기 R&R 기준):

| 업무 | 담당 | cadence | 자동화 상태 | 스코프 |
|---|---|---|---|---|
| 메일관리 | 투자팀 인턴 | 매일 | 🔶 mail-triage | ✅ IN |
| 비플랜 | 투자팀 인턴 | 루틴(금) | 🔶 bplan | ✅ IN |
| 그물망 | 투자팀 인턴 | 루틴(수) | 🔶 net-sourcing | ✅ IN |
| 투심보고서 | 투자팀 인턴 | 온디맨드 | ✅ investment-memo | ✅ IN |
| **IVC** | 투자팀 인턴 | 매달 1편 | 🔴 슬롯 | ✅ IN (사용자 구축) |
| **리서치** | 투자팀 인턴 | 온디맨드(상시) | 🔴 슬롯 | ✅ IN |
| **딜검토** | 투자팀 인턴 | 온디맨드 | 🔴 슬롯 | ✅ IN |
| 리크루팅 | 투자팀 인턴 | 온디맨드 | 🔴 | ❌ OUT |
| ~~디헬 뉴스레터~~ | 컴팀 인턴 | 매주 화 | 🔴 | ❌ OUT(타팀) |
| ~~아갈파 뉴스클리핑~~ | 컴팀 인턴 | 매주 목 | 🔴 | ❌ OUT(타팀) |
| ~~브런치 글쓰기~~ | 컴팀 인턴 | — | 🔴 | ❌ OUT(타팀) |
| NPR (New Product Report) | 서비스팀 | 매주 화-수 | 🔴 | ❌ OUT |

**발견된 숨은 노션 "복붙" 스킬 문서:** `auto-mail`·`beauty-mail`(메일) · `kv-linkedin-sourcing`(스킬 md) · `스타트업 센싱 md` — 일부는 net-sourcing에 이미 이식됨.
**핵심 가이드라인 페이지:** Research·디헬뉴스레터·Hello메일관리·BPlan·그물망·아갈파·투심·리크루팅 가이드라인 (각각 registry 슬롯의 근거 문서).

## 미해결 / 확인필요 (설계 단계로 이월)
1. **승인 브릿지 상세** — 심사역별 라우팅(누구 판단은 누구에게), Slack vs 메일 선택 기준, 승인→발송 상태전이.
2. **자원 락 목록 확정** — gmail-inbox · sheet-A1 · notion(bplan/sourcing DB별) · slack · apify.
3. `sourcing`·`bplan` 파이프라인의 **스테이지 경계**(어디까지 한 스테이지, stage_done 어디에 찍나).
