# 메일 재구축 0단계 — 안전 기준선 (2026-09-21)

재구축을 시작하기 전에, **지금 돌고 있는 시스템이 망가지지 않게** 깔아둔 것들.
계획 전문은 리포 밖 `~/Desktop/evan_bot/MAIL-FINAL-BUILD-PLAN-2026-09-21.md`.

## 1. 기준선

| 항목 | 값 |
|---|---|
| 되돌아갈 지점 | 커밋 `e6db3f1` · 태그 `baseline-2026-09-21` (코드 수정 없는 스냅샷) |
| 정적 검사 | `check_intent.py` 통과 11 · 실패 0 · 미적용 3 (A10~A12) |
| 고정 테스트 | `tests/` 53개 통과 — `./.venv/bin/python -m unittest discover -s tests -t .` |
| 코드 백업 | `~/kv-data-backup/_code/kv-<날짜>.bundle` (매일, 7세대). 복원: `git clone <번들> ~/kv` |

파일 하나만 되돌리기: `git checkout baseline-2026-09-21 -- skills/mail/rules.py`

## 2. 고정 테스트가 재는 것

**지금 코드가 하는 일**을 잰다. 옳은지가 아니다. 망을 부르지 않고 메일함을 건드리지 않는다.

| 파일 | 내용 |
|---|---|
| `test_mail_rules.py` | 규칙 7종과 그 **순서** · 심사역 지명 · 웹플로우 파서 (합성 입력) |
| `test_mail_route.py` | 카테고리→행동 전 갈래 · 지명 덮어쓰기 · 수신자 중복 처리 · 초안 작업 목록 (합성 입력) |
| `test_mail_recorded.py` | 2026-09-03~09-21 계획 파일에서 얼린 **실제 판정 666건**을 지금 코드에 재생 |

- 실제 기록은 제목·주소가 들어 있어 **리포 밖**에 둔다: `~/kv-data/mail/fixtures/` (권한 600). 없으면 그 테스트는 건너뛰고 그 사실을 말한다.
- 결함으로 알려진 동작도 그대로 고정했다(`_known_defect` · `_known_policy_gap` · `_known_tradeoff`). 고치면 그 테스트가 깨진다 — 그때 기대값을 **의도적으로** 바꾼다.
- 테스트가 실제로 무는지 확인했다(메모리 안에서만 방어를 되돌림): 헤더 규칙 제거 · 출자 갈래 행동 변경 · 웹플로우 파서 수정 → 전부 실패로 잡힘.
- 기록 재생의 한계: 계획 파일에 본문·수신자 수·헤더가 없다. 규칙 경로의 입력은 **기록된 근거에서 되짚은 최소 신호**다. "그 신호가 있으면 그 판정이 나온다"까지만 말한다.

### 고정해 둔 알려진 결함
| 테스트 | 내용 | 실측 |
|---|---|---|
| `test_content_is_first_line_only_known_defect` | 웹플로우 `문의 내용` 의 첫 줄만 잡힌다 | 3건 — 실제 886~3,904자 중 6~37자 (운영 리더로 확인) |
| `test_bplan_reply_is_also_protected_known_defect` | 접수 알림에 대한 **답장**도 접수 메일로 보호된다 | 9/11~14. 비플랜 쪽 정규식은 앵커가 있어 거른다 |
| `test_impersonation_substring_…_known_defect` | '피싱' 부분일치로 보안 솔루션 영업도 사칭 문의가 된다 | 코드 확인(실례 미확인) |
| `test_real_name_is_not_in_roster_known_defect` | 실명·직함 지목을 전부 놓친다 | 전 기간 발동 0회 |
| `test_ad_header_alone_decides_known_policy_gap` | 헤더 하나로 LLM 없이 광고 확정 | 협력 제안 1건 아카이브(9/9) |
| `test_ad_by_sender_token_noreply_known_policy_gap` | `noreply` 토큰이 시스템 알림을 광고로 잡는다 | 파일 열람 권한 요청 알림(9/17) |

## 3. 공유 지점 — 메일 작업 중 기본 동작을 바꾸면 안 되는 파일

| 파일 | 같이 쓰는 곳 |
|---|---|
| `kv_transport/claude.py` | 메일 · 앱스토어 · 링크드인 (7개 모듈) |
| `kv_transport/gmail.py` | 메일 · 비플랜 |
| `kv_transport/slack.py` | 메일 · 앱스토어 · 링크드인 |
| `kv_transport/notion.py` | 비플랜 · 앱스토어 · 링크드인 · tools 8개 |
| `constraints.yaml` | 17개 파일 |
| `kvlib/*` | 전 스킬 |

메일에만 필요한 동작은 **호출부에서 인자로** 넘긴다. 기본값은 그대로 둔다.
메일함에 쓰는 코드는 6곳뿐이다: 메일 스킬의 아카이브 2·초안 3, 비플랜의 라벨 이동 1. 그 외에 **수동 세션**이 장부·초안을 직접 만든 기록이 있다(9/14 교정 8건, 9/18 드랍 초안 25건).

## 4. 재활용 판정

**그대로 쓴다**
`kvlib/ledger.py`(매 줄 fsync · 찢긴 끝줄 복구) · `kvlib/lock.py`(flock, 절대 unlink 안 함) · `Fetched`(미상≠0건) · `kvlib/secrets.py`·`paths.py`(시크릿·데이터는 리포 밖) · `constraints.yaml` 방식 · `check_intent.py` · `schedule.py`(맥 전용 코드 격리)
`gmail.py` 의 인증·재시도·페이지네이션·`archive`/`relabel` 의 쓴 뒤 되읽기·`thread_after`·초안 생성 — 3주간 실행 실패 0건
`route.py` 의 `_tidy`·`body_chunks`·`_post_text`·`mail_link` · `brief.py`(실제 채널 말투에서 뽑은 요약 프롬프트) · 템플릿 7종 · `run.apply()` 의 실행 직전 재확인(실제로 20건을 걸렀다)

**고쳐서 쓴다**
`rules.classify()` — 구조적으로 필요한 규칙만 확정, 헤더·토큰은 신호로. 접수 제목 정규식에 앵커
`rules.parse_webflow()` — 필드 경계 추출
`route.decide()` — 행동 표를 정책 파일로. 주석의 `[사용자 결정]` 은 결정 기록으로 옮긴다
`rules.find_named_partner()` — 호칭 매칭 로직은 유지, 명부를 데이터로(실명·직함·Slack ID)
`slack.py` — `post`·`reactions`·`replies`·`notify_owner` 가 이미 있다
`claude.py` — 호출 격리·일일 상한·빈 출력=인증 만료 판정은 유지. 출력 검증은 호출부에서
`criteria.md` — 경계 사례 서술은 유지, 행동 문구만 분리

**대체한다**
하루 1회 계획 파일 방식 · 14일·100통 수집 · 15통 TSV 배치와 값 기반 파서 · 메일id+종류 멱등 키 · 메일 주소로 보내는 Slack 전달 · 초안에서 끝나는 구조

## 5. 0단계에서 새로 알게 된 것 — 계획에 반영할 것

1. **`schedule.py` 는 상주 프로세스를 선언할 수 없다.** 형식이 `매일 HH:MM` · 요일 · `N분마다` 뿐이다. 상주 프로세스 + Slack Socket Mode 로 가려면 `schedule.py`·A9 를 확장해야 한다.
   대안: **`N분마다` 짧은 주기 작업 + Slack 폴링.** `slack.reactions()`·`replies()` 가 이미 폴링 방식으로 동작하고, 판단 건은 주 2~3건이다. 몇 분의 지연을 받아들이면 상주 프로세스·Socket Mode·새 이벤트 권한이 전부 필요 없어지고, 선언·A9·A13·이관 구조가 그대로 유지된다. → 10월의 LangGraph 판정과 함께 정한다.
2. **A8(발송 경로 0건)은 자동 발송과 정면으로 부딪힌다.** 끄지 않고 재설계한다 → `docs/PROPOSAL-A8-send-gate.md`.
3. **테스트 러너가 생겼다.** A11(테스트가 망을 안 부른다)·A12(테스트가 프로덕션을 안 건드린다)를 미적용에서 실제 검사로 바꿀 수 있다.
4. **오프사이트 사본이 없다.** 번들도 스냅샷도 같은 디스크다. 원격 저장소를 붙이려면 먼저 Slack 메일 연동 주소·채널 ID 를 코드에서 설정으로 빼야 한다(`route.py`·`run.py`·`slack.py`·`SKILL.md`·`linkedin/run.py`).
5. **pytest 가 없다**(Python 3.14 venv). 지금 테스트는 표준 `unittest` 다. 평가 도구를 만들 때 의존성 추가 여부를 정한다.

## 6. 현행 시스템을 조정할 때의 절차

```
① 고정 테스트 통과 (의도한 변경이면 기대값을 같은 커밋에서 바꾼다)
② check_intent 통과
③ ./.venv/bin/python -m skills.mail.run apply --dry-run 으로 전·후 비교
④ 커밋 (변경 하나에 커밋 하나)
⑤ 다음 날 아침 com.kv.mail.plan.log · apply.log 확인
```
