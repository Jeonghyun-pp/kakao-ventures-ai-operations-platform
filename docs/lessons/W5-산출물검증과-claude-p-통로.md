# W5에서 배운 것 — claude -p 단일 통로 · 산출물 검증(WAP)

> 2026-08-03 · 데이터 엔지니어링 · 구조 설계 관점
> 결정의 *이유*는 `docs/adr/0003`, 로드맵은 `docs/roadmap-w5-w12-revised.md`.
> 이 문서는 **다음에 같은 모양을 만났을 때 알아보기 위한 것**이다.

---

## 0. W5를 한 장으로

```
[전]  AI 호출이 두 갈래 — API 분류기($, 상한없음) + claude 세션(구독, 주1회 수동)
      그리고 "claude 가 판단했다 = 성공" 으로 믿음 (exit0 신뢰)

[후]  코드 ──┐
             ├─→ session.py ─→ claude -p (구독, API키 없음)   ← 통로 하나
      나머지┘        │
                     ↓  soft 출력(강제 아님)
             validate.py (Audit) ─→ 통과분만 발행    ← 되읽기 검증
```

일반 이름: **Anti-Corruption Layer** · **Write-Audit-Publish** · **least privilege**

---

## 1. AI 호출을 한 통로로 격리한다 (Anti-Corruption Layer)

두 메커니즘(API + 세션)이 섞여 있으면 무인화할 때 "돈 나가는 경로"가 파이프라인에 남는다.
`session.py` 하나로 모으고 API 키 경로를 없앴다. 나머지 코드는 claude 를 **어떻게** 부르는지
(플래그·모델·권한) 모른다 — 부르는 방식이 바뀌어도 고칠 곳은 여기 한 곳.

> **일반 이름 — Anti-Corruption Layer / Adapter.** 외부의 변덕스러운 의존을 한 지점에 가두면
> 그 변덕이 시스템 전체로 번지지 않는다.

## 2. 구독은 공짜가 아니라 축이 바뀐 것이다

```
API 키   많이 쓰면 → 돈이 나간다 (상한 없음)
구독     많이 쓰면 → 쿼터가 소진된다 → 사람이 못 쓴다
```

무인화의 위험이 사라진 게 아니라 **성질이 바뀌었다.** 그래서 구독을 **공유 자원**으로 보고
`tasks.yaml` 에 `claude` 를 등록해 세션을 직렬화한다(admission control). 게이트도 `$` 대신
쿼터를 보게 재타깃한다(W6). — W2 레슨 #1·#3의 재현.

## 3. claude 출력은 강제(hard)가 아니라 권고(soft)다

API 의 `json_schema` 는 스키마를 **어길 수 없게** 강제했다. `claude -p` 는 스키마를 프롬프트에
줘도 "잘 따름"이지 강제가 아니다 — 프로즈가 섞이거나 ```json 펜스로 감싸일 수 있다.

**그래서 되읽기 검증이 옵션이 아니라 생명줄이 된다.** `exit 0 ≠ 성공` 을 W2(앱스토어)에서
데이터 유무로 배웠다면, W5 에선 **형식·계약 준수**로 다시 만난다.

> **일반 이름 — Write-Audit-Publish (WAP).** 판단(Write)→검증(Audit)→발행(Publish).
> 통과한 것만 하류로 보내 쓰레기의 전파를 경계에서 막는다. dbt tests · Great Expectations 와 같은 발상.

## 4. 검증 실패 정책은 부작용의 **가역성**으로 갈린다

같은 검증이라도 실패 시 대응이 달라야 한다. 그리고 그 기준은 task 이름이 아니라 **그 단계가
되돌릴 수 있는 부작용이냐**다 — net-sourcing 안에도 판정(가역)과 발송(비가역)이 함께 있다.

```
가역(판정 기입)   quarantine  어긋난 것만 DLQ 로 격리, 나머지 진행   recall 우선, 다음 회차가 주움
비가역(메일 발송) halt        하나라도 어긋나면 전체 중단, 발행 0    의심스러우면 멈춘다
```

> **일반 이름 — validation severity (warn vs error) · dead-letter queue · fail-closed.**
> 그리고 이 정책을 **코드에 박지 않고 `config/contracts.yaml` 에 선언**한다(data contract).
> 문서에 적는 건 선언이고, 파일에 없는 건 구조다.

## 5. 결정론은 코드, 확률은 claude

"언제 뭘 돌리나"(due·catch-up·락·멱등)는 날짜 빼기다 — 정답이 있고, 틀리면 비가역 사고(메일
2회 발송)고, 공짜고, 테스트된다. 이런 **결정론적 통제**에 "가끔 헷갈리는 확률적 모델"을 두면
신뢰가 가장 필요한 곳에 비결정성을 심는 꼴이다.

```
control plane (코드)   스케줄·체크포인트·락·멱등     ← 결정론. 안 틀림
compute (claude)       판단·생성                    ← 확률. 정답 없는 것만
```

session.py 는 **최소권한**(기본 도구 0개, 발송·셸·네트워크 구조적 거부)으로 이 경계를 강제한다.

## 6. 증명이 없으면 안 끝난 것이다

W5 주제 자체가 "exit0≠성공"이라, 짜기만 하고 안 돌려보면 자기모순이다. 합성 데이터로
end-to-end 를 돌려 4가지를 눈으로 봤다(`tests/_synthetic_slice.py`, 무발송·소량 쿼터):

| # | 증명 | 결과 |
|---|---|---|
| ① | claude 판단+초안이 session.py 통로로 실제로 나온다 | completed, 초안 2건 |
| ② | 재실행 멱등 — 완료 스테이지 스킵, **claude 재호출 0**, 초안 중복 0 | PASS |
| ③ | 발송 앞 계약 위반(결함 주입) → **halt, 발행 0** | failed, BAD 초안 없음 |
| ④ | 구조적 무발송 — `_assert_no_send` 살아있음, 초안=로컬 파일 | PASS |

---

## 가져갈 것 — 6줄

```
1. AI 호출은 한 통로로 격리한다                         (anti-corruption layer)
2. 구독은 공짜가 아니라 축이 쿼터로 바뀐 것이다          (shared resource → admission control)
3. claude 출력은 강제가 아니라 권고 — 되읽어 검증한다     (soft output → WAP)
4. 검증 실패 정책은 부작용의 가역성으로 갈린다            (quarantine vs halt, 선언적 계약)
5. 결정론은 코드, 확률은 claude                          (control plane vs compute)
6. 증명 없으면 안 끝난 것이다                            (exit0 ≠ 성공)
```

## 다음에 물을 것

```
이 AI 호출은 어느 통로로 나가나?           (여러 곳이면 격리 안 된 것)
이 출력을 그대로 믿나, 되읽나?             (soft 면 반드시 되읽기)
이 단계 실패는 되돌릴 수 있나?             (halt 냐 quarantine 이냐를 가른다)
이 판단은 정답이 있나?                     (있으면 코드, 없으면 claude)
짜기만 했나, 돌려서 봤나?                  (exit0 ≠ 성공)
```
