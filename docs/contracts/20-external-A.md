# [0] 미실측 3종 실측 결과 (2026-08-16, 읽기 전용 프로브)

`external/probe_a.py` · 결과 원본 `external/probe_a_result.json` · **오류 0건 · 쓰기 0회**

---

## ① 구글시트 — **접수키 계약 확정**

```
워크시트  비플랜DB2          5,561행(헤더 포함) × 14열
접두 매칭  14/14 해결 · 모호 0 · 누락 0        ← _resolve_columns 가 그대로 통과한다
```

| idx | 코드 prop | 헤더(접두) |
|---:|---|---|
| 0 | 타임스탬프 | `타임스탬프` |
| 1 | 이메일 | `이메일 주소` |
| 2 | 대표명 | `대표님의 성함을 알려주세요.` |
| 3 | 연락처 | `대표님의 연락처를 알려주세요.` |
| 4 | 서비스정의 | `1. 팀의 Service/Product를 한 문장으로…` |
| 5 | 문제정의 | `2. 문제 정의 : 누구의 & 어떤…` |
| 6 | 솔루션 | `3. 솔루션 : 그 고충/불편을…` |
| 7 | 비즈니스모델 | `4. 핵심 비즈니스 모델(e.g. 거래 수수료…` |
| 8 | 경쟁사 | `5. 핵심 경쟁사들은 어떤 곳이…` |
| 9 | 팀이력 | `6. 대표님과 핵심 구성원(c-level)…` |
| 10 | 경쟁력 | `7. 우리 팀이 이 서비스 & 기술을…` |
| 11 | 추가내용 | `(선택)추가로 공유해 주고 싶으신…` |
| 12 | 사업계획서 | `팀의 사업계획서를 첨부해주세요.` |
| 13 | 기업명 | `기업명을 알려주세요.(Please tell us…)` |

**계약**
```
접수키   = "<이메일>|<타임스탬프>" = r[1] + "|" + r[0]
열 해석  = **접두 매칭**(_COLS). 접두당 정확히 1개 매치여야 하고, 하나라도 실패하면 fail-loud
어기면   그 칸이 빈 채로 노션에 들어가고, bplan-score 가 빈 칸을 근거로 Red 를 낸다
         — 데이터 사고가 판정 사고로 번진다
```

⚠️ **잠재 위험(지금은 우연히 안전)**: `tools/bplan_backfill_key.py:107-110` 이 접두가 아니라
**위치 인덱스** `r[0]·r[1]·r[13]` 을 쓴다. 실측 결과 셋 다 **현재는 맞다**(타임스탬프·이메일 주소·기업명).
그러나 폼에 열이 하나 삽입되면 `r[13]` 이 밀려 **접수키가 잘못 심기고**, sync 의 upsert 가 그 키로
중복을 판단한다. 재빌드는 위치 인덱스를 쓰지 않는다.

---

## ② Gmail — 라벨 315개(사용자 300)

```
계정          deals@example.vc
스코프        gmail.modify   ← send 를 **허용한다.** 무발송 보장은 스코프가 아니라 "코드에 send 호출 0"
config 선언   gmail_label: "3. Evan"      → 라이브에 **존재 확인 ✅**
```

**담당자 계열 라벨(최상위)**: `0. 2025_오피스아워` · `0. 2026_오피스아워` · `0. Recruit` · `2. Sofia` · `3. Evan`

**`BPlan/` 하위 34개 — 노션 필드의 거울이다(사람이 손으로 유지)**
```
점수 태그    BPlan/1. Red tag · 1. Orange tag · 1. Green tag     ← 노션 `점수` 와 대응
담당자       BPlan/ReviewerTwo · 1.ReviewerOne · 1.Zero · 2. ReviewerThree · 2. ReviewerEight  ← 노션 `fwd to whom?` 과 대응
             BPlan/ReviewerFour · ReviewerSix · Jun · Matthew
상태         각 담당자 아래 /Done · /Reviewing
은퇴         BPlan/Old/{Inbae,Jongho}/… · BPlan/z[만기]…
```

**계약**
```
코드가 쓰는 라벨 = "3. Evan" 하나뿐이다(접수 메일 이동). BPlan/* 계층은 **사람의 수동 체계**다
환경변수 BPLAN_GMAIL_LABEL 이 config 를 덮을 수 있다 — selfcheck 는 config 만 보므로 비대칭
```
⚠️ 이름 불일치: 노션 `fwd to whom?` 값은 `zero`(소문자)인데 Gmail 라벨은 `BPlan/1.Zero` 다.
자동 포워딩을 켤 때 **매핑 표가 필요하다**(정확일치로 짜면 안 걸린다).

---

## ③ Slack — 🔴 채널 상태를 **사전 확인할 수 없다**

```
auth.test              ✅ ok · bot=kvautomation · team=Kakao Ventures
conversations.info     🔴 missing_scope        ← 채널 정보 조회 불가
채널                    review_test
웹훅                    설정됨
```

현 스코프는 `chat:write` · `groups:history` · `channels:history` · `incoming-webhook` 이라
**카드 발송과 반응 수거는 되지만 채널 자체를 못 읽는다**(`channels:read`/`groups:read` 없음).

**계약**
```
제약    Slack 채널의 존재·아카이브 여부·봇 멤버십을 **사전 확인할 수 없다**(스코프 부재)
증거    conversations.info → missing_scope (2026-08-16 실측). auth.test 는 통과
대응    사후 판정만 가능 — slackbot.post 가 channel_not_found · not_in_channel ·
        is_archived 를 "영구 오류"로 분류해 로그에 🔴 로 남긴다
어기면  selfcheck 에 "승인 채널이 살아 있는가" 항목을 넣을 수 없다.
        채널이 아카이브되거나 봇이 쫓겨나면 **첫 발송 실패 때까지 모른다**
```

→ **사람 결정 후보 8번**: 스코프를 늘려 사전 확인을 가능하게 할 것인가(재설치 필요 ·
과거 토큰 회전 이력 있음), 아니면 사후 판정으로 둘 것인가.

---

## [0] 실측 완료 현황

| | 상태 | 근거 |
|---|---|---|
| 노션 | ✅ | 8 DB · 10,102행 · 본문 1,786행 · 오류 0 |
| Apify 액터 | ✅ | 원본 136본 · 8,038 파일 전수 |
| 애플 | ✅ | 스냅샷 3,617본 전수 |
| **구글시트** | ✅ | 5,561행 × 14열 · 접두 14/14 |
| **Gmail** | ✅ | 라벨 315 · 선언 라벨 존재 확인 |
| **Slack** | ⚠️ | auth OK · **채널 조회 불가(스코프)** — 이것 자체가 계약 항목 |
| claude CLI | ✅ | session.py 계약 + `_usage` 120건 실측 |
