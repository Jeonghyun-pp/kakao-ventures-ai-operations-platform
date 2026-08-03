"""산출물 검증 — Write-Audit-Publish 의 **Audit**. claude 의 soft 출력을 계약으로 검사한다. [W5]

## WAP 패턴
판단 스테이지(Write: claude 가 판정을 냄) 다음에 이 검증 스테이지(Audit)를 두고, **통과한 것만**
다음 스테이지(Publish: 노션 기입·Gmail 초안)로 넘긴다. 쓰레기가 하류로 전파되는 걸 경계에서 막는다.

## exit0 ≠ 성공
claude 가 "판단을 끝냈다"는 것과 그 판단이 계약을 지켰다는 것은 다르다. 세션 종료코드가 아니라
**산출물**을 되읽어 검사한다.

## soft 출력 (claude 는 강제가 아니라 권고)
스키마를 프롬프트에 줘도 프로즈가 섞이거나 ```json 펜스로 감싸일 수 있다. `parse_json` 이 그걸
관대하게 벗겨낸다. 그래도 안 되면 파싱 실패 자체가 계약 위반이다.

## 선언적 계약 (하드코딩 0 — 결정 ③)  → config/contracts.yaml
  schema     : {field: {type, required, enum, nonempty}}
  on_invalid : halt | quarantine   ← 부작용 가역성으로 고른다(결정 ②)
    - halt      : 하나라도 어긋나면 **전체 중단**(ValidationHalt). 발송 등 비가역 스테이지.
    - quarantine: 어긋난 것만 DLQ 로 격리하고 나머지 진행. 판정 기입 등 가역 스테이지.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool,
          "list": list, "dict": dict}


class ValidationHalt(Exception):
    """halt 계약에서 위반이 나왔다 — 아무것도 발행하지 않고 파이프라인을 세운다."""


@dataclass
class Contract:
    schema: dict            # {field: {type, required, enum, nonempty}}
    on_invalid: str = "halt"   # halt | quarantine

    def __post_init__(self):
        if self.on_invalid not in ("halt", "quarantine"):
            raise ValueError(f"on_invalid 는 halt|quarantine: {self.on_invalid!r}")


# ─────────────────────────── soft 출력 파싱 ───────────────────────────

def parse_json(text: str):
    """claude 의 soft 텍스트에서 JSON 을 관대하게 꺼낸다. 실패는 ValueError."""
    t = (text or "").strip()
    if t.startswith("```"):                       # ```json ... ``` 펜스 벗기기
        inner = t.split("```")
        t = inner[1] if len(inner) >= 2 else t
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):   # 앞뒤 프로즈 제거: 첫 괄호~마지막 괄호
        i, j = t.find(open_c), t.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON 파싱 실패 — soft 출력이 계약을 어겼다")


# ─────────────────────────── 레코드 검증 ───────────────────────────

def check_record(rec: dict, schema: dict) -> list[str]:
    """한 레코드의 계약 위반 목록. 빈 리스트 = 통과."""
    errs: list[str] = []
    if not isinstance(rec, dict):
        return [f"레코드가 dict 가 아님: {type(rec).__name__}"]
    for field, spec in schema.items():
        present = field in rec and rec[field] is not None
        if not present:
            if spec.get("required"):
                errs.append(f"필수 필드 없음: {field}")
            continue
        val = rec[field]
        want = spec.get("type")
        if want and want in _TYPES and not isinstance(val, _TYPES[want]):
            errs.append(f"{field} 타입 {type(val).__name__} ≠ {want}")
        enum = spec.get("enum")
        if enum and val not in enum:
            errs.append(f"{field}={val!r} 는 허용값 아님 {enum}")
        if spec.get("nonempty") and isinstance(val, str) and not val.strip():
            errs.append(f"{field} 가 비어있음")
    return errs


def validate_records(
    records: list, contract: Contract, *, dlq_path: Path | None = None,
) -> tuple[list, list[tuple]]:
    """records 를 계약으로 나눈다 → (통과, [(레코드, [위반]) …]).

    halt      : 위반이 하나라도 있으면 ValidationHalt(아무것도 발행 안 함).
    quarantine: 위반분을 DLQ 파일로 격리(조용한 삭제 없음)하고 통과분만 반환.
    """
    ok: list = []
    bad: list[tuple] = []
    for rec in records:
        errs = check_record(rec, contract.schema)
        (ok if not errs else bad).append(rec if not errs else (rec, errs))

    if bad and contract.on_invalid == "halt":
        sample = bad[0][1]
        raise ValidationHalt(
            f"halt 계약 위반 {len(bad)}건 — 아무것도 발행하지 않음. 예: {sample}"
        )
    if bad and dlq_path is not None:              # quarantine: 격리 기록
        dlq_path.parent.mkdir(parents=True, exist_ok=True)
        dlq_path.write_text(
            json.dumps([{"record": r, "errors": e} for r, e in bad],
                       ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, bad


def validate_text(
    text: str, contract: Contract, *, dlq_path: Path | None = None,
) -> tuple[list, list[tuple]]:
    """claude stdout 원문 → 파싱 → 검증. 파싱 실패는 계약 성격대로 처리.

    halt: 파싱 실패 = ValidationHalt. quarantine: 전량을 DLQ 로 보내고 ([], all).
    """
    try:
        data = parse_json(text)
    except ValueError as e:
        if contract.on_invalid == "halt":
            raise ValidationHalt(f"파싱 실패(halt): {e}") from e
        if dlq_path is not None:
            dlq_path.parent.mkdir(parents=True, exist_ok=True)
            dlq_path.write_text(json.dumps({"parse_error": str(e), "raw": text},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
        return [], [({"_raw": text}, [str(e)])]
    records = data if isinstance(data, list) else data.get("results") or data.get("rows") or [data]
    return validate_records(records, contract, dlq_path=dlq_path)


# ─────────────────────────── 선언적 계약 로딩 ───────────────────────────

def load_contracts(path: str | Path) -> dict[str, Contract]:
    """config/contracts.yaml → {stage: Contract}. 정본은 파일 한 곳(하드코딩 0)."""
    import yaml
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, Contract] = {}
    for stage, spec in raw.items():
        out[stage] = Contract(schema=spec.get("schema") or {},
                              on_invalid=spec.get("on_invalid", "halt"))
    return out
