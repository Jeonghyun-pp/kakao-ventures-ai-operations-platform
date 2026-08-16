# kv

> **인턴 업무를 어떤 실패도 조용히 지나가지 못하는 최소 구조로 자동화한다.**

만드는 것은 자동화 스크립트 묶음이 아니다. **팀에 인턴이 한 명 더 있는데 그 인턴이
소프트웨어인 것**이다. 무엇을 만들지 헷갈릴 때는 두 번 묻는다 —
*사람 인턴이라면 이걸 할까?* 그리고 *이게 조용히 실패할 수 있나?*

```
docs/INTENT.md      의도와 검사 기준. 단계마다 여기 와서 대조한다
constraints.yaml    [0] 외부 제약의 기계 판본. 코드에 숫자를 박지 않는다
check_intent.py     의도를 기계로 센다. 미적용을 통과로 세지 않는다
tools/              일회성 도구 — 스킬 안에 두지 않는다
skills/appstore/    첫 스킬
```

## 세 루트

```
~/kv/          코드
~/kv-data/     데이터   리포 안에 심볼릭 링크를 만들지 않는다
~/kv-config/   시크릿
```

## 검사

```bash
./.venv/bin/python check_intent.py          # 의도 대조
./.venv/bin/python tools/mutation_test.py   # 검사가 실제로 무는지
```
