# 템플릿

Notion 「Hello 메일 템플릿」의 미러. 파일은 `project/templates/*.txt`.
`{google_form}`, `{assignee}`, `{note}`는 실행 시 `config.yaml` 값으로 치환된다.

| 파일 | 쓰이는 곳 | 원문 위치 |
|---|---|---|
| `inbound_form_ko.txt` | INBOUND_KO — 국문 콜드 인바운드 | 인바운드 투자 검토 요청 > 구글폼 접수 요청(국문) |
| `webflow_invest_ko.txt` | WEBFLOW 투자 문의 (국문) | 홈페이지 문의 > 투자 문의 > 국문 |
| `webflow_invest_en.txt` | WEBFLOW 투자 문의 (영문) | 홈페이지 문의 > 투자 문의 > 영문 |
| `webflow_mentoring_invest.txt` | WEBFLOW 멘토링 (투자) | 홈페이지 문의 > 멘토링 문의(투자) |
| `webflow_mentoring_job.txt` | WEBFLOW 멘토링 (취업) | 홈페이지 문의 > 멘토링 문의(취업) |
| `webflow_tips.txt` | WEBFLOW TIPS 문의 | 홈페이지 문의 > TIPS 투자 요청 |
| `trusted_direct_ko.txt` | TRUSTED_REF | 바로 받는 경우(국문) — 업데이트 by. 세라 |
| `partnership_decline.txt` | PARTNERSHIP 거절 회신 | 기타 문의 레퍼런스 > 협력 제안 |
| `fwd_intro.txt` | 모든 forward의 인트로 | (실제 포워드 메일 관행) |

## 코드가 만들지 않는 템플릿

아래는 **의도적으로 자동 생성하지 않는다.** 사람이 상황을 보고 Notion에서 가져다 쓴다.

- **채용 관련 전부** (인턴 문의 / 풀타임 채용) — 채용 진행 상태가 시점마다 바뀐다. 룰 #3 참조.
- **미팅 어레인지 / Zoom 링크 / 오피스 안내** — 심사역 일정·연락처가 필요해 코드가 알 수 없다.
- **드랍 메일** — bplan-automation의 `drop` 소관이다. 여기서 만들지 않는다.
- **그물망 콜드메일** — net-sourcing 소관이며 심사역 이름으로 나간다.

## 문구를 고칠 때

1. `project/templates/*.txt`를 고친다.
2. **Notion 원문도 같이 고친다.** 안 그러면 다음 인턴이 노션을 보고 다른 문구를 쓴다.
3. 오피스아워·TIPS처럼 **정책이 바뀌는 문구**는 특히 주의 — 템플릿에 "현재 중단"이라 적힌
   것들이 재개될 수 있다(TIPS는 2025 하반기부터 다시 가능).
