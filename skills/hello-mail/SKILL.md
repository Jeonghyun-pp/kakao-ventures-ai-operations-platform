---
name: hello-mail
description: >-
  카카오벤처스 Hello 계정(deals@example.vc) 메일 트리아지 자동화. 인박스를 스캔해
  ① 광고성 메일 아카이빙 ② Webflow 홈페이지 문의 대응 ③ 협력·섭외·채용·출자 제안 라우팅
  ④ 인바운드 투자 검토 요청 대응을 하나의 파이프라인(스캔→분류→라우팅→액션)으로 처리한다.
  회신·포워드는 **Gmail 초안까지만** 만들고 발송은 사람이 한다.
  Triggers on: "메일관리", "헬로 메일", "hello 메일", "메일 트리아지", "메일함 정리",
  "광고성 메일 아카이빙", "webflow 문의", "인바운드 투자 문의 대응", "/hello-mail".
  Do NOT trigger for: 비플랜 접수건 동기화·드랍메일(→ bplan-automation),
  그물망 소싱(→ net-sourcing).
---

# Hello 메일 트리아지 (hello-mail)

Hello 계정으로 들어오는 메일을 유형별로 분류해 **아카이빙 / 초안 생성 / 에스컬레이션** 셋 중
하나로 착지시킨다. 루틴업무 > 메일관리의 4개 서브태스크가 각각의 스킬이 아니라 **한 파이프라인의
서로 다른 라우트**다: `스캔 → 분류 → 라우팅 → 액션`.

> **상태(2026-07-13)**: triage(읽기 전용) → apply(아카이브 + 초안) 동작. 발송 경로 없음.
> LLM 분류는 `ANTHROPIC_API_KEY`가 있으면 무인, 없으면 Claude Code가 `reclassify`로 주입.
>
> **로드맵(2026-07-17 사용자 결정)**: 무인 하네스로 확장 중 — 초안 무수정 발송률을 측정해
> 카테고리별로 자동 발송을 승격하고, 최종적으로 L2(채용·사칭·미팅)만 사람에게 남긴다.
> 승격 조건·발송 상한·영구 제외 목록은 `config.yaml`의 `autonomy` 블록이 단일 진실이다.

<HARD-GATE>
아래 두 가지는 넘으면 안 되는 선이다. 예외 없음.

1. **비플랜 접수 메일을 건드리지 않는다.** 제목이 `[카카오벤처스]…로부터 접수된 사업계획서`인
   메일은 `protect`로만 처리한다 — 아카이빙·라벨이동·초안 그 무엇도 하지 않는다.
   bplan-automation의 `sync`가 이 메일을 인박스에서 제목으로 찾는데, 못 찾으면 **전체 실행이
   그 자리에서 멈추고 다음 실행에도 계속 막힌다**(main.py의 알려진 한계). 우리가 먼저 치우면
   비플랜 파이프라인이 통째로 죽는다.

2. **메일을 발송하지 않는다.** 회신·포워드는 Gmail 초안까지만. `core/gmail.py`에는 발송
   경로가 아예 없다 — 실수로도 나갈 수 없게 만든 것이다. 자동 발송을 붙이려면 사용자에게
   먼저 확인받고, `config.autonomy.send_enabled`와 함께 코드에 send를 추가해야 한다.
</HARD-GATE>

---

## Non-Negotiable Rules
모든 실행에 적용. 위반 = 잘못된 메일이 회사 이름으로 외부에 나간다.

<CRITICAL>
**#1 — Webflow는 reply가 아니라 forward다**: 발신인이 `no-reply-forms@webflow.com`인 메일은
반드시 **포워드 초안**으로 만들고, 수신자는 **본문에서 파싱한 `이메일:` 주소**로 명시 지정한다.
reply를 쓰면 수신자가 `no-reply@webflow.com`으로 잡혀 **반송된다**(2026-07-10 실제 발생:
550 Address not found — 문의자는 답을 못 받았다).
대신: 주소 파싱에 실패하면 초안을 만들지 말고 `escalate`로 사람에게 넘긴다. 추측 금지.

**#2 — 확신이 없으면 UNKNOWN**: 분류가 애매하면 억지로 카테고리를 고르지 말고 UNKNOWN으로 둔다.
UNKNOWN은 버려지는 게 아니라 `#Squad-비플랜` 슬랙으로 포워드되어 사람이 판단한다(가이드라인의
공식 폴백). 오분류는 조용히 잘못된 회신을 만들지만, UNKNOWN은 아무것도 잘못되게 하지 않는다.
대신: 애매한 것을 살려서 넘기는 비용은 슬랙 메시지 하나뿐이다.

**#3 — 채용 문의에 자동 초안을 만들지 않는다**: RECRUIT는 항상 `escalate`다.
Notion 채용 템플릿은 전부 "현재 채용을 진행하고 있지 않습니다"인데 Gmail 라벨엔
`0. Recruit/2026 H2 Intern`이 **살아 있다 = 지금 채용 중이다**. 템플릿을 자동 생성하면 채용
중인데 "안 뽑는다"는 메일이 나간다. 채용 상태는 시점마다 바뀌고 코드는 그걸 모른다.
대신: 사람이 현재 채용 상태를 확인하고 `templates/`의 템플릿을 골라 쓴다.

**#4 — 아카이빙은 자동, 회신은 초안**: 액션의 위험도를 3등급으로 나눈다.
  - L0 `archive` — 되돌릴 수 있고 외부로 안 나감 → 자동 실행
  - L1 `reply`/`forward` — 회사 이름으로 나감 → **초안만**, 발송은 사람
  - L2 `escalate` — 인박스에 남기고 리포트만 (채용·사칭·판단불가)
대신: 아카이빙을 자동으로 두는 대가로, 회신은 절대 자동화하지 않는다.

**#5 — 같은 메일을 두 번 처리하지 않는다**: 액션 직후 `data/processed.json`에 즉시 기록하고,
매 실행은 기록된 id를 건너뛴다. bplan-automation의 비원자적 쓰기 결함(재실행 시 중복 초안)을
되풀이하지 않기 위해서다.
대신: 재실행이 안전하므로 크론으로 매일 돌려도 된다.

**#6 — 사칭·피싱 문의는 자동 회신 금지**: `person-35f314b98660@example.com` 등 카카오벤처스를 사칭한 채용
면접 초대가 유포 중이고(2026-07 실측, 피해자 3명 문의), 피해자들이 hello@로 진위를 묻는다.
IMPERSONATION은 무조건 `escalate`. 이번 하네스의 대응 범위 밖이며, 자동 회신 대상에서 빼는
것이 목적이다.
대신: 감지되면 리포트에 ⚠️로 올려 사람/PR팀이 처리하게 한다.
</CRITICAL>

→ 카테고리·라우팅 전체: `references/routing.md` · 템플릿: `references/templates.md`

---

## Project 위치

```
~/.claude/skills/hello-mail/project
```

- 러너: `project/.venv/bin/python` · 엔트리포인트: `project/main.py`
- 설정(핸드오프 1파일): `project/config.yaml` — 담당자·슬랙주소·심사역 명단·노이즈 규칙
- 템플릿: `project/templates/*.txt` (Notion "Hello 메일 템플릿" 미러)
- 자격증명(출력·커밋 금지): `token.json`, `gmail_credentials.json` — bplan-automation과 동일한
  Gmail OAuth(`gmail.modify` 스코프). 초안·라벨 모두 가능하고 발송에는 쓰지 않는다.

편의 변수: `PROJ="$HOME/.claude/skills/hello-mail/project"`

## 서브커맨드

| 커맨드 | 하는 일 | 메일함 변경 |
|---|---|---|
| **`triage`** | 인박스 스캔 → 분류 → 계획 리포트 + `data/plan_*.json` 저장 | **없음** (기본) |
| `reclassify <id>=<CAT> …` | UNKNOWN 건에 카테고리 주입 후 재라우팅 | 없음 |
| `apply` | 직전 계획 실행 — 아카이빙 + 초안 생성 | 있음 (발송은 없음) |
| `run` | triage + apply 일괄 | 있음 |

```bash
PROJ="$HOME/.claude/skills/hello-mail/project"
"$PROJ/.venv/bin/python" "$PROJ/main.py" triage      # 읽기 전용 — 항상 여기서 시작
"$PROJ/.venv/bin/python" "$PROJ/main.py" apply       # 확인 후 실행
```

## 실행법 (Claude Code에서)

1. **먼저 `triage`를 돌린다.** 아무것도 바꾸지 않으므로 안전하다.
2. 리포트에 **UNKNOWN이 있고 `ANTHROPIC_API_KEY`가 없으면**, 해당 메일을 직접 읽고
   분류해 `reclassify <msg_id>=<CATEGORY>`로 주입한다. 카테고리 목록은
   `references/routing.md`. **확신이 없으면 UNKNOWN으로 남겨둔다** (룰 #2).
3. 계획을 사용자에게 요약해 보여주고 **`apply` 전에 확인을 받는다.** apply는 사용자의 실제
   메일함을 바꾼다(아카이빙·초안 생성).
4. apply 후 `📮 초안이 생성되었습니다` 안내를 사용자에게 그대로 전달한다 — 발송은 사람 몫이다.

## 무인 실행 (크론)

`ANTHROPIC_API_KEY`가 `.env`에 있으면 LLM이 UNKNOWN을 스스로 분류하므로 `run`이 헤드리스로
돈다. 아카이빙은 자동 처리되고 초안은 쌓이며, 사람은 아침에 Gmail 초안함만 확인하면 된다.
키가 없으면 미분류가 전부 슬랙으로 포워드되므로(안전하지만 시끄럽다) 크론 전에 키를 넣을 것.

## 주의 / 한계

- **분류 정확도가 전부다.** 오분류 → 잘못된 초안. 그래서 `triage`가 기본이고 `apply`는 명시적이다.
  초안 발송 전 수신자·성함·사명을 반드시 확인할 것(발송 후 1분간 undo 가능).
- **영문 인바운드는 아카이빙된다.** auto-reply가 이미 나갔다는 가이드라인 근거지만, 좋은 팀이
  섞여 있을 수 있다. 리포트에 항상 표시되니 훑어볼 것.
- **협력 제안은 거절 초안이 함께 생성된다.** 가이드라인상 슬랙 패스 확인 후 발송하는 흐름이므로,
  **슬랙에서 확인받기 전에 보내지 말 것.**
- **심사역 명단은 수동 관리**: `config.yaml`의 `partners`. 없는 이름이 지명되면 UNKNOWN으로 빠진다.
- **비플랜 스레드의 후속 답장**도 제목이 같으므로 protect된다(인박스에 남음). 의도된 동작이다.
