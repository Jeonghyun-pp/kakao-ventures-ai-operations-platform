# 검증된 소스 함정 목록 (2026-07-16~17 실측)

어댑터 수리·소스 추가 시에만 로드. 전체 소스 인벤토리는 `$DATA/kol-source-list.md`,
레지스트리는 `$DATA/sources.yaml`이 진실이다 — 여기는 "왜 그 URL이어야 하는가"의 기록.

## RSS 함정
| 소스 | 함정 | 검증된 정답 |
|---|---|---|
| appfigures | /rss, /feed 등 흔한 경로 전부 404 | `appfigures.com/resources/rss/insights` |
| sarahtavel | apex 도메인 curl 연결 자체가 실패 | `www.sarahtavel.com/feed` (www 필수) |
| neuron | theneurondaily.com/feed 404 | `rss.beehiiv.com/feeds/N4eCstxvgX.xml` |
| hnrss | 간헐 502/429 (캐시 ~55분) | 재시도 또는 다음 주 — 파손 장기화 시 Algolia HN API(`hn.algolia.com/api/v1/search_by_date?tags=show_hn`)로 대체 |
| stratechery | 유료 Daily Update는 피드에서 ~130자 발췌 | 무료 주간 전문 + 유료 제목을 테마 시그널로. 구독 결정 시 개인화 피드로 교체 |
| lenny | /t/ai 전용 피드 404, 항목에 category 태그 없음 | 전체 피드 + Pass 0 AI 키워드 필터 |
| kv-posts | 전체 이력 피드 — 첫 몇 회 15건씩 드레인 후 잠잠해짐 | 정상 동작이니 침묵 오판 금지 |

## Scrape 함정
| 소스 | 함정 | 정답 |
|---|---|---|
| a16z | WP인데 피드 의도적 비활성, wp-json도 404 | href 정규식 + `a16z.com/post-sitemap.xml` 이중경로. diff 10건+ 어긋나면 패턴 파손 |
| a16z | 카테고리 페이지가 허브 페이지(/global 등)도 링크함 | Pass 1이 claims:[]로 거름. 오탐 잦으면 패턴에 slug 패턴 강화 |
| forerunner | 링크가 상대경로 `../perspectives/{slug}.html` | link_prefix로 절대화 |
| bitkraft/sensortower | RSS 없음, JS 사이트 | sitemap lastmod가 RSS급 — `bitkraft.vc/sitemap.xml`, `sensortower.com/en-US-blog-sitemap-1.xml` |

## YouTube 함정
| 항목 | 함정 | 정답 |
|---|---|---|
| 20VC 채널 ID | 페이지 grep 시 연관 채널(Business of Sport)이 먼저 잡힘 | `UCf0PBRjhf0rF8fWBIxTuoWA` (canonical 확정값) |
| 자막 직접 curl | timedtext가 POT 토큰 요구로 빈 응답 | yt-dlp 필수 (`--write-auto-subs --skip-download`) |
| Lightcone | Apple/Spotify 피드 2025-12에서 멈춤 | 유튜브 재생목록 `EXAMPLE_ID_7465dd147389`이 유일 최신 |
| VTT 원본 | 롤링 중복으로 실발화의 3~4배 | clean_vtt.py 필수 (실측 421KB→45KB, 10%) |
| Garry Tan 채널 | 쇼츠가 원본 논평 | shorts_policy: full (다른 채널은 titles) |

## Apify (✅ VERIFIED — 2026-07-17 스모크 실측, 총 ~$0.08)
- X: `apidojo~tweet-scraper` — `twitterHandles` 배열 작동, 응답 필드 author.userName/text/createdAt/url 확정.
  **함정 1**: payload `start` 날짜를 액터가 무시 (요청 07-09, 응답에 07-03 포함) → 어댑터의
  createdAt 클라이언트 컷 제거 금지. 포맷 `"Fri Jul 03 13:53:34 +0000 2026"`.
  **함정 2**: 런 옵션(maxItems 등)은 쿼리 파라미터 — payload에 넣으면 조용히 무시 (net-sourcing 사고).
- LinkedIn: `harvestapi~linkedin-profile-posts` — 개인 프로필 3곳 각 10건 정상.
  **함정 1**: URL 필드는 `linkedinUrl`(`url` 아님), 날짜는 `postedAt.date`(dict 안).
  **함정 2**: 응답 = 활동 피드라 **리포스트 섞임** (실측 30건 중 12건) — author.publicIdentifier가
  슬러그와 다르면 리포스트. actual_author/is_repost 메타 필수 (org 에코 방어).
  **함정 3**: company URL은 이 액터 미지원 (southparkcommons 0건 실측) — SPC는 블로그 RSS로 커버, 어댑터가 스킵.
  **함정 4**: maxPosts=N은 날짜 무관 최신 N건 — 8일 컷은 postedAt.timestamp로 클라이언트에서.
- 직접 수집은 HTTP 999로 불가 확정. 계정 공유: net-sourcing과 같은 APIFY_TOKEN ($29/월 Starter).
  preflight가 90% 경고 시 실행 금지. 원본은 `project/data/raw/_actor/`에 자동 보관 —
  스키마 재교정·무과금 재생(replay)이 가능하니 삭제 금지.

## Gmail
- hello-mail의 `token.json` 재사용 (경로는 config.yaml). **읽기 전용** — 라벨/발송 mutation 금지
  (hello-mail 영역 침범). a16z Consumer Substack은 'Coming Soon' 상태 — 구독돼 있으면 런칭 시 자동 유입.

## 인물 변동 (표기 갱신용)
- Example Author A: 2026-04-17 a16z 퇴사 → 독립 펀드 설립 중 (org: ex-a16z). 신규 펀드 채널 미공개
- Tom Blomfield: 2026-07-13 YC 휴직 → Anthropic (sources.yaml에서 보류 중)
- Sequoia: 2025-11 스튜어드 교체 Botha → Alfred Lin + Pat Grady
- Josh Elman: 2026-06-23 a16z consumer 합류 (@joshelman)
