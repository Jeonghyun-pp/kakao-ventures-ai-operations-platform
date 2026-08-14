"""접수메일 담당자 라벨 — **두 잡이 보는 한 곳.**

## 왜 별도 모듈인가

`bplan_sync_job` 은 이 라벨을 **붙이고**, `bplan_drop_job` 은 이 라벨 **안에서** 원본
메일을 찾는다:

    sync  →  messages().modify(addLabelIds=[<라벨>], removeLabelIds=["INBOX"])
    drop  →  q = f'label:"<라벨>" subject:"[카카오벤처스] {회사} 로부터 접수된…"'

즉 **한 잡의 산출물이 다른 잡의 입력**이다. 두 곳에 문자열을 따로 적어두면 어느 한쪽만
고쳐도 `sync 는 A 에 넣고 drop 은 B 에서 찾는` 상태가 되고, 그때 drop 은 예외를 던지지
않는다 — **조용히 0건**을 찾고 "원본 메일을 못 찾음"이라고 보고한다. 실패가 정상처럼 보인다.

🔴 2026-08-14 에 이 결합의 다른 형태로 실제 사고가 났다. 재빌드가 sync 의 라벨 단계를
   통째로 뺐고(라벨을 안 붙임), drop 은 그대로 라벨로 찾았다. 42건이 라벨 없이 인박스에
   남았고 그 42건은 드랍 초안이 영영 안 만들어지는 상태였다. 그때 drop 의 `label` 인자는
   **출처가 아예 없었다** — `main()` 이 컷오버 가드에서 먼저 죽어서 아무도 못 봤다.

## 진실원은 `config.yaml` 의 `bplan.gmail_label` 하나다

환경변수로 덮을 수 있게 두는 이유: 담당자가 바뀌면(소피아→에반) 라벨이 바뀌는데,
그때 코드 배포 없이 돌려볼 수 있어야 한다. 다만 **기본값은 항상 config** 다.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parents[1]
ENV_VAR = "BPLAN_GMAIL_LABEL"


def bplan_label() -> str:
    """접수메일 담당자 라벨. **없으면 죽는다 — 빈 문자열을 돌려주지 않는다.**

    빈 문자열을 돌려주면 drop 의 질의가 `label:"" subject:…` 가 되어 라벨 조건이
    사실상 사라지고, `pick_thread` 가 "후보 다건"으로 전부 거절한다. 원인이 라벨
    설정이라는 걸 로그에서 알아낼 방법이 없다.
    """
    if v := os.environ.get(ENV_VAR):
        return v
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    label = ((cfg or {}).get("bplan") or {}).get("gmail_label")
    if not label:
        raise RuntimeError(
            f"config.yaml 에 bplan.gmail_label 이 없다 ({PROJ / 'config.yaml'}). "
            f"sync 는 이 라벨을 붙이고 drop 은 이 라벨에서 원본을 찾는다 — 없으면 둘 다 무의미하다. "
            f"임시로 덮으려면 환경변수 {ENV_VAR}.")
    return str(label)
