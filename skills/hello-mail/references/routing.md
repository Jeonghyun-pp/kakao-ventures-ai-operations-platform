# 카테고리 · 라우팅

노션 가이드라인의 처리 규칙을 코드가 실행할 수 있는 형태로 옮긴 것. 구현은 `core/route.py`.

## 액션 등급

| 액션 | 뜻 | 위험도 | 자동? |
|------|----|-------|------|
| `protect` | 아무것도 안 함 | — | 항상 |
| `archive` | INBOX 라벨 제거 (되돌릴 수 있음) | L0 | 자동 |
| `reply` | 원본 스레드에 답장 **초안** | L1 | 초안만 |
| `forward` | 포워드 **초안** | L1 | 초안만 |
| `escalate` | 인박스에 두고 리포트만 | L2 | 사람 |

## 카테고리 표

| 카테고리 | 판정 기준 | 액션 | 태스크 |
|---|---|---|---|
| `BPLAN_INTAKE` | 제목 `[카카오벤처스]…로부터 접수된 사업계획서` | `protect` | — |
| `AD_NOISE` | List-Unsubscribe 헤더 / 발신처 denylist / 제목 `(광고)` 등 | `archive` | 1 |
| `MASS_BLAST` | 수신자 20명 이상 | `archive` | 1 |
| `INBOUND_EN` | 영문 콜드 투자 제안 | `archive` (auto-reply 기발송) | 4 |
| `WEBFLOW` | 발신 `no-reply-forms@webflow.com` → 본문 `문의 유형` 파싱 | 하위 분기 ↓ | 2 |
| `INBOUND_KO` | 한국어 콜드 투자 제안 | `reply` 구글폼 안내 | 4 |
| `TRUSTED_REF` | 내부 포워딩·증권사 추천 등 신뢰 경로 | `reply` 바로 접수 안내 | 4 |
| `EVENT_SPEAKER` | 행사 초청·강연/심사위원 섭외 | `forward` → #Squad-비플랜 | 3 |
| `PARTNERSHIP` | 제휴·협력·솔루션 도입 제안, 학회 협업, 기업 탐방 | `forward` → 슬랙 **+ 거절 회신 초안** | 3 |
| `LP_CUSTODY` | 출자 제안, 조합 수탁·설정 | `forward` → #Squad-비플랜 (관리팀 직행은 확인 후) | 3 |
| `RECRUIT` | 채용·인턴·커리어 문의 | **`escalate`** (룰 #3) | 3 |
| `IMPERSONATION` | 카카오벤처스 사칭 메일 진위 확인 | **`escalate`** (룰 #6) | — |
| `NAMED_PARTNER` | 본문/제목에 특정 심사역 지명 | `forward` → 그 심사역 | 3 |
| `UNKNOWN` | 위 어디에도 확신 없음 | `forward` → #Squad-비플랜 | — |

## WEBFLOW 하위 분기

본문의 `문의 유형:` 값으로 갈린다. **수신자는 항상 본문에서 파싱한 `이메일:` 주소**이며,
파싱 실패 시 초안을 만들지 않고 `escalate`한다 (룰 #1).

| 문의 유형 | 액션 |
|---|---|
| 투자 문의 | `forward` → 문의자 · 구글폼 안내 (본문 언어로 국/영문 선택) |
| 멘토링 문의 | `forward` → 문의자 · 취업 키워드 있으면 취업 템플릿, 아니면 투자(오피스아워) 템플릿 |
| TIPS 문의 | `forward` → 문의자 · TIPS 템플릿 |
| 섭외 문의 | `forward` → #Squad-비플랜 (대응하지 않음) |
| 그 외/미상 | `escalate` |

## 카테고리 판정 기준 (사람/Claude가 reclassify할 때)

애매하면 UNKNOWN. 아래는 갈림길만 적는다.

- **INBOUND_KO vs PARTNERSHIP**: "우리 회사에 투자해달라" = INBOUND. "우리 솔루션을 쓰거나
  포트폴리오사에 소개해달라" = PARTNERSHIP. 판단 근거는 **돈의 방향**이다.
- **PARTNERSHIP vs LP_CUSTODY**: 상대가 우리에게 **자금을 넣거나 펀드 행정을 대행**하겠다면
  LP_CUSTODY(→ 관리팀). 상대가 **자기 상품/서비스를 팔려는** 것이면 PARTNERSHIP.
  패밀리오피스·자산운용사의 "인사드립니다"류는 둘 가람 수 있다 → **UNKNOWN**으로 두고 슬랙에 넘겨라.
- **TRUSTED_REF**: 콜드메일이 아니라는 **근거가 본문에 있어야** 한다(내부 임직원이 포워딩했다,
  누구 소개로 연락한다 등). 근거 없이 추정하지 마라 — 틀리면 구글폼을 건너뛴 특혜가 나간다.
- **EVENT_SPEAKER vs AD_NOISE**: 우리에게 **역할을 요청**하면(연사·심사위원·투자자 미팅 참여)
  EVENT_SPEAKER. 그냥 행사를 **알리기만** 하면 AD_NOISE(아카이빙).
