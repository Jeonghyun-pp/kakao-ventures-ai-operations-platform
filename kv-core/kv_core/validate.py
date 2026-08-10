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
    min_records: int = 0    # 통과 레코드 최소 기대치 — 0건 성공(진공 통과)을 침묵 실패와 구분한다(규칙05)

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
    # 프로즈 속에서 완전한 JSON 값들을 스캔한다. 예전의 "첫 괄호~마지막 괄호" 슬라이스는
    # 프로즈 괄호가 섞이면 틀린 부분구조를 **조용히** 반환했다(리뷰 W5-med, N건이 1건으로 축소).
    # 후보가 정확히 1개면 그것, 2개 이상이면 모호 — fail-closed 로 파싱 실패 처리한다.
    dec = json.JSONDecoder()
    candidates: list = []
    i = 0
    while i < len(t):
        if t[i] in "[{":
            try:
                obj, end = dec.raw_decode(t, i)
                candidates.append(obj)
                i = end
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(f"JSON 후보가 {len(candidates)}개 — 모호해서 채택하지 않는다(fail-closed)")
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
        elif want in ("int", "float") and isinstance(val, bool):
            # bool 은 int 의 서브클래스라 isinstance 를 통과한다 — 계약상 True 는 숫자가 아니다.
            errs.append(f"{field} 타입 bool ≠ {want}")
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
        _dlq_append(dlq_path, [{"record": r, "errors": e} for r, e in bad])

    if len(ok) < contract.min_records:            # 진공 통과 방지(규칙05): 0건 성공 ≠ 성공
        msg = f"통과 레코드 {len(ok)}건 < 계약 최소 {contract.min_records}건"
        if contract.on_invalid == "halt":
            raise ValidationHalt(msg)
        print(f"⚠️  validate: {msg} — quarantine 계약이라 진행하나 산출이 기대에 못 미친다.")
        if dlq_path is not None:
            _dlq_append(dlq_path, [{"batch_error": msg}])
    return ok, bad


# DLQ 회전 임계 — 넘으면 `.1` 로 밀고 새로 시작한다(보관 1세대). [W9 C-M6]
_DLQ_MAX_BYTES = 5 * 1024 * 1024
_DLQ_MAX_RECORD = 20_000        # 한 항목이 이 이상이면 원문을 잘라 담는다


def _dlq_append(dlq_path: Path, entries: list[dict]) -> None:
    """DLQ 는 **append(jsonl)** — 덮어쓰면 지난 회차 격리분이 유실된다(리뷰 W5-med).

    [W9 C-M6] 다만 회전이 없으면 무한히 자란다. `validate_text` 는 파싱 실패 시 **claude
    원문 전체**를 덤프하므로 하루치가 100KB 를 넘고, meeting-mail 이 halt→quarantine 으로
    바뀌면서 깨진 출력이 파이프라인을 세우지 않고 계속 쌓이게 됐다(성장률 증가).
    그리고 관측자가 매 브리핑마다 이 파일을 통째로 읽는다 — 커질수록 관측이 느려진다.

    두 가지로 막는다: ①항목 하나가 지나치게 크면 원문을 잘라 담고 ②파일이 임계를 넘으면
    한 세대만 남기고 회전한다. **격리 기록 자체를 지우지는 않는다**(조용한 삭제 금지).
    """
    from datetime import datetime, timezone
    dlq_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dlq_path.exists() and dlq_path.stat().st_size >= _DLQ_MAX_BYTES:
            rotated = dlq_path.with_suffix(dlq_path.suffix + ".1")
            rotated.unlink(missing_ok=True)
            dlq_path.rename(rotated)
            print(f"⚠️  DLQ 회전: {dlq_path.name} → {rotated.name} (임계 {_DLQ_MAX_BYTES//1024//1024}MB)")
    except OSError as e:                              # 회전 실패가 격리 기록을 막으면 안 된다
        print(f"⚠️  DLQ 회전 실패(기록은 계속): {e!r}")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(dlq_path, "a", encoding="utf-8") as f:
        for e in entries:
            line = json.dumps({"ts": ts, **e}, ensure_ascii=False)
            if len(line) > _DLQ_MAX_RECORD:
                # 원문을 통째로 담아 파일을 폭발시키지 않는다 — 진단에 필요한 앞부분만.
                e = dict(e)
                if isinstance(e.get("record"), dict) and isinstance(e["record"].get("raw"), str):
                    e["record"] = dict(e["record"], raw=e["record"]["raw"][:4000] + "…(잘림)")
                elif isinstance(e.get("raw"), str):
                    e["raw"] = e["raw"][:4000] + "…(잘림)"
                line = json.dumps({"ts": ts, "truncated": True, **e}, ensure_ascii=False)[:_DLQ_MAX_RECORD]
            f.write(line + "\n")


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
            _dlq_append(dlq_path, [{"parse_error": str(e), "raw": text}])
        return [], [({"_raw": text}, [str(e)])]
    if isinstance(data, list):
        records = data
    elif data.get("results") is not None:   # `or` 금지 — 빈 [] (정당한 0건)가 falsy 로 둔갑한다(리뷰 W5-med)
        records = data["results"]
    elif data.get("rows") is not None:
        records = data["rows"]
    else:
        records = [data]
    return validate_records(records, contract, dlq_path=dlq_path)


# ─────────────────────────── 선언적 계약 로딩 ───────────────────────────

def load_contracts(path: str | Path) -> dict[str, Contract]:
    """config/contracts.yaml → {stage: Contract}. 정본은 파일 한 곳(하드코딩 0)."""
    import yaml
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, Contract] = {}
    for stage, spec in raw.items():
        out[stage] = Contract(schema=spec.get("schema") or {},
                              on_invalid=spec.get("on_invalid", "halt"),
                              min_records=int(spec.get("min_records", 0)))
    return out
