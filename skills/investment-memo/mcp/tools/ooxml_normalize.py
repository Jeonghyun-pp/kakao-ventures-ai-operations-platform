"""OOXML 정규화 — python-docx가 다룰 수 없는 구조를 걷어낸다.

투심보고서 템플릿은 구글 닥스에서 내보내진 이력이 있어 본문과 표 셀 곳곳에
`<w:sdt>`(구조화 문서 태그) 래퍼가 남아 있다. 태그명이 `goog_rdk_*`인 것으로
보아 제안(suggestion) 기능의 잔해이며, 의미 있는 콘텐츠 컨트롤이 아니다.

문제는 python-docx가 `<w:tc>`의 직계 `<w:p>`만 문단으로 인식한다는 점이다.
`<w:sdt><w:sdtContent><w:p>` 구조로 감싸인 셀은 `cell.text`가 빈 문자열이 되고
`cell.paragraphs`도 비어서, 읽기도 쓰기도 불가능하다. 템플릿의 표 8개 중
26개 셀이 이 상태라 표 자동 채우기가 원천적으로 막혀 있었다.

`<w:sdt>`는 렌더링상 투명한 래퍼이므로, 자식을 제자리에 풀어놓고 래퍼만
제거하면 화면 출력은 완전히 동일하면서 python-docx가 접근할 수 있게 된다.
"""

from docx.oxml.ns import qn

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


def _visible_text(path: str) -> str:
    """docx의 화면 노출 텍스트를 전부 이어붙인다 (정규화 무결성 검증용)."""
    import re
    import zipfile

    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def normalize_file(path: str) -> tuple[int, bool]:
    """docx 파일을 제자리에서 정규화한다.

    텍스트가 한 글자라도 달라지면 저장하지 않고 예외를 던진다.

    Returns:
        (풀어낸 sdt 개수, 변경 여부)
    """
    import tempfile
    from pathlib import Path

    from docx import Document

    before = _visible_text(path)
    document = Document(path)
    removed = unwrap_junk_sdt(document)

    if removed == 0:
        return (0, False)

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
    return (removed, True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("사용법: python -m tools.ooxml_normalize <파일.docx> [...]")
        raise SystemExit(2)

    for target in sys.argv[1:]:
        count, changed = normalize_file(target)
        state = f"{count}개 제거" if changed else "변경 없음"
        print(f"  {target}: {state}")
