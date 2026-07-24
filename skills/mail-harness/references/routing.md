# 카테고리 · 라우팅

노션 「Hello 계정 메일관리 가이드라인」의 처리 규칙을 코드가 실행하는 형태로 옮긴 것.
구현: `project/triage/core/route.py`. (hello-mail에서 승계, 2026-07-17 정합성 수정 반영판)

## 액션 등급

| 액션 | 뜻 | 위험도 | 자동? |
|------|----|-------|------|
| `protect` | 아무것도 안 함 (비플랜 접수 메일 — bplan sync 소관) | — | 항상 |
| `archive` | INBOX 라벨 제거 (되돌릴 수 있음) | L0 | 자동 |
| `reply` / `forward` | 답장/포워드 **초안** | L1 | 초안만 (P6부터 승격 카테고리는 지연 발송) |
| `escalate` | 인박스에 두고 다이제스트 🚩 | L2 | 영구 사람 |

## 카테고리 표

| 카테고리 | 판정 기준 | 액션 |
|---|---|---|
| `BPLAN_INTAKE` | 제목 `[카카오벤처스]…로부터 접수된 사업계획서` | `protect` |
| `AD_NOISE` | List-Unsubscribe / 발신처 denylist / 제목 `(광고)` 등 | `archive` |
| `MASS_BLAST` | 수신자 20명 이상 | `archive` |
| `INBOUND_EN` | 영문 콜드 투자 제안 | `archive` (auto-reply 기발송) |
| `WEBFLOW` | 발신 `no-reply-forms@webflow.com` → `문의 유형` 파싱 | 하위 분기 ↓ |
| `INBOUND_KO` | 한국어 콜드 투자 제안 | `reply` 구글폼 안내 |
| `TRUSTED_REF` | 내부 포워딩·증권사 추천 등 신뢰 경로 | `reply` 바로 접수 안내 (**never_auto_send**) |
| `EVENT_SPEAKER` | 행사 초청·강연/심사위원 섭외 | `forward` → #Squad-비플랜 |
| `PARTNERSHIP` | 제휴·협력·솔루션 도입, 학회 협업, 기업 탐방 | `forward` → 슬랙 + 거절 초안 (**거절은 never_auto_send**) |
| `LP_CUSTODY` | 출자 제안, 조합 수탁·설정 | `forward` → #Squad-비플랜 (관리팀 직행은 확인 후) |
| `RECRUIT` | 채용·인턴·커리어 문의 | **`escalate`** |
| `IMPERSONATION` | 카카오벤처스 사칭 확인 문의 | **`escalate`** |
| `NAMED_PARTNER` | 특정 심사역 지명 (명단: config `partners` — reviewer-one·reviewer-two·reviewer-three·zero) | `forward` → 그 심사역 |
| `UNKNOWN` | 확신 없음 | `forward` → #Squad-비플랜 |

## WEBFLOW 하위 분기

수신자는 항상 본문에서 파싱한 `이메일:` 주소. 파싱 실패 시 초안 없이 `escalate` (룰 #1).

| 문의 유형 | 액션 |
|---|---|
| 투자 문의 | `forward` → 문의자 · 구글폼 안내 (본문 언어로 국/영문) |
| 멘토링 문의 | `forward` → 문의자 · 취업 키워드 있으면 취업 템플릿, 아니면 투자 |
| TIPS 문의 | `forward` → 문의자 · TIPS 템플릿 |
| 섭외 문의 | `forward` → #Squad-비플랜 (대응하지 않음) |
| 그 외/미상 | `escalate` |

## reclassify 판정 기준 (Claude가 UNKNOWN을 분류할 때)

애매하면 UNKNOWN 유지. 아래는 갈림길만.

- **INBOUND_KO vs PARTNERSHIP**: "우리에게 투자해달라" = INBOUND. "우리 솔루션을
  쓰거나 포트폴리오사에 소개해달라" = PARTNERSHIP. 판단 근거는 **돈의 방향**.
- **PARTNERSHIP vs LP_CUSTODY**: 상대가 **자금을 넣거나 펀드 행정을 대행**하겠다면
  LP_CUSTODY. **자기 상품/서비스를 팔려는** 것이면 PARTNERSHIP. 패밀리오피스류
  "인사드립니다"는 둘 가람 수 있다 → UNKNOWN 유지.
- **TRUSTED_REF**: 콜드가 아니라는 **근거가 본문에 있어야** 한다(내부 포워딩, 소개).
  근거 없이 추정 금지 — 틀리면 구글폼을 건너뛴 특혜가 나간다.
- **EVENT_SPEAKER vs AD_NOISE**: **역할을 요청**하면(연사·심사위원) EVENT_SPEAKER.
  행사를 **알리기만** 하면 AD_NOISE.

```bash
"$PROJ/.venv/bin/python" "$PROJ/triage/main.py" reclassify <msg_id>=INBOUND_KO <msg_id>=RECRUIT
```
