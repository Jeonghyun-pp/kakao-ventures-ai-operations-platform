# cross_db_overlap.md

채널 DB 3벌 ↔ base DB 인물 중복 (LinkedIn URL 정규화 · 핸들 기준)

- `base_주간소싱`: LinkedIn키 344 · 핸들 894 · 제목 1379
- `ch_앱스토어`: LinkedIn키 0 · 핸들 0 · 제목 2664
- `ch_스레드`: LinkedIn키 0 · 핸들 635 · 제목 635
- `ch_링크드인`: LinkedIn키 1934 · 핸들 1141 · 제목 1141
- `dedup_극초기`: LinkedIn키 0 · 핸들 0 · 제목 1277
- `dedup_bplan3`: LinkedIn키 0 · 핸들 0 · 제목 539
- `dedup_bplan2`: LinkedIn키 0 · 핸들 0 · 제목 0
- `dedup_스타트업본DB`: LinkedIn키 0 · 핸들 0 · 제목 2275

| A | B | LinkedIn키 교집합 | 핸들 교집합 | 제목 교집합 |
|---|---|---:|---:|---:|
| base_주간소싱 | ch_앱스토어 | 0 | 0 | 485 |
| base_주간소싱 | ch_스레드 | 0 | 634 | 634 |
| base_주간소싱 | ch_링크드인 | 260 | 259 | 259 |
| base_주간소싱 | dedup_극초기 | 0 | 0 | 8 |
| base_주간소싱 | dedup_bplan3 | 0 | 0 | 0 |
| base_주간소싱 | dedup_bplan2 | 0 | 0 | 0 |
| base_주간소싱 | dedup_스타트업본DB | 0 | 0 | 0 |
| ch_앱스토어 | ch_스레드 | 0 | 0 | 0 |
| ch_앱스토어 | ch_링크드인 | 0 | 0 | 0 |
| ch_앱스토어 | dedup_극초기 | 0 | 0 | 6 |
| ch_앱스토어 | dedup_bplan3 | 0 | 0 | 0 |
| ch_앱스토어 | dedup_bplan2 | 0 | 0 | 0 |
| ch_앱스토어 | dedup_스타트업본DB | 0 | 0 | 0 |
| ch_스레드 | ch_링크드인 | 0 | 0 | 0 |
| ch_스레드 | dedup_극초기 | 0 | 0 | 1 |
| ch_스레드 | dedup_bplan3 | 0 | 0 | 0 |
| ch_스레드 | dedup_bplan2 | 0 | 0 | 0 |
| ch_스레드 | dedup_스타트업본DB | 0 | 0 | 0 |
| ch_링크드인 | dedup_극초기 | 0 | 0 | 18 |
| ch_링크드인 | dedup_bplan3 | 0 | 0 | 0 |
| ch_링크드인 | dedup_bplan2 | 0 | 0 | 0 |
| ch_링크드인 | dedup_스타트업본DB | 0 | 0 | 0 |
| dedup_극초기 | dedup_bplan3 | 0 | 0 | 0 |
| dedup_극초기 | dedup_bplan2 | 0 | 0 | 0 |
| dedup_극초기 | dedup_스타트업본DB | 0 | 0 | 55 |
| dedup_bplan3 | dedup_bplan2 | 0 | 0 | 0 |
| dedup_bplan3 | dedup_스타트업본DB | 0 | 0 | 1 |
| dedup_bplan2 | dedup_스타트업본DB | 0 | 0 | 0 |