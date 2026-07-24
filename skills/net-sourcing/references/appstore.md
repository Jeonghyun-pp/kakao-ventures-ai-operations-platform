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
adapters는 **필터하지 않는다**. AI 여부·카테고리 적합·한국팀·신규성 판단은 전부 core/judge.
여기서는 raw 수집 + signals(rank, release_date, chart_new_entry)만.
