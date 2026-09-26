---
name: memo
description: 심사역이 슬랙 스레드에 "○○ 투심" + 자료를 올리면 투심보고서 초안 docx 를 같은 스레드에 올리고, 피드백을 받아 다음 버전을 내고, ✅ 시점의 문서를 정답으로 남긴다. 메일·비플랜과 같은 뼈대. 엔진은 kv-automation 의 investment-memo 스킬(무인 모드).
---

# 투심보고서 요청 레인

2026-09-26 에 만들었다. 0단계(Corpus02 무인 실행 9분 · 논거 커버리지 81%)와 1~4단계 검증(노션 본문 읽힘 · 리눅스에서 도구 전부 통과) 위에 있다. 계획: `~/Desktop/evan_bot/MEMO-LANE-BUILD-PLAN-2026-09-26.md`.

## 🔴 밖으로 나가는 것 — 스레드 글·파일뿐, 문은 **닫혀 있다**

```
constraints.yaml  memo.outbound: "off"     off = 기록만(actions:dry) · slack = 스레드에 올린다. on 은 없다(그 이상 나갈 것이 없다)
  ack       접수 답글(노션 찾음/못 찾음 · 자료 체크리스트)     SLACK_KINDS 허용 목록 — 그 밖의 종류는 기록만
  deliverN  초안 docx 올리기(files:write 필요)
  noteN     미확인 목록 + 다음 안내
request_channel: test   요청을 읽는 채널도 처음엔 review_test. 실채널은 사용자가 정한 날 slack.register 한 줄 + 이 값
요청자 = people.yaml 의 slack_id + memo.extra_requesters. 명단 밖은 조용히 무시(기본 거부)
```
노션에는 쓰지 않는다. 모델 호출은 로컬 `claude -p`(`kv_transport.claude.run_skill`). 하루 요약·health 는 기존 승인 종류(review_test).
`--dry-run` 은 이 실행만 문을 내린다. 무엇을 했(을)는지는 `~/kv-data/memo/db/memo.sqlite` 의 `actions`, 판정·입력·산출 md 는 `judgments`.

## 하루

```
10분마다 followup   채널 최상위 글에 "투심" + 명단 안 사람 → 요청(key = 글 ts)
                    → [ack] 노션 스타트업 DB 검색 · 스레드 파일 체크리스트(인띰·IR덱·텀시트·등기부·주주명부·재무제표·FP)
                    → collect_wait_min 뒤: 스레드 파일 다운로드(NFC) + 노션 페이지 본문 md → ~/kv-data/memo/requests/<ts>/in/
                    → run_skill(session_timeout_min) → 새 docx 있으면 성공 → vN.docx 보관 · judgments(v1=live)
                    → [deliverN] docx · [noteN] 미확인 목록 + "고칠 점은 스레드에 · 끝나면 ✅"
                    → 사람 답글(글·파일)이 오면 v(N+1) (max_versions 까지) · ✅ 면 완료
                    → 완료: 스레드에 사람이 올린 docx(없으면 마지막 버전) = 정답 → v1 과 대조(생존율·폐기율·분량비·버려진 문단 표본) → judgments human
12:40 health         오늘 요청이 있었는데 접수 답글이 없으면 review_test 에 한 줄. 요청 없는 날은 조용
저녁(followup 끝에)  ~/kv-data/memo/reports/<날짜>.md — 첫 절 = 심사역이 지운 초안 문단 표본 · review_test 요약(하루 첫 회만)
```
**스케줄은 아직 설치하지 않는다** — `followup` 을 켜면 요청 채널을 읽기 시작하고 문이 slack 이면 답글이 나간다. 설치는 사용자 결정(비플랜과 같다). 설치할 때 frontmatter 에 `followup 10분마다 · health 12:40` 두 행을 넣는다.

## 사람이 하는 것 — 기준 깎기

```
정답      스레드에 ✅. 수정한 docx 를 스레드에 올렸으면 그것이 완성본이다
보고서    첫 절 "심사역이 지운 초안 문단 표본" — 8/6 에 손으로 했던 일(지운 패턴 → 프롬프트)을 매 요청마다 재료로 준다
기준      무인 규칙 skills/memo/headless.md(분량 상한·미확인·M&A 기본값) · 문체·섹션 기준은 kv-automation/skills/investment-memo/prompts/*.md
코퍼스    ~/Downloads/투심보고서/<회사>/ 13쌍(인풋+완성본) — `run --company X --folder …` 로 초안을 내고 완성본과 비교하면 첫날 표본이 된다
```

## 명령

```bash
cd ~/kv
./.venv/bin/python -m skills.memo.run followup [--dry-run]                  # 요청 채널 한 바퀴
./.venv/bin/python -m skills.memo.run run --company Corpus02 --folder ~/Downloads/투심보고서/Corpus02 [--dry-run]   # 슬랙 없이 한 건(--dry-run 은 무인 규칙 본문만)
./.venv/bin/python -m skills.memo.run health | status | report
cd ~/kv-automation/skills/investment-memo && ../../.venv/bin/python scripts/backtest.py compare <초안.docx> <완성본.docx>   # 스킬의 자
```

## 구성

`run.py` 한 파일(흐름은 위 "하루"와 같다 · `grep ^def`) + `headless.md`(무인 규칙 — 실행마다 회사명·폴더·노션 줄·피드백을 채워 `--append-system-prompt-file` 로. 스킬 프롬프트는 손대지 않는다).
통로는 `kv_transport/{notion,slack,claude}.py` 끝의 2026-09-26 절. `constraints.yaml memo.*`: outbound · request_channel · request_pattern · extra_requesters · collect_wait_min · session_timeout_min · max_versions · report_* · backup_keep · skill_dir · skill_python · allowed_tools. **근거는 그 주석에**.

## 알아둘 것

**성공은 docx 존재로 판정한다.** 0단계 실측: 산출물은 9분에 다 나왔는데 마지막 메시지의 API 호출이 망 오류로 죽어 rc=1 이었다. rc 나 마지막 메시지를 믿으면 멀쩡한 초안을 실패로 센다.

**재개를 만들지 않았다.** 정상 세션 9분 · 실패하면 처음부터 다시 도는 게 서버 상태를 md 에서 되살리는 것보다 싸다. 요청이 30분을 넘기 시작하면 그때.

**인띰만으로는 못 쓴다.** Example Company D 최종본 221문단 중 인띰 유래는 9%, 노션 미팅록 24%, IR 덱 12%. 그래서 ack 가 체크리스트를 되묻고, 없는 자료는 기다리지 않고 `(미확인)` 으로 낸다 — Example Company D 도 그렇게 제출했고 자료는 이틀 뒤에 왔다.

**분량이 문제다.** 무인 초안은 완성본의 1.8배가 나왔고, Example Company D 에서 심사역은 403문단 중 100개를 지웠다(라벨·해설·인물평·회사 시뮬레이션·출처 없는 단정). headless.md 4번이 상한을 말하지만, 진짜 교정은 보고서 첫 절(지운 문단 표본)을 보고 프롬프트를 깎는 것이다.

**파일명은 NFC.** 맥은 NFD 로 저장한다. `완성본` 폴더가 복사 제외에서 빠져 IRR 엑셀이 답안지처럼 들어간 사고(09-26). 다운로드할 때 NFC 로 통일한다.

**권한 둘이 아직 없다.** 슬랙 봇에 `files:read`·`files:write` 가 없다(다운로드는 로그인 HTML 이 200 으로 온다 — 크기가 아니라 종류로 판정한다). 서버에서 돌리려면 `claude setup-token`. 둘 다 사람 몫.
