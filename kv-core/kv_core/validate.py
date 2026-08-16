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
from dataclasses import dataclass, field
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
    # 🔴 [2026-08-16] **전송 형식.** `json` | `tsv`. 자세한 근거는 `parse_tsv` 독스트링.
    #    한 줄 요약: JSON 은 `"` 가 내용이면서 구분자라 **내용 한 글자가 구조를 깬다**.
    #    계약 파일이 형식을 선언하고 파서가 그걸 따른다 — 코드에 박으면 두 진실원이 된다.
    wire: str = "json"
    # tsv 일 때 열 순서. **자유 텍스트 필드를 반드시 맨 뒤**에 둔다(`parse_tsv` 참조).
    columns: list = field(default_factory=list)

    def __post_init__(self):
        if self.on_invalid not in ("halt", "quarantine"):
            raise ValueError(f"on_invalid 는 halt|quarantine: {self.on_invalid!r}")
        if self.wire not in ("json", "tsv"):
            raise ValueError(f"wire 는 json|tsv: {self.wire!r}")
        if self.wire == "tsv":
            if not self.columns:
                raise ValueError("wire: tsv 인데 columns 가 없다 — 열 순서 없이는 파싱이 불가능하다")
            unknown = [c for c in self.columns if c not in self.schema]
            if unknown:
                raise ValueError(
                    f"columns 에 스키마에 없는 열 {unknown} — 오타면 그 열이 **통째로 버려지고** "
                    f"필수 필드 누락으로 전건이 위반이 된다. 스키마 필드: {sorted(self.schema)}")
            missing = [f for f, s in self.schema.items()
                       if s.get("required") and f not in self.columns]
            if missing:
                raise ValueError(
                    f"필수 필드 {missing} 가 columns 에 없다 — tsv 는 열 순서가 유일한 단서라 "
                    f"선언에서 빠진 필드는 **영원히 채워지지 않는다**.")


# ─────────────────────────── soft 출력 파싱 ───────────────────────────

def wire_instruction(contract: Contract) -> str:
    """계약 → 모델에게 줄 **출력 형식 지시문**. 형식의 진실원을 하나로 묶는다.

    🔴 프롬프트에 형식을 손으로 적으면 **계약과 프롬프트가 두 진실원**이 된다.
       계약에서 열을 하나 빼도 프롬프트는 옛 형식을 계속 요구하고, 그 어긋남은
       "레코드는 오는데 필드가 비어 있다"로 나타나 원인이 안 보인다.
       이 리포가 반복해 당한 형태라(리뷰 F4 '천장의 진실이 두 곳') 여기서 막는다.
    """
    if contract.wire != "tsv":
        return ("JSON 배열만 출력하라. 각 원소는 "
                + "{" + ", ".join(f'"{f}": "..."' for f in contract.schema) + "} 형식. "
                + "프로즈·코드펜스·설명 금지.")
    cols, last = contract.columns, contract.columns[-1]
    return (
        f"출력은 **탭 구분 텍스트**다. 한 줄에 한 건, 열 순서는 정확히 "
        f"{' <TAB> '.join(cols)} 이다.\n"
        f"· 열 구분은 **탭 문자 하나**({len(cols)}열이므로 탭 {len(cols)-1}개). 쉼표·파이프 금지.\n"
        f"· 마지막 열 `{last}` 는 **줄 끝까지**가 내용이다 — 그 안에서는 따옴표·괄호·탭을 "
        f"자유롭게 써도 되고 **이스케이프하지 마라**.\n"
        f"· 🔴 `{last}` 안에 **줄바꿈을 넣지 마라.** 줄바꿈이 레코드 구분자다.\n"
        f"· 헤더 줄·번호·불릿·코드펜스·설명 금지. 데이터 줄만 출력하라.")


def parse_tsv(text: str, columns: list[str]) -> tuple[list[dict], list[str]]:
    """탭 구분 · **자유 텍스트 필드는 맨 뒤**. → (레코드, 못 읽은 줄)

    ## 왜 JSON 을 버렸나 (2026-08-16)

    실측: `judge_dlq.jsonl` 2건이 **같은 결함**으로 죽어 있었다. claude 가 `reason` 안에
    이스케이프하지 않은 `"` 를 넣었다 — 둘 다 정확히 **column 386**:

        … 이데일리 인터뷰("방산 '무엇을 만드냐'에서 '얼마나 빨리 만드냐'로") …
        … 티로 사례("아이템은 매력적이지 않지만 짱짱한 팀") …

    그 `"` 가 문자열을 조기 종료시켜 **배열 전체**가 깨졌고, 같은 배치의 나머지 15건이
    함께 버려졌다. 배치당 16건 · 2회 발생 = **32건 유실**, 배치 기준 약 2.3%
    (08-11~08-16 판정 적용 1,410건 ≈ 88배치 중 2개).

    ### 문제의 형태

        JSON 에서 `"` 는 **내용이면서 동시에 구분자**다
            → 내용에 들어간 문자 하나가 구조를 파괴한다
            → 파괴 범위가 배치 전체다

    파서를 관대하게 만드는 것(깨진 배열에서 읽히는 것만 줍기)은 **파괴가 일어난 뒤**를
    다루는 대증요법이고, 어느 조각을 주울지 추측이 섞인다. 여기서는 **파괴가 구조에
    닿지 못하게** 한다 — 형식을 바꾼다.

    ### 이 형식이 그 부류를 없애는 방식

        <page_id>\\t<verdict>\\t<one_liner>\\t<reason 는 줄 끝까지>
        파싱:  line.split("\\t", len(columns) - 1)

    · `"` `'` `(` `)` `{` `}` `\\` 는 **그냥 글자**다. 이스케이프할 것이 없다.
    · 자유 텍스트가 맨 뒤 + `maxsplit` 이라, **reason 안의 탭까지 흡수**된다.
    · 남는 위험은 reason 안의 줄바꿈 하나뿐이고, 그건 **그 레코드만** 깬다.
    · 한 줄이 깨져도 다른 줄은 **구조적으로** 산다 — 구제 휴리스틱이 아니라 보장이다.
      (`events.jsonl` 의 torn-tail 규율과 같은 성질 · 불변식 I2)

    ### 이게 가능한 이유

    계약 5개가 **전부 평평한 스칼라**다 — `page_id`·`verdict`·`reason`·`one_liner`·
    `score`·`fwd`. 중첩이 하나도 없다. **애초에 JSON 이 필요한 구조가 아니었다.**
    ⚠️ 예외는 `meeting-mail` 의 `body` 다 — 메일 본문이라 줄바꿈이 필수여서 줄 단위
       형식에 못 담는다. 그 계약만 `wire: json` 을 유지한다.

    ### 이래도 안 되는 것 — 정직하게

    형식은 **구조적 실패**만 없앤다. claude 가 필드를 빠뜨리거나, 없는 `page_id` 를
    지어내거나, 허용값 밖 verdict 를 내는 **의미적 실패**는 그대로 남는다.
    그건 `check_record` 의 몫이고 이미 작동한다(위 DLQ 구제분 15/15 계약 통과).
    완전한 보장은 API 의 tool-use 강제(constrained decoding)뿐인데, 이 시스템은 과금 0
    을 위해 **구독제 claude CLI** 를 쓴다 — 그 보장이 없다. 비용과 맞바꾼 것이다.

    ## 반환

    `(레코드, 못 읽은 줄)`. **못 읽은 줄을 버리지 않고 돌려준다** — 호출부가 몇 건이
    유실됐는지 시끄럽게 알리고 DLQ 에 남길 수 있어야 한다. 조용한 유실이 이 결함의 본체였다.
    """
    n = len(columns)
    recs: list[dict] = []
    broken: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        # 코드펜스·프로즈 한두 줄이 섞여도 그 줄만 버린다(전체를 죽이지 않는다).
        if line.lstrip().startswith("```"):
            continue
        parts = line.split("\t", n - 1)
        if len(parts) != n:
            broken.append(line)
            continue
        rec = {c: p.strip() for c, p in zip(columns, parts)}
        # 마지막(자유 텍스트) 열만 양끝 공백을 보존한다 — 본문 서식이 판정 근거일 수 있다.
        rec[columns[-1]] = parts[-1].rstrip()
        recs.append(rec)
    return recs, broken


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
        val = rec.get(field)
        present = field in rec and val is not None
        # 🔴 [2026-08-16] 필수 아닌 필드의 **빈 문자열은 "없음"과 같이 본다.**
        #    LLM 은 "값 없음"을 키 생략이 아니라 `""` 로 낸다. 그 둘을 다르게 대우하면
        #    선언이 아니라 출력 습관이 판정을 가른다. 실제로 DLQ 에 두 건이 그렇게 죽어 있었다:
        #      · bplan  `{score:"칼드랍", reason:""}` → "reason 가 비어있음" → **판정 유실**
        #      · 그물망 `{to:"", subject:…, body:…}` → "to 가 비어있음"   → **초안 유실**
        #    후자가 특히 나쁘다 — W9 C-H3 가 *"`to` 는 코드가 RECIPIENT 로 덮어쓰는 값"* 이라며
        #    `required: true → false` 로 고쳤는데 `nonempty: true` 를 남겨, 어차피 버릴 값
        #    하나 때문에 완성된 본문이 그대로 격리됐다. **고침이 절반만 적용돼 있었다.**
        #    공백만 있는 문자열(`"  "`)도 같이 묶는다 — `""` 는 통과하고 `" "` 는 죽는
        #    비일관을 만들지 않기 위해서다.
        if present and not spec.get("required") and isinstance(val, str) and not val.strip():
            present = False
        if not present:
            if spec.get("required"):
                errs.append(f"필수 필드 없음: {field}")
            continue
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

    🔴 [2026-08-16] `wire: tsv` 계약은 **줄 단위**로 읽는다. 한 줄이 깨져도 나머지 줄은
       그대로 살아 검증으로 넘어간다 — 파싱 실패가 배치 전체를 죽이던 구조를 없앴다
       (근거는 `parse_tsv` 독스트링).
    """
    if contract.wire == "tsv":
        records, broken = parse_tsv(text, contract.columns)
        if broken:
            # 🔴 조용히 넘어가지 않는다. 유실이 안 보이면 "잘 돌고 있다"로 읽힌다 —
            #    그게 이 결함이 넉 달 숨어 있던 방식이다.
            print(f"  ⚠️  형식이 깨진 줄 {len(broken)}건 — 이 줄만 버린다"
                  f"(나머지 {len(records)}건은 정상 진행). 해당 행은 다음 회차 큐로 돌아간다.")
            for b in broken[:3]:
                print(f"      · {b[:120]}")
            if dlq_path is not None:
                _dlq_append(dlq_path, [{"parse_error": "tsv 열 수 불일치", "raw": b}
                                       for b in broken])
        if not records:
            # 한 건도 못 읽었다 = 형식 자체가 어긋났다(모델이 옛 JSON 을 냈을 수 있다).
            # 그 사실을 **파싱 실패로 취급**해 계약 성격대로 처리한다.
            msg = (f"tsv 로 읽은 레코드 0건(깨진 줄 {len(broken)}) — 출력이 선언한 형식이 "
                   f"아니다. columns={contract.columns}")
            if contract.on_invalid == "halt":
                raise ValidationHalt(f"파싱 실패(halt): {msg}")
            if dlq_path is not None and not broken:
                _dlq_append(dlq_path, [{"parse_error": msg, "raw": text}])
            return [], [({"_raw": text}, [msg])]
        return validate_records(records, contract, dlq_path=dlq_path)

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

# 계약 스펙에 쓸 수 있는 키 — **미지 키를 거부**하기 위한 어휘표다.
# ⚠️ 여기 있는 것만 실제로 존중된다. `check_record` 가 읽는 키와 반드시 같아야 한다
#    (다르면 "선언했는데 안 도는 계약"이 생기고, 그게 이 결함의 형태다).
_SPEC_KEYS = {"schema", "on_invalid", "min_records", "wire", "columns"}
_FIELD_KEYS = {"type", "required", "enum", "nonempty"}


def load_contracts(path: str | Path) -> dict[str, Contract]:
    """config/contracts.yaml → {stage: Contract}. 정본은 파일 한 곳(하드코딩 0).

    ## 🔴 [2026-08-13 W12 · 리뷰 E-F5] 오타를 조용히 삼키고 있었다

    `spec.get("schema") or {}` 는 `shema:` 라고 한 글자 틀리면 **빈 스키마로 로드된다.**
    경고도 예외도 없다. 그 계약이 `on_invalid: halt` 를 선언하고 있어도 스키마가 비면
    **모든 쓰레기가 halt 없이 통과해 노션에 기입된다** — 실측: 쓰레기 3건 → 통과 3·위반 0.

    ADR 0003 결정 ④ 는 *"claude 출력은 권고(soft) → 되읽기 검증 필수. validating 스테이지를
    옵션에서 **생명줄**로 승격"* 이라 정했다. 그 생명줄이 한 글자로 꺼졌다.
    `min_records` 오타는 진공 통과 방지(규칙 05)를 동시에 끈다 — 0건 성공이 성공이 된다.
    `type: integer`(정답은 `int`)처럼 모르는 타입 이름도 검사를 조용히 건너뛰었다.

    같은 리포의 다른 로더들은 정반대다 — `registry.load` 는 미지 필드에 raise 하고,
    `cadence.py:7` 은 *"조용한 폴백을 만들지 마라 — 이 파일의 존재 이유다"* 라고 적었다.
    **그 원칙이 옆 모듈로 전개되지 않았다.** 여기서 전개한다.
    (`on_invalid` 값 검증만 이미 fail-loud 였다 — 나머지를 거기 수준으로 올린다.)
    """
    import yaml
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, Contract] = {}
    for stage, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"계약 '{stage}': 스펙이 매핑이 아니다({type(spec).__name__})")
        unknown = sorted(set(spec) - _SPEC_KEYS)
        if unknown:
            raise ValueError(
                f"계약 '{stage}': 알 수 없는 키 {unknown} — 오타면 그 설정이 **조용히 무시된다**. "
                f"쓸 수 있는 키: {sorted(_SPEC_KEYS)}")
        schema = spec.get("schema") or {}
        if not schema:
            raise ValueError(
                f"계약 '{stage}': schema 가 비었다 — 빈 스키마는 **모든 레코드를 통과시킨다**. "
                f"검증할 게 없으면 계약을 지워라(있는데 안 도는 것이 가장 나쁘다).")
        for fname, fspec in schema.items():
            if not isinstance(fspec, dict):
                raise ValueError(f"계약 '{stage}.{fname}': 필드 스펙이 매핑이 아니다")
            fbad = sorted(set(fspec) - _FIELD_KEYS)
            if fbad:
                raise ValueError(f"계약 '{stage}.{fname}': 알 수 없는 키 {fbad} — "
                                 f"쓸 수 있는 키: {sorted(_FIELD_KEYS)}")
            want = fspec.get("type")
            if want is not None and want not in _TYPES:
                raise ValueError(
                    f"계약 '{stage}.{fname}': 알 수 없는 type '{want}' — 모르는 타입은 "
                    f"**검사를 건너뛴다**(선언했는데 안 도는 계약이 된다). "
                    f"쓸 수 있는 값: {sorted(_TYPES)}")
        out[stage] = Contract(schema=schema,
                              on_invalid=spec.get("on_invalid", "halt"),
                              min_records=int(spec.get("min_records", 0)),
                              wire=str(spec.get("wire", "json")),
                              columns=list(spec.get("columns") or []))
    return out


# ─────────────────────────── 판단 룰 게이트 ───────────────────────────

# 🔴 [2026-08-13 W12 · 리뷰 C-F-B10] "가동 스위치"가 **토큰만** 봤다.
#    `if any(ln.strip() == _EMPTY_TOKEN ...)` 하나뿐이라 실측에서 이것들이 전부 통과했다:
#        0바이트 · 공백만 · 제목 한 줄만 남은 파일
#    나쁜 merge·잘못된 `>` 리다이렉트·편집 중 저장 사고로 룰 파일이 비면 게이트가 통과하고,
#    프롬프트엔 시스템 문구와 데이터만 남는다. 모델은 그래도 라벨 안에서 뭔가를 고르고,
#    계약도 통과하고, 노션에 기입된다 — **룰 0줄로 판정이 나간다.**
#
#    임계는 추측하지 않고 실측했다(2026-08-13):
#        bplan-criteria.md   8,208자 · 본문 167줄
#        judge-criteria.md  11,148자 · 본문 206줄
#    30줄이면 작은 쪽 대비 5배 여유이고, 위 세 파손 형태는 전부 걸린다.
#    ⚠️ 룰 ID(`CR-1` 등) 존재로 잡고 싶었지만 judge 쪽은 ID 체계가 없다(0종) —
#       한쪽에만 있는 규약을 공통 게이트로 쓰면 그 파일이 영원히 막힌다.
_CRITERIA_MIN_LINES = 30


def criteria_body_lines(text: str) -> int:
    """주석·제목·빈 줄을 뺀 실제 룰 분량."""
    return sum(1 for ln in (text or "").splitlines()
               if ln.strip() and not ln.lstrip().startswith("#"))


def criteria_missing(text: str, empty_token: str) -> str:
    """룰이 없거나 부실한 이유. 빈 문자열이면 가동 가능.

    두 조건 **모두** 본다 — 토큰(사람이 명시적으로 끈 상태)과 분량(사고로 비워진 상태).
    """
    if any(ln.strip() == empty_token for ln in (text or "").splitlines()):
        return f"단독 줄 토큰 {empty_token} 이 있다 — 지우면 가동된다"
    n = criteria_body_lines(text)
    if n < _CRITERIA_MIN_LINES:
        return (f"룰 본문이 {n}줄뿐이다(최소 {_CRITERIA_MIN_LINES}). "
                f"나쁜 merge·잘못된 리다이렉트로 비워졌는지 확인하라 — "
                f"룰 0줄로 판정이 나가면 아무도 못 알아챈다")
    return ""
