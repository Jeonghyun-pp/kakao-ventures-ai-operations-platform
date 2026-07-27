"""템플릿에 이미 존재하는 표를 제자리에서 채운다.

표를 새로 만들지 않는 이유: 템플릿의 표에는 열 너비, 셀 병합, 테두리,
음영, 표 스타일이 이미 KV 양식대로 잡혀 있다. 새로 생성하면 이 서식을
전부 재현해야 하고 미세하게 어긋난다. 기존 셀에 값만 쓰면 서식은 자동으로
보존된다.

표 식별은 인덱스가 아니라 라벨 서명으로 한다. 템플릿에 표가 추가되거나
순서가 바뀌어도 깨지지 않게 하기 위함이다.

두 가지 채우기 방식:
  label_value — 라벨 셀 오른쪽 셀에 값을 쓴다 (회사정보, 투자조건)
  grid        — 헤더 아래 데이터 행을 채운다. 행이 모자라면 마지막 데이터
                행의 서식을 복제해 늘린다 (핵심인력, 투자내역 등)
"""

import copy

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

# 표 바로 앞에 오는 제목 → 표 키.
# 라벨 서명보다 우선한다. 지식재산권 표처럼 서명이 ("구분","구분")으로 축퇴되어
# 다른 표와 구분되지 않는 경우가 있기 때문이다.
HEADING_TO_KEY = {
    "투자 대상 회사": "회사정보",
    "투자 조건": "투자조건",
    "회사 히스토리": "회사히스토리",
    "핵심 인력": "핵심인력",
    "투자내역 및 채무 현황": "투자내역",
    "지식재산권": "지식재산권",
    "주요 경쟁자": "주요경쟁자",
    "M&A": "회수방안",
    "IPO": "회수방안",
}

# 표 식별 서명과 채우기 방식.
# signature: 표에 반드시 존재해야 하는 라벨들. 하나라도 없으면 다른 표로 본다.
TABLE_SPECS = [
    {
        "key": "회사정보",
        "signature": ("회사명", "대표이사", "설립일", "법인등록번호"),
        "mode": "label_value",
    },
    {
        "key": "투자조건",
        "signature": ("투자업체", "총 투자금액", "투자 전 기업가치"),
        "mode": "label_value",
    },
    {
        "key": "회사히스토리",
        "signature": ("일자", "구분"),
        "mode": "grid",
    },
    {
        "key": "핵심인력",
        "signature": ("이름", "직급/역할", "학력", "주요경력"),
        "mode": "grid",
    },
    {
        "key": "투자내역",
        "signature": ("날짜", "유치 금액(원)", "주당단가", "참여 투자자"),
        "mode": "grid",
    },
    {
        "key": "주요경쟁자",
        "signature": ("구분", "기업명", "내용"),
        "mode": "grid",
    },
    {
        # 라벨 서명이 ("구분","구분")으로 축퇴되어 투자조건·주요경쟁자와
        # 구분되지 않는다. 제목 기반 식별에만 의존한다.
        "key": "지식재산권",
        "signature": (),
        "mode": "grid",
    },
    {
        "key": "회수방안",
        "signature": ("회사", "기대치", "당위성"),
        "mode": "grid",
    },
]


def _cell_text(cell) -> str:
    return cell.text.strip()


def _row_labels(table) -> set[str]:
    """표 안의 모든 셀 텍스트를 모은다 (식별용)."""
    out = set()
    for row in table.rows:
        for cell in row.cells:
            text = _cell_text(cell)
            if text:
                # 줄바꿈이 섞인 라벨(예: "공동투자자\n(공동기표 조건은 아님)")은
                # 첫 줄만으로도 매칭되게 한다
                out.add(text)
                out.add(text.split("\n")[0].strip())
    return out


def _normalize_heading(text: str) -> str:
    """제목에서 번호·주석을 걷어내 비교 가능한 형태로 만든다.

    "3) 투자내역 및 채무 현황 (TBD)" → "투자내역 및 채무 현황"
    """
    import re

    out = re.sub(r"^\s*\d+\s*[).]\s*", "", text.strip())
    out = re.sub(r"\s*\((TBD|미정)\)\s*$", "", out, flags=re.I)
    return out.strip()


def identify_by_heading(document) -> dict:
    """본문을 순서대로 훑어 각 표 직전의 제목으로 표를 식별한다.

    제목이 sdt에 갇혀 있으면 텍스트가 비어 보이므로, 호출 전에
    ooxml_normalize.unwrap_junk_sdt()가 적용되어 있어야 한다.
    """
    found = {}
    current = None

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, document)
            style = para.style.name or ""
            if style.startswith("Heading") or style.startswith("제목"):
                text = _normalize_heading(para.text)
                current = HEADING_TO_KEY.get(text)
        elif child.tag == qn("w:tbl"):
            if current and current not in found:
                found[current] = Table(child, document)
                current = None  # 같은 제목 아래 두 번째 표는 잡지 않는다

    return found


def identify_tables(document) -> dict:
    """문서의 표를 식별한다. 제목 기반을 우선하고 라벨 서명으로 보완한다.

    Returns:
        {"회사정보": <Table>, ...} — 못 찾은 키는 포함되지 않는다
    """
    found = identify_by_heading(document)
    used = {id(t._tbl) for t in found.values()}

    for spec in TABLE_SPECS:
        if spec["key"] in found or not spec["signature"]:
            continue
        for table in document.tables:
            if id(table._tbl) in used:
                continue
            labels = _row_labels(table)
            if all(sig in labels for sig in spec["signature"]):
                found[spec["key"]] = table
                used.add(id(table._tbl))
                break

    return found


def _distinct_cells(row) -> list:
    """병합으로 중복 반환되는 셀을 제거하고 왼쪽부터 고유 셀만 돌려준다."""
    out = []
    seen = set()
    for cell in row.cells:
        key = id(cell._tc)
        if key not in seen:
            seen.add(key)
            out.append(cell)
    return out


def fill_label_value(table, data: dict, fill_cell) -> tuple[list[str], list[str]]:
    """라벨 셀 오른쪽 셀에 값을 쓴다.

    data에 없는 라벨은 건드리지 않는다. 템플릿에 들어 있는 법률 보일러플레이트
    (투자금 사용처 실사, 대상거래 처리 방안 등)를 그대로 살리기 위함이다.

    Returns:
        (채운 라벨, data에 있었지만 표에서 못 찾은 라벨)
    """
    filled = []
    remaining = dict(data)

    for row in table.rows:
        cells = _distinct_cells(row)
        for pos, cell in enumerate(cells):
            label = _cell_text(cell)
            if not label:
                continue
            # 줄바꿈 라벨은 첫 줄로도 조회한다
            key = label if label in remaining else label.split("\n")[0].strip()
            if key not in remaining:
                continue
            if pos + 1 >= len(cells):
                continue
            fill_cell(cells[pos + 1], str(remaining.pop(key)))
            filled.append(key)

    return filled, list(remaining)


def _clone_row(table, src_index: int):
    """src_index 행의 XML을 복제해 표 끝에 붙이고 새 행을 반환한다.

    python-docx의 add_row()는 서식 없는 빈 행을 만들기 때문에 사용하지 않는다.
    기존 행을 복제하면 셀 너비·테두리·음영이 그대로 따라온다.
    """
    src_tr = table.rows[src_index]._tr
    new_tr = copy.deepcopy(src_tr)
    src_tr.addnext(new_tr)
    return table.rows[src_index + 1]


def _clear_cell(cell):
    """셀의 모든 단락을 비운다 (첫 단락만 남기고 텍스트 제거)."""
    for para in cell.paragraphs[1:]:
        para._element.getparent().remove(para._element)
    para = cell.paragraphs[0]
    for run in list(para.runs):
        run._element.getparent().remove(run._element)


def _unmerge_vertical(table, header_rows: int) -> int:
    """데이터 행의 세로 병합(w:vMerge)을 해제한다.

    템플릿 표에는 이전 보고서에서 남은 세로 병합이 걸려 있는 경우가 있다.
    python-docx는 세로로 병합된 셀을 각 행에서 **같은 객체**로 반환하므로,
    행마다 다른 값을 쓰면 나중 값이 앞 값을 덮어써 조용히 데이터가 사라진다.
    실제로 주요경쟁자 표(0열 r1·r2에 vMerge=restart)에서 첫 행 값이 유실됐다.

    행별로 값이 다른 데이터를 채우는 것이 목적이므로 병합을 먼저 푼다.

    Returns:
        해제한 셀 수
    """
    removed = 0
    for row in table.rows[header_rows:]:
        # row.cells를 쓰면 안 된다. python-docx는 병합 연속(vMerge continue) 행의
        # 셀을 요청하면 병합 시작 행의 <w:tc>를 대신 돌려주므로, 연속 행의
        # vMerge는 영영 보이지 않고 시작 행만 두 번 방문하게 된다.
        # 원문 <w:tr>의 <w:tc>를 직접 순회해야 전부 걷힌다.
        for tc in row._tr.findall(qn("w:tc")):
            tc_pr = tc.find(qn("w:tcPr"))
            if tc_pr is None:
                continue
            for vmerge in tc_pr.findall(qn("w:vMerge")):
                tc_pr.remove(vmerge)
                removed += 1
    return removed


def fill_grid(table, rows: list, fill_cell, header_rows: int = 1) -> int:
    """헤더 아래 데이터 행을 rows로 교체한다.

    행이 모자라면 마지막 데이터 행을 복제해 늘리고, 남으면 비운다.

    Returns:
        채운 데이터 행 수
    """
    if not rows:
        return 0

    ncols = len(table.columns)

    # 세로 병합을 먼저 푼다. 풀지 않으면 병합된 셀이 여러 행에서 같은 객체로
    # 반환되어, 나중 행의 값이 앞 행의 값을 덮어쓴다.
    _unmerge_vertical(table, header_rows)

    # 필요한 만큼 행을 확보한다
    while len(table.rows) - header_rows < len(rows):
        _clone_row(table, len(table.rows) - 1)
        # 복제한 행이 병합 속성을 물려받았을 수 있으므로 다시 푼다
        _unmerge_vertical(table, header_rows)

    for offset, data_row in enumerate(rows):
        row = table.rows[header_rows + offset]
        cells = _distinct_cells(row)
        for ci in range(min(ncols, len(cells))):
            value = str(data_row[ci]) if ci < len(data_row) else ""
            fill_cell(cells[ci], value)

    # 템플릿에 남아 있던 잉여 데이터 행(예: "회사 설립", "-")을 비운다
    for extra in range(header_rows + len(rows), len(table.rows)):
        for cell in _distinct_cells(table.rows[extra]):
            _clear_cell(cell)

    return len(rows)


def fill_tables(document, data: dict, fill_cell) -> dict:
    """표 데이터를 일괄 반영한다.

    Args:
        document: docx.Document
        data: {"회사정보": {"회사명": "...", ...},
               "핵심인력": [["이름","직급","학력","경력"], ...]}
        fill_cell: (cell, text) -> None. writer_docx._fill_cell을 주입받는다.

    Returns:
        {"filled": {키: 설명}, "missing_tables": [...], "unmatched_labels": {...}}
    """
    tables = identify_tables(document)
    report = {"filled": {}, "missing_tables": [], "unmatched_labels": {}}

    for key, payload in data.items():
        if not payload:
            continue
        table = tables.get(key)
        if table is None:
            report["missing_tables"].append(key)
            continue

        spec = next((s for s in TABLE_SPECS if s["key"] == key), None)
        if spec is None:
            report["missing_tables"].append(key)
            continue

        if spec["mode"] == "label_value":
            filled, unmatched = fill_label_value(table, payload, fill_cell)
            report["filled"][key] = f"{len(filled)}개 항목"
            if unmatched:
                report["unmatched_labels"][key] = unmatched
        else:
            count = fill_grid(table, payload, fill_cell)
            report["filled"][key] = f"{count}행"

    return report
