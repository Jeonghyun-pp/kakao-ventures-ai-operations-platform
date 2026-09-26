# App Store 어댑터 세부

## 데이터 소스 (검증됨)
- **랭킹 SSR** (무료·무인증, **카테고리당 200위**): `tools/rank_lookup.fetch_chart(genre, chart)`
  - `https://apps.apple.com/kr/iphone/charts/{genre}?chart={top-free|top-paid}` 의
    `serialized-server-data` 파싱. rank 1~25는 인라인 메타 보유, 26~200은 app_id만.
  - **200위 = 애플 무료 천장**(그 이상은 유료). SSR 실패 시 **RSS-100 자동 폴백**.
  - 과거 "200위는 diandian 유료" 서술은 폐기 — SSR로 무료 200위 확보(appstore-rank 스킬 60회 실측).
- **상세 보강**: `tools/rank_lookup.enrich(apps)` = `lookup?id=...&country=kr` 50개 배치.
  → **최초 출시일(releaseDate)**·개발사·설명·평점·리뷰수·업데이트일·링크. tail(26~200)은
    메타가 없어 enrich 필수 → 어댑터가 `config.channels.appstore.enrich=true`로 수집 시 호출.
    (releaseDate가 초기성 판단[A2]의 기준일이라 소싱에선 enrich 켜두는 게 기본.)

## 장르 ID [B1]
| id | 카테고리 |
|---|---|
| 6016 | 엔터테인먼트 |
| 6017 | 교육 |
| 6008 | 사진 및 비디오 |
| 6007 | 생산성 |
(쇼핑·금융 제외 — judge L0에서도 재확인)

## 신규 차트진입 필터 [B3]
매 수집마다 `data/snapshots/appstore_{genre}_{date}.json`에 app_id 리스트 저장.
`new_entry_lookback_days`(기본 14) 이전 스냅샷과 비교해 **"2주 전 차트에 없다가 새로 진입"**
한 앱에 `signals.chart_new_entry = true`. (판단 아님 — 신호만. 컷은 judge가.)

## 어댑터 범위
adapters는 **품질을 필터하지 않는다**. AI 여부·카테고리 적합·신규성 판단은 전부 core/judge.
여기서는 raw 수집 + signals(rank, release_date, chart_new_entry)만.
예외는 아래 국적 컷 하나다(SKILL.md 어댑터 HARD-GATE 4조건 통과분).

## 국적 컷 — `kr_only` [2026-08-19 신설]

**왜**: KR 스토어프론트라 외국 앱도 설명이 한국어로 번역돼 들어온다. 2026-W34 착지분
**204건 중 131건(64%)이 외국 개발사**였고, 사람이 매주 그걸 손으로 걸러왔다.

**무엇을 하나 (오해 금지)**: `core/kr_origin.classify()` 는 **3값**을 낸다.

| verdict | 뜻 | 착지 |
|---|---|---|
| `kr` | 한국이라는 **증거**가 있다 (.kr 도메인 · 한글 사명 · `(KR)` 표기) | ✅ |
| `unknown` | **판정 못 했다.** 한국이라는 뜻이 **아니다** | ✅ 통과 |
| `foreign` | 외국이라는 **증거**가 있다 | ❌ 버림 |

🔴 이건 *"한국을 고르는"* 필터가 **아니다**. **"확실한 외국을 빼는"** 필터다.
비용이 비대칭이라서다 — 한국 앱을 잘못 버리면 **영구 유실**(아무도 그게 없어진 걸 모른다),
외국이 남으면 **사람이 거른다**(지금도 하는 일). 그래서 애매하면 통과시킨다.

**판정 계단** (위에서부터, 먼저 걸리는 것이 이김):

| # | 신호 | → |
|---|---|---|
| L1 | 호스팅 플랫폼 도메인(github.io·vercel.app·supabase.co…)은 **국적 정보 0** → 도메인 판정 건너뜀 | — |
| L2 | `seller_url` ccTLD: `.kr` / 국가귀속 ccTLD | kr / foreign |
| L3 | `seller_name` 에 `주식회사`·`(주)`·`(KR)`·한글 | kr |
| L4 | `seller_name` 문자체계: 가나 · 한자 · 키릴 | foreign |
| L5 | `seller_name` 외국 법인격(`Anonim Sirketi`·`Pte Ltd`·`GmbH`·`K.K.`·`有限公司`…) / 중국 지역명 | foreign |
| L6 | 앱 텍스트가 **한글 0**인 채로 가나·키릴·타이·아랍·한자 | foreign |
| L7 | 설명 **한글비율 < 5%** (설명 40자 이상일 때만) | foreign |
| — | 그 외 | unknown |

**결정적 재료 = `sellerName`**. 옛 `kr_signal` 이 실패한 건 `artistName`(표시명)을 봤기 때문이다:

```
artistName  "Quvarte"                            ← 국적 판별 불가
sellerName  "Quvarte Teknoloji Anonim Sirketi"   ← 튀르키예. 한 방에 끝
```

이 필드는 `rank_lookup.enrich` 가 **받아놓고 `setdefault` 때문에 버리고 있었다**(2026-08-19 수리).

**세 개의 안전장치** (전부 실측 사고에서 나왔다):

1. **성씨 veto** — L7이 외국이라 판정해도 `seller_name` 에 한국식 성씨 로마자가 있으면 `unknown` 으로 되돌린다.
   실측: `HOJUNG KIM`(FlyReady) · `Minsu Kim`(Snick) — 설명이 영어라 버려질 뻔했다.
   🔴 성씨 목록은 **veto 전용**이다. 이걸로 한국이라고 **단정하지 마라** — Lee/Yang/Wu/Do 는
   중국·베트남과 충돌하고 그게 옛 kr_signal 이 죽은 지점이다. veto면 충돌 비용이 "외국 몇 개 통과"뿐이다.
2. **설명 40자 미만이면 L7 미적용** — lookup 장애로 설명이 비면 한글비율 0 →
   **그날 수집이 통째로 증발**한다. (어댑터의 '출시일 미상 컷'과 같은 원칙, 방향만 반대.)
3. **과다 삭제 HEALTH** — 외국 판정률 > 90% 면 경고(평시 ~63%). `enrich: false` 와 같이 켜도 경고.

**실측 / 재측정**:
```
python tools/kr_origin_eval.py          # fixtures/kr_origin_w34.json (199건 고정 라벨)
  → 외국 126건 중 105건 제거(83%) · 한국 73건 유실 0건 · PASS
```
🔴 `core/kr_origin.py` 의 규칙(TLD 목록·법인격·성씨·임계값)을 고쳤으면 **반드시 이걸 돌려라.**
통과 기준은 **유실 0건** 하나다. 라벨을 늘리려면 픽스처에 같은 스키마로 행을 추가한다.

**남는 노이즈**: 이 컷은 외국의 83%만 뺀다. 나머지 ~17%는 seller 신호가 없고 설명이 한국어로
번역된 앱들이다(예: `Told One, LLC`). 그건 사람이 본다 — `Match Reason` 끝의
`국적 unknown(...)` 표시가 "확인된 한국"과 "판정 못 함"을 갈라 준다.

**롤백**: `config.yaml` → `channels.appstore.kr_only: false`. 코드 변경 불필요.
