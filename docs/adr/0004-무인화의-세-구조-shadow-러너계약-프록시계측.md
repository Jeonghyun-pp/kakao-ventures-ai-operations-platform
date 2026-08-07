# ADR 0004 — 무인화의 세 구조: shadow 병행 · 러너 성공 계약 · 프록시 계측

> 상태: 확정 (W6 빌드에 반영, 중간점검에서 사후 문서화 — 플랜 파일은 세션 소멸성이라 여기로 승격)
> 날짜: 2026-08-05 · 결정자: 박정현
> 배경: W6 무인 워커화 중 내린 구조 결정 3종이 코드 주석·커밋 메시지에만 살았다.
> 중간점검이 "의도 문서 없는 주차에 결함이 밀집한다"를 재실증(W3 11건·W4 14건) — 같은 갭을 만들지 않는다.

---

## 결정 ① — 컷오버는 shadow 병행으로 산다

**registry 에 `shadow: bool` 을 선언한다.** 외부 러너(runner: github-actions)가 primary 인 task 를
로컬 스케줄러가 **검증용으로 병행 실행**한다. 컷오버 = `runner: local` + shadow 제거 — 한 줄.

- **왜 runner 를 거짓말시키지 않나**: runner 는 "실행 주체가 누구인가"의 선언이다. 검증하려고
  `local` 로 바꾸면 GH 가 여전히 primary 라는 사실이 카탈로그에서 사라진다.
- **전제 = 저장소 분리**: shadow 는 신·구가 **같은 저장소를 쓰지 않을 때만** 안전하다
  (appstore: GH=data 브랜치 vs 로컬=~/kv-data). 같은 저장소면 불변규칙 01(신·구 병행 금지) 위반.
- **프로토콜**: 3일 연속 로컬 성공(유효 파일 ≥80/84) → 컷오버 결정(사람 승인) → 구 러너 은퇴.
  "폴백은 대체가 증명되기 전까지 못 지운다"(W2 레슨)의 구현.
- **관측 주의(중간점검 T7)**: shadow 의 워터마크는 **로컬 검증 신호**지 task 건강이 아니다 —
  표면은 `[shadow-local]` 라벨과 "primary 관측 없음"을 반드시 표기한다.

## 결정 ② — 러너 성공 계약: True 는 "완주"만 뜻한다

**러너 = `(task, period) -> bool`, True = 그 주기의 일이 실제로 완주·검증됐다.**

- run_due 는 True 일 때만 last_success 를 전진시킨다. queued(락 경합)·부분 실패·불확실을
  True 로 돌려주면 **워터마크가 실행 없이 전진해 결손이 조용히 굳는다**(at-least-once 붕괴).
  의심스러우면 False — 다음 wake 가 재시도한다(재시도는 싸고, 유실은 비싸다).
- **완주의 판정은 반환코드가 아니라 산출물이다**(규칙 05). board.py 는 0/84 저장에도 exit 0 —
  러너가 산출물을 직접 센다. 그리고 **세는 것은 존재가 아니라 온전함**(중간점검 T4:
  0바이트 84개가 통과했다 — 크기>0 + 파싱 가능 + 비어있지 않음).
- 미배선 task 는 run_due **앞에서** 걸러 loud 스킵한다 — 스킵이 러너 반환값으로 표현되면
  last_attempt 가 오염된다(시도하지 않은 것을 시도로 기록).

## 결정 ③ — 계측 불가 자원은 프록시로 재되, 한계를 계약에 적는다

**claude 구독 쿼터는 사용량 API 가 없다 → 호출 수/일(UTC)을 대리 지표로 계측한다.**

- **chokepoint metering**: 단일 통로(session.py)가 유일한 계측 지점 — spawn 전 어드미션
  (`gate.check_calls`), spawn 시도 후 기록(`record_call`, timeout 포함·미spawn 제외).
- **게이트의 목적은 정밀 회계가 아니라 방화벽이다**: 재시도 루프 폭주 차단 + 사람 몫 잠식
  방지(W2 레슨 #1). 그래서 감수하는 것들을 계약에 명시한다 — 호출당 크기 상이(±오차),
  check→record TOCTOU(±동시성 콜), UTC 자정 경계(±1). 임계는 선언(resources.yaml)이며
  `0 = 전면 동결`이다(falsy 로 "미설정" 취급 금지 — 중간점검 B3).
- 분산 불변식(`max_batches_per_run < daily_calls_warn`)은 **기계 검증**한다 — 두 파일에
  걸친 약속은 사람 기억으로 못 지킨다.

---

## 이 결정들이 틀렸다면

- **shadow 가 3일 넘게 계속 필요하다** → 컷오버 기준이 틀렸거나 로컬 러너가 신뢰 불가 —
  shadow 는 전이 상태지 상주 상태가 아니다.
- **False 반환이 잦아 재시도가 낭비다** → 완주 판정 임계가 과도 — 임계 재조정(80/84)이지
  계약 완화가 아니다.
- **프록시(호출 수)와 실쿼터 체감이 크게 어긋난다** → 세션 시간 합 등 더 나은 대리 지표로
  교체 — 계측 지점(chokepoint)은 그대로다.

## 관련
- ADR 0002(신뢰 경계 배치·컷오버 규율) · ADR 0003(claude 단일화·쿼터 축)
- 구현: `kv_core/{registry,scheduler,workers,gate,session}.py` · `config/{tasks,resources}.yaml`
- 검증: `tests/{_synthetic_dispatch,_synthetic_claude_gate,_synthetic_orrery}.py`
- 중간점검: `docs/reviews/W6-W7-midpoint-review.md` (T4·T7·B3 가 이 문서의 각주다)
