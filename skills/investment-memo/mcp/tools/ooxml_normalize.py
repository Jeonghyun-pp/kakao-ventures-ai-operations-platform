"""OOXML 정규화 — python-docx가 다룰 수 없는 구조를 걷어내고 표 레이아웃을 잡는다.

## 1. sdt 잔해 제거 (unwrap_junk_sdt)

투심보고서 템플릿은 구글 닥스에서 내보내진 이력이 있어 본문과 표 셀 곳곳에
`<w:sdt>`(구조화 문서 태그) 래퍼가 남아 있다. 태그명이 `goog_rdk_*`인 것으로
보아 제안(suggestion) 기능의 잔해이며, 의미 있는 콘텐츠 컨트롤이 아니다.

문제는 python-docx가 `<w:tc>`의 직계 `<w:p>`만 문단으로 인식한다는 점이다.
`<w:sdt><w:sdtContent><w:p>` 구조로 감싸인 셀은 `cell.text`가 빈 문자열이 되고
`cell.paragraphs`도 비어서, 읽기도 쓰기도 불가능하다. 템플릿의 표 8개 중
26개 셀이 이 상태라 표 자동 채우기가 원천적으로 막혀 있었다.

`<w:sdt>`는 렌더링상 투명한 래퍼이므로, 자식을 제자리에 풀어놓고 래퍼만
제거하면 화면 출력은 완전히 동일하면서 python-docx가 접근할 수 있게 된다.

## 2. 표 레이아웃 정규화 (normalize_layout)

템플릿의 표 3개(회사 히스토리·지식재산권·회수방안)에 `<w:tblpPr>`이 걸려 있다.
이건 표를 본문 흐름에서 떼어내 절대좌표에 띄우는 **부동 표(floating table)**
속성으로, 앞 내용 길이가 바뀌면 표가 본문 위로 올라타 글자를 덮는다. 실제
산출본에서 표와 본문이 겹쳐 사람이 매번 손으로 잡아야 했다.

또 표마다 왼쪽 들여쓰기가 제각각이다(390/420/795/903 twip). 795·903짜리 표는
본문보다 눈에 띄게 오른쪽으로 밀려 "정렬이 안 맞는다"는 지적을 받는다.

레이아웃 정규화는 **텍스트를 한 글자도 바꾸지 않는다.** 서식 속성만 만진다.
"""

from docx.oxml.ns import qn

# 하우스 표 들여쓰기 (twip). 템플릿의 다수 표가 390~420이며 본문 불릿과 맞는다.
HOUSE_TABLE_INDENT = 420
# 이 범위 안이면 의도된 값으로 보고 건드리지 않는다
TABLE_INDENT_OK = (0, 450)

# 셀 안 불릿의 들여쓰기 (twip). 기본 440/440은 좁은 셀에서 글머리와 글자 사이가
# 과하게 벌어져 셀이 세 줄로 늘어난다.
CELL_BULLET_LEFT = 220
CELL_BULLET_HANGING = 220

# CT_PPr 자식의 스키마 순서 중 ind 삽입 위치 계산에 필요한 앞선 요소들
_PPR_BEFORE_IND = (
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
    "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
    "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing",
)

# CT_TblPr 자식의 스키마 순서 중 tblInd 앞에 와야 하는 요소들
_TBLPR_BEFORE_IND = (
    "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
    "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing",
)

# 걷어낼 sdt 태그 접두사. 구글 닥스 잔해만 대상으로 하고, 사람이 의도적으로
# 만든 콘텐츠 컨트롤(목차 등)은 건드리지 않는다.
JUNK_TAG_PREFIXES = ("goog_rdk",)


def _sdt_tag_name(sdt) -> str | None:
    """<w:sdt>의 <w:tag w:val="..."> 값을 반환한다. 없으면 None."""
    pr = sdt.find(qn("w:sdtPr"))
    if pr is None:
        return None
    tag = pr.find(qn("w:tag"))
    if tag is None:
        return None
    return tag.get(qn("w:val"))


def _is_junk(sdt) -> bool:
    name = _sdt_tag_name(sdt)
    if name is None:
        return False
    return name.startswith(JUNK_TAG_PREFIXES)


def unwrap_junk_sdt(document) -> int:
    """문서에서 구글 닥스 잔해 sdt를 풀어낸다.

    <w:sdt><w:sdtPr/><w:sdtContent>X</w:sdtContent></w:sdt> → X

    Args:
        document: docx.Document 객체

    Returns:
        풀어낸 sdt 개수
    """
    body = document.element.body
    removed = 0

    # 중첩 sdt가 있을 수 있으므로 더 이상 안 걸릴 때까지 반복한다.
    # 매 회 리스트를 새로 만들어 순회 중 트리 변경으로 인한 누락을 피한다.
    while True:
        targets = [sdt for sdt in body.iter(qn("w:sdt")) if _is_junk(sdt)]
        if not targets:
            break

        for sdt in targets:
            parent = sdt.getparent()
            if parent is None:
                continue

            content = sdt.find(qn("w:sdtContent"))
            index = list(parent).index(sdt)

            if content is not None:
                # sdtContent의 자식들을 sdt가 있던 자리에 그대로 끼워 넣는다
                for offset, child in enumerate(list(content)):
                    parent.insert(index + offset, child)

            parent.remove(sdt)
            removed += 1

    return removed


def count_junk_sdt(document) -> int:
    """걷어낼 대상 sdt 개수를 센다 (변경하지 않음)."""
    return sum(1 for sdt in document.element.body.iter(qn("w:sdt")) if _is_junk(sdt))


# ---------------------------------------------------------------------------
# 표 레이아웃 정규화 — 텍스트를 바꾸지 않는다
# ---------------------------------------------------------------------------


def _insert_ordered(parent, el, before_tags: tuple) -> None:
    """스키마 순서를 지켜 el을 parent에 끼워 넣는다."""
    from docx.oxml import OxmlElement  # noqa: F401  (호출자 편의)

    anchor = None
    for child in parent:
        local = child.tag.split("}")[-1]
        if local in before_tags:
            anchor = child
        else:
            break
    if anchor is None:
        parent.insert(0, el)
    else:
        anchor.addnext(el)


def _tbl_pr(tbl):
    from docx.oxml import OxmlElement

    pr = tbl.find(qn("w:tblPr"))
    if pr is None:
        pr = OxmlElement("w:tblPr")
        tbl.insert(0, pr)
    return pr


def unfloat_tables(document, indent: int = HOUSE_TABLE_INDENT) -> int:
    """부동 표(`w:tblpPr`)를 본문 흐름 안으로 되돌린다.

    `w:tblpPr`이 있으면 표가 절대좌표에 앵커되어 앞 본문 길이가 바뀔 때마다
    본문을 덮어쓴다. 속성을 제거하고 같은 크기의 왼쪽 들여쓰기로 대체하면
    보이는 위치는 사실상 같으면서 겹침이 사라진다.

    Returns:
        되돌린 표 개수
    """
    from docx.oxml import OxmlElement

    fixed = 0
    for tbl in document.element.body.iter(qn("w:tbl")):
        pr = tbl.find(qn("w:tblPr"))
        if pr is None:
            continue
        floats = pr.findall(qn("w:tblpPr"))
        if not floats:
            continue
        for el in floats:
            pr.remove(el)
        # 부동 표는 tblInd가 없는 경우가 많다 — 하우스 들여쓰기를 채워 준다
        if pr.find(qn("w:tblInd")) is None:
            ind = OxmlElement("w:tblInd")
            ind.set(qn("w:w"), str(indent))
            ind.set(qn("w:type"), "dxa")
            _insert_ordered(pr, ind, _TBLPR_BEFORE_IND)
        # tblOverlap=never도 함께 걸어 재부유 시에도 겹치지 않게 한다
        for el in pr.findall(qn("w:tblOverlap")):
            pr.remove(el)
        overlap = OxmlElement("w:tblOverlap")
        overlap.set(qn("w:val"), "never")
        _insert_ordered(pr, overlap, ("tblStyle",))
        fixed += 1
    return fixed


def normalize_table_indent(document, indent: int = HOUSE_TABLE_INDENT) -> int:
    """표 왼쪽 들여쓰기를 하우스 기준으로 맞춘다.

    템플릿의 핵심인력(795)·투자내역(903) 표는 본문보다 눈에 띄게 밀려 있어
    "왼쪽 정렬 좀"이라는 지적을 매번 받는다. TABLE_INDENT_OK 범위 밖만 고친다.

    Returns:
        고친 표 개수
    """
    from docx.oxml import OxmlElement

    lo, hi = TABLE_INDENT_OK
    fixed = 0
    for tbl in document.element.body.iter(qn("w:tbl")):
        pr = _tbl_pr(tbl)
        ind = pr.find(qn("w:tblInd"))
        if ind is None:
            ind = OxmlElement("w:tblInd")
            ind.set(qn("w:type"), "dxa")
            _insert_ordered(pr, ind, _TBLPR_BEFORE_IND)
            current = None
        else:
            try:
                current = int(ind.get(qn("w:w")) or 0)
            except ValueError:
                current = None
        if current is not None and lo <= current <= hi:
            continue
        ind.set(qn("w:w"), str(indent))
        ind.set(qn("w:type"), "dxa")
        fixed += 1
    return fixed


def trim_cell_trailing_empty(document) -> int:
    """표 셀 끝의 빈 문단을 지운다.

    셀에 세로 가운데 정렬(vAlign=center)이 걸려 있어도 뒤에 빈 문단이 달려
    있으면 그 빈 줄까지 포함해 가운데를 잡으므로 글자가 위로 붙어 보인다.
    `vAlign`을 아무리 만져도 안 고쳐지는 "셀 내용이 위에 붙는" 증상의 정체다.

    문단이 하나도 없는 셀은 Word가 파일을 손상으로 판정하므로 최소 1개는 남긴다.
    빈 문단에는 `<w:t>`가 없어 화면 텍스트는 달라지지 않는다.

    Returns:
        지운 문단 개수
    """
    removed = 0
    for tc in document.element.body.iter(qn("w:tc")):
        paras = tc.findall(qn("w:p"))
        # 뒤에서부터, 남은 문단이 1개가 될 때까지만
        for p in reversed(paras):
            if len(tc.findall(qn("w:p"))) <= 1:
                break
            text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
            if text or p.find(qn("w:drawing")) is not None or p.find(qn("w:pict")) is not None:
                break
            tc.remove(p)
            removed += 1
    return removed


def tighten_cell_bullets(
    document, left: int = CELL_BULLET_LEFT, hanging: int = CELL_BULLET_HANGING
) -> int:
    """표 셀 안 불릿의 글머리-본문 간격을 좁힌다.

    본문용 목록 정의(left=440, hanging=440)를 좁은 셀에 그대로 쓰면 글머리와
    글자 사이가 과하게 벌어져 한 줄이면 될 내용이 두세 줄로 늘어난다.
    셀 안 문단에만 명시 `w:ind`를 걸어 numbering.xml 기본값을 덮어쓴다.

    Returns:
        고친 문단 개수
    """
    from docx.oxml import OxmlElement

    fixed = 0
    for tc in document.element.body.iter(qn("w:tc")):
        for p in tc.findall(qn("w:p")):
            pPr = p.find(qn("w:pPr"))
            if pPr is None or pPr.find(qn("w:numPr")) is None:
                continue
            ind = pPr.find(qn("w:ind"))
            if ind is None:
                ind = OxmlElement("w:ind")
                _insert_ordered(pPr, ind, _PPR_BEFORE_IND)
            ind.set(qn("w:left"), str(left))
            ind.set(qn("w:hanging"), str(hanging))
            fixed += 1
    return fixed


def normalize_layout(document) -> dict:
    """표 레이아웃을 하우스 기준으로 일괄 정규화한다.

    텍스트는 바꾸지 않는다. 서식 속성만 만진다.

    Returns:
        {"unfloated": n, "reindented": n, "trimmed_cells": n, "tightened_bullets": n}
    """
    return {
        "unfloated": unfloat_tables(document),
        "reindented": normalize_table_indent(document),
        "trimmed_cells": trim_cell_trailing_empty(document),
        "tightened_bullets": tighten_cell_bullets(document),
    }


def audit_layout(document) -> list[str]:
    """정규화가 끝난 뒤 남은 레이아웃 위반을 보고한다 (변경하지 않음)."""
    problems = []
    for i, tbl in enumerate(document.element.body.iter(qn("w:tbl")), start=1):
        pr = tbl.find(qn("w:tblPr"))
        if pr is None:
            continue
        if pr.findall(qn("w:tblpPr")):
            problems.append(f"표{i}: 부동 표(tblpPr) 잔존 — 본문과 겹침")
        ind = pr.find(qn("w:tblInd"))
        if ind is not None:
            try:
                w = int(ind.get(qn("w:w")) or 0)
            except ValueError:
                w = 0
            if w > TABLE_INDENT_OK[1]:
                problems.append(f"표{i}: 들여쓰기 {w}twip — 본문보다 밀려 보임")
    return problems


def _visible_text(path: str) -> str:
    """docx의 화면 노출 텍스트를 전부 이어붙인다 (정규화 무결성 검증용)."""
    import re
    import zipfile

    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def normalize_file(path: str, layout: bool = True) -> tuple[dict, bool]:
    """docx 파일을 제자리에서 정규화한다.

    텍스트가 한 글자라도 달라지면 저장하지 않고 예외를 던진다. sdt 제거도
    레이아웃 정규화도 화면 텍스트를 바꾸지 않으므로, 이 검사가 통과하지
    못하면 어딘가 버그가 있다는 뜻이다.

    Args:
        layout: 표 부동/들여쓰기/셀 위생까지 정규화할지

    Returns:
        (변경 내역 dict, 변경 여부)
    """
    import tempfile
    from pathlib import Path

    from docx import Document

    before = _visible_text(path)
    document = Document(path)
    stats = {"sdt": unwrap_junk_sdt(document)}
    if layout:
        stats.update(normalize_layout(document))

    if not any(stats.values()):
        return (stats, False)

    tmp = tempfile.mktemp(suffix=".docx")
    document.save(tmp)

    after = _visible_text(tmp)
    if before != after:
        Path(tmp).unlink(missing_ok=True)
        raise RuntimeError(
            f"정규화가 텍스트를 변경함 — 저장하지 않음 ({path}). "
            f"길이 {len(before)} → {len(after)}"
        )

    Path(tmp).replace(path)
    return (stats, True)


if __name__ == "__main__":
    import sys

    from docx import Document

    if len(sys.argv) < 2:
        print("사용법: python -m tools.ooxml_normalize <파일.docx> [...]")
        raise SystemExit(2)

    for target in sys.argv[1:]:
        stats, changed = normalize_file(target)
        detail = ", ".join(f"{k}={v}" for k, v in stats.items() if v)
        print(f"  {target}: {detail if changed else '변경 없음'}")
        for problem in audit_layout(Document(target)):
            print(f"    ⚠️  {problem}")
