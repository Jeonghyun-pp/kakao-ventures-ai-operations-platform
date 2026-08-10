"""
.docx 템플릿 기반 투심보고서 생성 모듈

템플릿의 {{플래그}}를 찾아 sections dict의 내용으로 교체한다.
markdown 형식의 내용을 docx 단락(볼드 헤드라인, 불릿, 이미지)으로 변환한다.

서식은 텍스트 접두(•) 대신 **워드 네이티브 목록(numPr)** 으로 만든다.
템플릿 numbering.xml에 이미 정의된 numId를 재사용하므로 정렬·들여쓰기가
워드 기본 목록과 동일하게 일관된다.

스타일 스펙:
- 헤드라인: 맑은 고딕 9pt 볼드, #000000. (대괄호로 감싸지 않음 — 프롬프트가 쓴 텍스트 그대로)
  - 일반 섹션: 네이티브 불릿(BULLET_NUMID, ilvl0) — 볼드 헤드라인
  - 투자포인트: 네이티브 자동번호(a) b) c), 전용 num 인스턴스) — 볼드 헤드라인(카테고리 단어 [시장] 등은 보존)
- 불릿: 맑은 고딕 9pt, 네이티브 불릿(depth→ilvl: 0/1/2 = •/o/▪)
- 이미지: 가운데 정렬, Figure 번호 캡션 (굴림 9pt 기울임, #1F497D)
- 작성일: 맑은 고딕 16pt, "년도.월" 형식
"""

import re
import shutil
import logging
from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .ooxml_normalize import audit_layout, normalize_layout, unwrap_junk_sdt
from .tables_fill import fill_tables

logger = logging.getLogger(__name__)

# 스타일 상수
FONT_NAME = "맑은 고딕"
FONT_SIZE = Pt(9)
FONT_COLOR = RGBColor(0x00, 0x00, 0x00)
CAPTION_FONT = "굴림"
CAPTION_COLOR = RGBColor(0x1F, 0x49, 0x7D)
DATE_FONT_SIZE = Pt(16)

# 워드 네이티브 목록 설정
# 템플릿의 numId에 의존하지 않고, 생성 시 자체 불릿/a·b·c 목록 정의를 numbering.xml에 직접 주입한다.
# (Word로 템플릿을 편집하면 numbering이 재번호되어 하드코딩 numId가 깨지므로 분리한다.)
BULLET_LEFT0 = 1440   # 불릿 ilvl0 left (이후 +720/level) — 소제목과 정렬 유지
BULLET_HANGING = 360
LIST_STYLE = "List Paragraph"

# 콘텐츠 단락 서식 (B 원본 실측값) — depth0 들여쓰기/줄간격/글머리 폰트를 명시해
# numbering·스타일 기본값 의존을 제거한다.
LIST_LEFT = 924       # depth0 left indent (twip)
LIST_HANGING = 357    # 번호/불릿 매달기 폭
SPACE_BEFORE = 120    # 단락 앞 간격
LINE_SPACING = 240    # lineRule=auto (1.0줄)
SZ_HALFPT = "18"      # 9pt (half-point)

FLAG_TO_KEY = {
    "{{투자포인트}}": "투자 포인트",
    "{{문제정의}}": "문제정의",
    "{{가설}}": "가설",
    "{{서비스상세}}": "서비스 상세",
    "{{지표}}": "지표",
    "{{BM}}": "BM",
    "{{향후전략및계획}}": "향후 전략 및 계획",       # 전략 + KPI 목표 및 마일스톤 통합
    "{{FinancialProjection}}": "Financial Projection",
    "{{시장현황및전망}}": "시장 현황 및 전망",         # 시장 현황 + 시장 전망 통합
    "{{주요경쟁자}}": "주요 경쟁자",
    "{{경쟁우위평가}}": "경쟁우위 평가",
    "{{InvestmentAnalysis}}": "Investment Analysis",  # 신규 섹션
}

ABC_HEADLINE_SECTIONS = {"투자 포인트"}

# 이미지 크기 (inch). A4 세로 + 좌우 여백 기준 본문 폭이 6.19"다.
# 이 폭을 넘기면 그림이 본문 밖으로 삐져나가 인쇄본에서 잘린다.
TEXT_WIDTH_IN = 6.19
IMG_DEFAULT_WIDTH_IN = 5.0
# 가로로 긴 로드맵·타임라인 슬라이드를 고정 폭 5"로 넣으면 높이가 0.5" 밑으로
# 눌려 글자를 읽을 수 없다. 최소 이 높이는 확보하도록 폭을 키운다.
IMG_MIN_HEIGHT_IN = 0.9
# 본문 폭까지 키워도 이 높이가 안 나오면 어떻게 배치해도 판독 불가다 — 경고한다.
IMG_UNREADABLE_HEIGHT_IN = 0.7
# 한 페이지를 넘기지 않도록 하는 상한
IMG_MAX_HEIGHT_IN = 7.5


def write_docx(
    sections: dict,
    company_name: str,
    folder_path: str,
    section_images: dict | None = None,
    template_path: str | None = None,
    tables: dict | None = None,
    warnings: list | None = None,
) -> str:
    """템플릿 .docx의 플래그를 교체하여 투심보고서를 생성한다.

    Args:
        tables: 표 데이터. {"회사정보": {"회사명": "..."}, "핵심인력": [[...]]}
                tools/tables_fill.py의 TABLE_SPECS 키를 사용한다.
                생략하면 표는 템플릿 상태 그대로 남는다.
        warnings: 사람이 손봐야 하는 항목을 담아 돌려줄 리스트 (선택).
                  로그로만 남기면 아무도 안 보므로 호출자가 사용자에게 전달한다.
    """
    if template_path is None:
        template_path = str(
            Path(__file__).resolve().parent.parent / "template" / "투심보고서_템플릿.docx"
        )

    output_dir = Path(folder_path).resolve()
    today_display = date.today().strftime("%Y.%m")
    safe_name = re.sub(r'[^\w\-]', '_', company_name)
    filename = f"투심보고서_{safe_name}_{date.today().strftime('%Y-%m-%d')}.docx"
    output_path = output_dir / filename

    shutil.copy2(template_path, str(output_path))
    doc = Document(str(output_path))

    # 구글 닥스에서 내보낸 템플릿으로 교체될 경우를 대비한 방어.
    # sdt에 갇힌 셀은 python-docx가 읽지도 쓰지도 못하므로 표 채우기가 실패한다.
    junk = unwrap_junk_sdt(doc)
    if junk:
        logger.warning(
            "템플릿에서 구글 닥스 sdt 잔해 %d개를 제거함. "
            "tools/ooxml_normalize.py로 템플릿을 미리 정규화해 둘 것.",
            junk,
        )

    # 단순 텍스트 교체
    _replace_flag_simple(doc, "{{회사명}}", company_name)
    _replace_flag_date(doc, "{{작성일}}", today_display)

    # 표 채우기 (플래그 교체 전에 수행 — 표는 플래그와 무관한 별도 경로)
    if tables:
        report = fill_tables(doc, tables, _fill_cell)
        if report["missing_tables"]:
            logger.warning("템플릿에서 못 찾은 표: %s", report["missing_tables"])
        if report["unmatched_labels"]:
            logger.warning("표에서 못 찾은 라벨: %s", report["unmatched_labels"])
        if report.get("dropped_columns"):
            logger.warning("표 열 부족으로 버려진 값: %s", report["dropped_columns"])
            if warnings is not None:
                warnings.extend(
                    f"표 열 부족: {k} — {v}" for k, v in report["dropped_columns"].items()
                )

    # 자체 numbering 주입 (템플릿 numId에 의존하지 않음): 불릿 + a) b) c)
    bullet_num_id = _ensure_bullet_num(doc)
    abc_num_id = _create_abc_num(doc)

    # 섹션 내용 교체
    used_images: set[str] = set()
    figure_counter = [0]
    image_warnings: list[str] = []

    for flag, key in FLAG_TO_KEY.items():
        content = sections.get(key, "")
        if not content or not str(content).strip():
            content = "[작성 필요]"

        sec_imgs = []
        if section_images and key in section_images and key != "투자 포인트":
            sec_imgs = section_images[key]

        _replace_flag_with_content(
            doc, flag, str(content), output_dir, used_images, sec_imgs,
            section_key=key, figure_counter=figure_counter, abc_num_id=abc_num_id,
            company_name=company_name, bullet_num_id=bullet_num_id,
            image_warnings=image_warnings,
        )

    # 표 레이아웃 정규화 — 반드시 표 채우기·내용 삽입이 끝난 뒤에 한다.
    # 셀 끝 빈 문단 정리와 부동 표 해제는 채워진 결과에도 적용돼야 한다.
    layout = normalize_layout(doc)
    if any(layout.values()):
        logger.info("표 레이아웃 정규화: %s", layout)
    for problem in audit_layout(doc):
        logger.warning("레이아웃 위반 잔존 — %s", problem)
        if warnings is not None:
            warnings.append(f"레이아웃: {problem}")
    if warnings is not None:
        warnings.extend(f"이미지 판독 불가: {w}" for w in image_warnings)

    doc.save(str(output_path))
    return str(output_path.resolve())


# ---------------------------------------------------------------------------
# 플래그 교체
# ---------------------------------------------------------------------------

def _replace_flag_simple(doc, flag: str, replacement: str):
    """단순 텍스트 플래그를 교체한다."""
    for paragraph in doc.paragraphs:
        if flag in paragraph.text:
            _replace_in_runs(paragraph, flag, replacement)
            return


def _replace_flag_date(doc, flag: str, date_str: str):
    """작성일 플래그를 교체한다. 맑은 고딕 16pt."""
    for paragraph in doc.paragraphs:
        if flag in paragraph.text:
            for run in paragraph.runs:
                run.text = ""
            run = paragraph.add_run(date_str)
            _apply_font(run, FONT_NAME, DATE_FONT_SIZE, FONT_COLOR)
            return


def _replace_in_runs(paragraph, flag: str, replacement: str):
    """단락의 run들에서 플래그를 찾아 교체한다. run 분리 대응."""
    for run in paragraph.runs:
        if flag in run.text:
            run.text = run.text.replace(flag, replacement)
            return

    full_text = "".join(run.text for run in paragraph.runs)
    if flag in full_text:
        new_text = full_text.replace(flag, replacement)
        for i, run in enumerate(paragraph.runs):
            run.text = new_text if i == 0 else ""


def _picture_size(abs_path: Path) -> tuple:
    """네이티브 비율을 보고 삽입 크기를 정한다.

    고정 폭 5"로 넣으면 가로로 긴 슬라이드(예: 1620×137px 마일스톤 로드맵)가
    5"×0.42"로 눌려 표 안 글자가 5pt 이하가 된다. 사람이 열어보기 전까지
    아무도 모르고, 열어봐도 원본을 다시 캡처하는 것 말고는 손쓸 수 없다.

    Returns:
        (width, height, warning|None) — width/height는 docx.shared.Inches
    """
    from docx.image.image import Image as DocxImage

    try:
        img = DocxImage.from_file(str(abs_path))
        ratio = img.px_width / img.px_height
    except Exception:  # 메타데이터를 못 읽으면 기존 동작 유지
        return Inches(IMG_DEFAULT_WIDTH_IN), None, None

    width = IMG_DEFAULT_WIDTH_IN
    height = width / ratio

    # 너무 납작하면 본문 폭 한도까지 키운다
    if height < IMG_MIN_HEIGHT_IN:
        width = min(TEXT_WIDTH_IN, IMG_MIN_HEIGHT_IN * ratio)
        height = width / ratio

    # 너무 길면 한 페이지에 맞춘다
    if height > IMG_MAX_HEIGHT_IN:
        height = IMG_MAX_HEIGHT_IN
        width = height * ratio

    width = min(width, TEXT_WIDTH_IN)
    height = width / ratio

    warning = None
    if height < IMG_UNREADABLE_HEIGHT_IN:
        warning = (
            f"{abs_path.name}: 원본 {img.px_width}×{img.px_height}px는 본문 폭"
            f"({TEXT_WIDTH_IN}\")에 넣어도 높이 {height:.2f}\"라 글자를 읽을 수 없음. "
            "원본 슬라이드를 상하 2단으로 잘라 두 장으로 넣을 것"
        )
    return Inches(width), Inches(height), warning


def _add_picture(paragraph, abs_path: Path):
    """비율을 지켜 그림을 넣는다. 판독 불가 크기면 경고를 남긴다."""
    width, height, warning = _picture_size(abs_path)
    run = paragraph.add_run()
    if height is None:
        run.add_picture(str(abs_path), width=width)
    else:
        run.add_picture(str(abs_path), width=width, height=height)
    if warning:
        logger.warning("이미지 판독 불가 — %s", warning)
    return warning


def _replace_flag_with_content(
    doc, flag: str, content: str, output_dir: Path,
    used_images: set, section_images: list,
    section_key: str = "", figure_counter: list | None = None,
    abc_num_id: int | None = None, company_name: str = "",
    bullet_num_id: int | None = None, image_warnings: list | None = None,
):
    """플래그를 markdown 내용으로 교체한다. 헤드라인/불릿은 워드 네이티브 목록으로 만든다."""
    if figure_counter is None:
        figure_counter = [0]
    is_investment_point = (section_key == "투자 포인트")
    is_competitor = (section_key == "주요 경쟁자")
    is_financial = (section_key == "Financial Projection")
    use_abc = section_key in ABC_HEADLINE_SECTIONS and abc_num_id is not None

    for _, paragraph in enumerate(doc.paragraphs):
        if flag not in paragraph.text:
            continue

        _clear_paragraph(paragraph)

        lines = content.strip().split("\n")
        current_element = paragraph._element
        first_line = True

        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1
            stripped = line.rstrip()
            if not stripped:
                continue

            # 마크다운 표: 연속된 "|...|" 줄을 한 번에 소비
            if re.match(r'^\s*\|.*\|\s*$', stripped):
                table_lines = [stripped]
                while i < len(lines) and re.match(r'^\s*\|.*\|\s*$', lines[i].rstrip()):
                    table_lines.append(lines[i].rstrip())
                    i += 1
                rows = _parse_md_table(table_lines)
                if rows and is_competitor:
                    # 주요 경쟁자 섹션만 실제 docx 표로 렌더
                    tbl_el = _add_table_after(doc, current_element, rows)
                    current_element = tbl_el
                    first_line = False
                elif rows:
                    # 그 외 섹션: 표를 만들지 않고 각 행을 " / " join한 일반 텍스트로 폴백
                    for row in rows:
                        p = _get_or_create(doc, paragraph, current_element, first_line)
                        _add_formatted_runs(p, " / ".join(c for c in row if c))
                        current_element = p._element
                        first_line = False
                continue

            # 이미지 라인
            img_match = re.search(r'!\[(.*?)\]\((.*?)\)', stripped)
            if img_match and not is_investment_point:
                caption_text = img_match.group(1)
                img_path = img_match.group(2)
                if img_path not in used_images:
                    used_images.add(img_path)
                    abs_path = output_dir / img_path
                    if abs_path.exists():
                        p = _get_or_create(doc, paragraph, current_element, first_line)
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        warn = _add_picture(p, abs_path)
                        if warn and image_warnings is not None:
                            image_warnings.append(warn)
                        current_element = p._element
                        first_line = False
                        # 캡션 (Financial Projection은 "{회사}'s Financial Projection" 고정, 그 외 Figure 번호)
                        cap_p = _new_paragraph_after(doc, current_element)
                        cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        figure_counter[0] += 1
                        if is_financial:
                            cap_text = f"Figure {figure_counter[0]}. {company_name}'s Financial Projection"
                        else:
                            cap_text = f"Figure {figure_counter[0]}."
                            if caption_text:
                                cap_text += f" {caption_text}"
                        cap_run = cap_p.add_run(cap_text)
                        _apply_font(cap_run, CAPTION_FONT, FONT_SIZE, CAPTION_COLOR, italic=True)
                        current_element = cap_p._element
                continue

            # 하위하위 불릿: "    - 텍스트" (4칸, ilvl2)
            deep_bullet = re.match(r'^    - (.+)$', stripped)
            if deep_bullet:
                p = _get_or_create(doc, paragraph, current_element, first_line)
                _set_list_paragraph(p, bullet_num_id, ilvl=2)
                _apply_para_format(p, depth=2, bold=False, abc=use_abc)
                _add_formatted_runs(p, deep_bullet.group(1))
                current_element = p._element
                first_line = False
                continue

            # 하위 불릿: "  - 텍스트" (2칸, ilvl1)
            sub_bullet = re.match(r'^  - (.+)$', stripped)
            if sub_bullet:
                p = _get_or_create(doc, paragraph, current_element, first_line)
                _set_list_paragraph(p, bullet_num_id, ilvl=1)
                _apply_para_format(p, depth=1, bold=False, abc=use_abc)
                _add_formatted_runs(p, sub_bullet.group(1))
                current_element = p._element
                first_line = False
                continue

            # 불릿: "- 텍스트" (ilvl0)
            bullet = re.match(r'^- (.+)$', stripped)
            if bullet:
                p = _get_or_create(doc, paragraph, current_element, first_line)
                _set_list_paragraph(p, bullet_num_id, ilvl=0)
                _apply_para_format(p, depth=0, bold=False, abc=use_abc)
                _add_formatted_runs(p, bullet.group(1))
                current_element = p._element
                first_line = False
                continue

            # 볼드 헤드라인: **[텍스트]** 또는 **텍스트**
            bold_match = re.match(r'^\*\*(.*?)\*\*\s*$', stripped)
            if bold_match:
                # 헤드라인은 대괄호로 감싸지 않는다. 프롬프트가 쓴 텍스트를 그대로 사용하므로
                # 서술형 헤드라인엔 대괄호가 없고, 카테고리 단어([시장] 등)는 그대로 보존된다.
                wrapped = bold_match.group(1)
                p = _get_or_create(doc, paragraph, current_element, first_line)
                if use_abc and abc_num_id is not None:
                    # 투자포인트: 워드가 a) b) c)를 자동 부여하므로 수기 접두는 제거.
                    # numbering ilvl=1(a/b/c)이지만 들여쓰기 depth=0(불릿과 같은 left=924).
                    wrapped = re.sub(r'^[a-z]\)\s*', '', wrapped)
                    _set_list_paragraph(p, abc_num_id, ilvl=1, style_name=None)
                else:
                    # 일반 섹션: 네이티브 불릿(ilvl0) + 볼드 헤드라인
                    _set_list_paragraph(p, bullet_num_id, ilvl=0)
                _apply_para_format(p, depth=0, bold=True, abc=use_abc)
                run = p.add_run(wrapped)
                _apply_font(run, FONT_NAME, FONT_SIZE, FONT_COLOR, bold=True)
                current_element = p._element
                first_line = False
                continue

            # 일반 텍스트 (목록 아님)
            p = _get_or_create(doc, paragraph, current_element, first_line)
            _add_formatted_runs(p, stripped)
            current_element = p._element
            first_line = False

        # section_images 처리
        if section_images and not is_investment_point:
            for img_ref in section_images:
                file_path = img_ref.get("file", "")
                caption_text = img_ref.get("caption", "")
                if file_path and file_path not in used_images:
                    used_images.add(file_path)
                    abs_path = output_dir / file_path
                    if abs_path.exists():
                        img_p = _new_paragraph_after(doc, current_element)
                        img_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        warn = _add_picture(img_p, abs_path)
                        if warn and image_warnings is not None:
                            image_warnings.append(warn)
                        current_element = img_p._element
                        figure_counter[0] += 1
                        cap_p = _new_paragraph_after(doc, current_element)
                        cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        cap_text = f"Figure {figure_counter[0]}."
                        if caption_text:
                            cap_text += f" {caption_text}"
                        cap_run = cap_p.add_run(cap_text)
                        _apply_font(cap_run, CAPTION_FONT, FONT_SIZE, CAPTION_COLOR, italic=True)
                        current_element = cap_p._element

        return


# ---------------------------------------------------------------------------
# 네이티브 목록 헬퍼
# ---------------------------------------------------------------------------

# CT_PPr 자식 요소의 스키마 순서 (ECMA-376). 삽입 위치 계산용.
_PPR_ORDER = [
    'pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'framePr', 'widowControl',
    'numPr', 'suppressLineNumbers', 'pBdr', 'shd', 'tabs', 'suppressAutoHyphens',
    'kinsoku', 'wordWrap', 'overflowPunct', 'topLinePunct', 'autoSpaceDE',
    'autoSpaceDN', 'bidi', 'adjustRightInd', 'snapToGrid', 'spacing', 'ind',
    'contextualSpacing', 'mirrorIndents', 'suppressOverlap', 'jc', 'textDirection',
    'textAlignment', 'textboxTightWrap', 'outlineLvl', 'divId', 'cnfStyle', 'rPr',
]


def _insert_pPr_child(pPr, el):
    """pPr에 el을 스키마 순서에 맞는 위치로 삽입한다 (기존 pBdr·shd 등을 보존)."""
    tag = el.tag.split('}')[-1]
    my = _PPR_ORDER.index(tag)
    for child in pPr:
        ct = child.tag.split('}')[-1]
        if ct in _PPR_ORDER and _PPR_ORDER.index(ct) > my:
            child.addprevious(el)
            return
    pPr.append(el)


def _set_list_paragraph(paragraph, num_id: int, ilvl: int, style_name: str | None = LIST_STYLE):
    """단락을 워드 네이티브 목록 항목으로 만든다.

    style_name이 주어지면 해당 단락 스타일(기본 'List Paragraph')을 적용하고,
    pPr에 <w:numPr><w:ilvl/><w:numId/></w:numPr>를 스키마 순서에 맞게 주입한다.
    """
    if style_name:
        paragraph.style = style_name
    pPr = paragraph._element.get_or_add_pPr()
    for old in pPr.findall(qn('w:numPr')):
        pPr.remove(old)
    numPr = OxmlElement('w:numPr')
    il = OxmlElement('w:ilvl'); il.set(qn('w:val'), str(ilvl))
    ni = OxmlElement('w:numId'); ni.set(qn('w:val'), str(num_id))
    numPr.append(il); numPr.append(ni)
    _insert_pPr_child(pPr, numPr)


def _apply_para_format(paragraph, depth: int, bold: bool, abc: bool):
    """콘텐츠 단락에 명시적 들여쓰기·줄간격·단락표시 폰트를 부여한다 (B 원본값).

    - ind: leftChars=0 항상.
        · 투자포인트(abc) depth0 → left=924/hanging=357 명시 (a/b/c 헤드라인·불릿 정렬).
        · 본문 depth0 및 모든 depth≥1 → left/hanging 생략 → numbering 의존
          (numId22 ilvl0=1440 → a)/b) 소제목과 정렬, ilvl1=2160, ilvl2=2880).
    - spacing: before=120, line=240, lineRule=auto (모든 콘텐츠 단락 통일).
    - 단락표시(paragraph-mark) rPr: 맑은 고딕 9pt(+볼드) → 번호/불릿 글리프 폰트·크기를 본문과 일치.
    pPr 자식 스키마 순서(pStyle→numPr→spacing→ind→…→rPr)를 지킨다.
    """
    pPr = paragraph._element.get_or_add_pPr()

    # spacing (기존 제거 후 스키마 위치에 삽입)
    for old in pPr.findall(qn('w:spacing')):
        pPr.remove(old)
    sp = OxmlElement('w:spacing')
    sp.set(qn('w:before'), str(SPACE_BEFORE))
    sp.set(qn('w:line'), str(LINE_SPACING))
    sp.set(qn('w:lineRule'), 'auto')
    _insert_pPr_child(pPr, sp)

    # ind
    for old in pPr.findall(qn('w:ind')):
        pPr.remove(old)
    ind = OxmlElement('w:ind')
    ind.set(qn('w:leftChars'), '0')
    if abc and depth == 0:
        # 투자포인트만 명시 들여쓰기. 본문은 numbering(1440)에 맡겨 소제목과 정렬.
        ind.set(qn('w:left'), str(LIST_LEFT))
        ind.set(qn('w:hanging'), str(LIST_HANGING))
    _insert_pPr_child(pPr, ind)

    # 단락표시 rPr (글머리 글리프 폰트/크기 제어)
    for old in pPr.findall(qn('w:rPr')):
        pPr.remove(old)
    rPr = OxmlElement('w:rPr')
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), FONT_NAME)
    rFonts.set(qn('w:eastAsia'), FONT_NAME)
    rFonts.set(qn('w:hAnsi'), FONT_NAME)
    rPr.append(rFonts)
    if bold:
        rPr.append(OxmlElement('w:b'))
        rPr.append(OxmlElement('w:bCs'))
    color = OxmlElement('w:color'); color.set(qn('w:val'), '000000'); rPr.append(color)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), SZ_HALFPT); rPr.append(sz)
    szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), SZ_HALFPT); rPr.append(szCs)
    pPr.append(rPr)


# ---------------------------------------------------------------------------
# numbering 주입 (템플릿 의존 제거 — 자체 불릿/a·b·c 정의를 직접 만든다)
# ---------------------------------------------------------------------------

def _numbering_el(doc):
    """문서의 numbering 루트(<w:numbering>) 엘리먼트. 없으면 None."""
    try:
        return doc.part.numbering_part.element
    except (NotImplementedError, AttributeError):
        return None


def _add_abstract_num(numbering, levels: list[dict]) -> int:
    """새 abstractNum을 levels 스펙으로 만들어 모든 <w:num> 앞에 삽입, abstractNumId 반환.

    levels: [{"ilvl": int, "fmt": "bullet|decimal|lowerLetter", "text": str,
              "font": str|None, "left": int, "hanging": int}]
    """
    ids = [int(a.get(qn('w:abstractNumId'))) for a in numbering.findall(qn('w:abstractNum'))]
    new_aid = (max(ids) + 1) if ids else 0

    an = OxmlElement('w:abstractNum'); an.set(qn('w:abstractNumId'), str(new_aid))
    mll = OxmlElement('w:multiLevelType'); mll.set(qn('w:val'), 'hybridMultilevel')
    an.append(mll)
    for spec in levels:
        lvl = OxmlElement('w:lvl'); lvl.set(qn('w:ilvl'), str(spec["ilvl"]))
        start = OxmlElement('w:start'); start.set(qn('w:val'), '1'); lvl.append(start)
        nf = OxmlElement('w:numFmt'); nf.set(qn('w:val'), spec["fmt"]); lvl.append(nf)
        lt = OxmlElement('w:lvlText'); lt.set(qn('w:val'), spec["text"]); lvl.append(lt)
        jc = OxmlElement('w:lvlJc'); jc.set(qn('w:val'), 'left'); lvl.append(jc)
        pPr = OxmlElement('w:pPr')
        ind = OxmlElement('w:ind')
        ind.set(qn('w:left'), str(spec["left"])); ind.set(qn('w:hanging'), str(spec["hanging"]))
        pPr.append(ind); lvl.append(pPr)
        if spec.get("font"):
            rPr = OxmlElement('w:rPr'); rf = OxmlElement('w:rFonts')
            rf.set(qn('w:ascii'), spec["font"]); rf.set(qn('w:hAnsi'), spec["font"])
            rf.set(qn('w:hint'), 'default'); rPr.append(rf); lvl.append(rPr)
        an.append(lvl)

    first_num = numbering.find(qn('w:num'))
    if first_num is not None:
        first_num.addprevious(an)   # 모든 abstractNum은 num 앞에 와야 함
    else:
        numbering.append(an)
    return new_aid


def _add_num(numbering, abstract_id: int, start_override: bool = False) -> int:
    """abstract_id를 참조하는 새 <w:num>을 끝에 추가하고 numId 반환."""
    ids = [int(n.get(qn('w:numId'))) for n in numbering.findall(qn('w:num'))]
    new_id = (max(ids) + 1) if ids else 1
    num = OxmlElement('w:num'); num.set(qn('w:numId'), str(new_id))
    ref = OxmlElement('w:abstractNumId'); ref.set(qn('w:val'), str(abstract_id)); num.append(ref)
    if start_override:
        for ilvl in (0, 1):
            ov = OxmlElement('w:lvlOverride'); ov.set(qn('w:ilvl'), str(ilvl))
            so = OxmlElement('w:startOverride'); so.set(qn('w:val'), '1')
            ov.append(so); num.append(ov)
    numbering.append(num)
    return new_id


# 불릿 글리프 순환 (●/○/▪) — Symbol/Courier/Wingdings
_BULLET_GLYPHS = [("", "Symbol"), ("o", "Courier New"), ("", "Wingdings")]


def _ensure_bullet_num(doc) -> int | None:
    """자체 불릿 abstractNum(ilvl0~8) + num을 주입하고 numId를 반환한다."""
    numbering = _numbering_el(doc)
    if numbering is None:
        return None
    levels = []
    for ilvl in range(9):
        glyph, font = _BULLET_GLYPHS[ilvl % 3]
        levels.append({
            "ilvl": ilvl, "fmt": "bullet", "text": glyph, "font": font,
            "left": BULLET_LEFT0 + 720 * ilvl, "hanging": BULLET_HANGING,
        })
    aid = _add_abstract_num(numbering, levels)
    return _add_num(numbering, aid)


def _create_abc_num(doc) -> int | None:
    """자체 a·b·c용 abstractNum(ilvl0=decimal, ilvl1=lowerLetter) + num을 주입한다.
    ilvl0/1을 1부터 시작하도록 startOverride를 건다. numbering part 없으면 None."""
    numbering = _numbering_el(doc)
    if numbering is None:
        return None
    levels = [
        {"ilvl": 0, "fmt": "decimal", "text": "%1)", "font": None, "left": 720, "hanging": 360},
        {"ilvl": 1, "fmt": "lowerLetter", "text": "%2)", "font": None, "left": 1440, "hanging": 360},
    ]
    aid = _add_abstract_num(numbering, levels)
    return _add_num(numbering, aid, start_override=True)


# ---------------------------------------------------------------------------
# 표(table) 헬퍼
# ---------------------------------------------------------------------------

# 경쟁사 표 스타일 (템플릿에 존재하는 styleId, B와 동일)
TABLE_STYLE_ID = "af6"


def _parse_md_table(table_lines: list[str]) -> list[list[str]]:
    """마크다운 파이프 표 줄들을 [[셀,...], ...]로 파싱한다.
    구분선 행(|---|---|)은 제외한다."""
    rows: list[list[str]] = []
    for ln in table_lines:
        s = ln.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        # 구분선(|---|:--|) 스킵
        if re.fullmatch(r'\|[\s:\-|]+\|', s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        rows.append(cells)
    return rows


def _set_table_style(tbl, style_id: str):
    """표에 styleId 기준으로 표 스타일을 적용한다 (이름 접근 대신 OXML 직접)."""
    tblPr = tbl._tbl.tblPr
    for old in tblPr.findall(qn('w:tblStyle')):
        tblPr.remove(old)
    st = OxmlElement('w:tblStyle')
    st.set(qn('w:val'), style_id)
    tblPr.insert(0, st)  # tblStyle은 tblPr의 첫 자식


def _fill_cell(cell, text: str, bullet: bool = False):
    """셀을 채운다. <br>는 단락 분리, run은 맑은 고딕 9pt(볼드는 인라인 **만).
    bullet=True면 각 단락 앞에 "• " 접두를 붙인다."""
    # 셀의 기존 내용을 전부 제거한다.
    # 첫 단락의 run만 지우면, 여러 단락으로 된 템플릿 셀에서 두 번째 이후 단락이
    # 그대로 남아 새 내용 사이에 끼어든다. 예컨대 투자단가 셀의
    # "-액면가: ~ / -발행단가: ~"에 두 줄을 쓰면 옛 "-발행단가: ~"가 중간에 살아남는다.
    for para in cell.paragraphs[1:]:
        para._element.getparent().remove(para._element)
    p0 = cell.paragraphs[0]
    for r in list(p0.runs):
        r._element.getparent().remove(r._element)
    parts = text.split("<br>")
    for idx, part in enumerate(parts):
        p = cell.paragraphs[0] if idx == 0 else cell.add_paragraph()
        if bullet:
            run = p.add_run("• ")  # • 접두
            _apply_font(run, FONT_NAME, FONT_SIZE, FONT_COLOR)
        seg = re.split(r'(\*\*.*?\*\*)', part.strip())
        for s in seg:
            if s.startswith("**") and s.endswith("**") and len(s) >= 4:
                run = p.add_run(s[2:-2])
                _apply_font(run, FONT_NAME, FONT_SIZE, FONT_COLOR, bold=True)
            elif s:
                run = p.add_run(s)
                _apply_font(run, FONT_NAME, FONT_SIZE, FONT_COLOR)


def _add_table_after(doc, ref_element, rows: list[list[str]]):
    """rows로 docx 표를 만들어 ref_element 뒤에 삽입하고 표 요소를 반환한다."""
    ncols = max(len(r) for r in rows)
    tbl = doc.add_table(rows=len(rows), cols=ncols)  # 문서 끝에 임시 생성
    _set_table_style(tbl, TABLE_STYLE_ID)
    for ri, row in enumerate(rows):
        for ci in range(ncols):
            # 내용 열(마지막 열, 데이터 행)만 불릿 부여
            _fill_cell(tbl.cell(ri, ci), row[ci] if ci < len(row) else "",
                       bullet=(ri > 0 and ci == ncols - 1))
    _style_competitor_table(tbl)
    _merge_first_column(tbl)
    ref_element.addnext(tbl._tbl)  # 끝에서 떼어 ref 뒤로 이동


def _merge_first_column(tbl):
    """0열(구분)의 연속된 같은 값 데이터 행(헤더 제외)을 세로 병합한다."""
    n = len(tbl.rows)
    if n <= 2:
        return
    # 먼저 값/그룹을 읽어둔 뒤 병합(병합 중 인덱스 안정)
    groups = []
    ri = 1
    while ri < n:
        val = tbl.cell(ri, 0).text.strip()
        rj = ri
        while rj + 1 < n and tbl.cell(rj + 1, 0).text.strip() == val:
            rj += 1
        groups.append((ri, rj, val))
        ri = rj + 1
    for start, end, val in groups:
        if end <= start:
            continue
        merged = tbl.cell(start, 0).merge(tbl.cell(end, 0))
        # 병합으로 합쳐진 중복 단락 제거 후 단일 값으로 재작성
        tc = merged._tc
        for p in tc.findall(qn('w:p')):
            tc.remove(p)
        tc.append(OxmlElement('w:p'))
        _fill_cell(merged, val)
        merged.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    return tbl._tbl


# 경쟁자 표 양식 (템플릿 표준 표와 동일)
TABLE_HEADER_FILL = "BDD6EE"   # 헤더 행 음영 (연한 파랑)
CENTER_COLS = (0, 1)           # 가운데 정렬 열: 구분·기업명
# 3열 경쟁자 표 열 폭(dxa) — 목표 파일 B와 동일 (구분/기업명/내용)
COMP_COL_WIDTHS = (1605, 1785, 5235)

# CT_TblPr 자식 스키마 순서 (삽입 위치 계산용)
_TBLPR_ORDER = [
    'tblStyle', 'tblpPr', 'tblOverlap', 'bidiVisual', 'tblStyleRowBandSize',
    'tblStyleColBandSize', 'tblW', 'jc', 'tblCellSpacing', 'tblInd', 'tblBorders',
    'shd', 'tblLayout', 'tblCellMar', 'tblLook',
]


def _insert_tblPr_child(tblPr, el):
    """tblPr에 el을 스키마 순서에 맞는 위치로 삽입한다."""
    tag = el.tag.split('}')[-1]
    my = _TBLPR_ORDER.index(tag)
    for child in tblPr:
        ct = child.tag.split('}')[-1]
        if ct in _TBLPR_ORDER and _TBLPR_ORDER.index(ct) > my:
            child.addprevious(el)
            return
    tblPr.append(el)


def _set_competitor_widths(tbl, widths):
    """표를 고정 레이아웃으로 만들고 열 폭(dxa)을 widths로 지정한다."""
    total = sum(widths)
    tblPr = tbl._tbl.tblPr
    # tblW (dxa, 합계) + tblLayout fixed
    for old in tblPr.findall(qn('w:tblW')):
        tblPr.remove(old)
    tw = OxmlElement('w:tblW'); tw.set(qn('w:type'), 'dxa'); tw.set(qn('w:w'), str(total))
    _insert_tblPr_child(tblPr, tw)
    for old in tblPr.findall(qn('w:tblLayout')):
        tblPr.remove(old)
    tl = OxmlElement('w:tblLayout'); tl.set(qn('w:type'), 'fixed')
    _insert_tblPr_child(tblPr, tl)
    # tblGrid 열 폭
    grid = tbl._tbl.find(qn('w:tblGrid'))
    if grid is not None:
        for gc, w in zip(grid.findall(qn('w:gridCol')), widths):
            gc.set(qn('w:w'), str(w))
    # 각 셀 tcW (tcPr 맨 앞)
    for row in tbl.rows:
        for ci, cell in enumerate(row.cells):
            if ci >= len(widths):
                continue
            tcPr = cell._tc.get_or_add_tcPr()
            for old in tcPr.findall(qn('w:tcW')):
                tcPr.remove(old)
            tcw = OxmlElement('w:tcW'); tcw.set(qn('w:type'), 'dxa'); tcw.set(qn('w:w'), str(widths[ci]))
            tcPr.insert(0, tcw)  # tcW는 tcPr의 앞쪽 요소


def _style_competitor_table(tbl):
    """경쟁자 표에 테두리 + 헤더 음영 + 정렬 + 열 폭을 적용한다 (기존 표준 표 양식)."""
    # 0) 열 폭 고정 (3열 경쟁자 표만)
    if len(tbl.columns) == len(COMP_COL_WIDTHS):
        _set_competitor_widths(tbl, COMP_COL_WIDTHS)

    # 1) 테두리: 6변 single sz=8 #000000
    tblPr = tbl._tbl.tblPr
    for old in tblPr.findall(qn('w:tblBorders')):
        tblPr.remove(old)
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        e = OxmlElement('w:' + edge)
        e.set(qn('w:val'), 'single'); e.set(qn('w:sz'), '8')
        e.set(qn('w:space'), '0'); e.set(qn('w:color'), '000000')
        borders.append(e)
    _insert_tblPr_child(tblPr, borders)

    # 2a) 모든 셀: 세로 가운데(vAlign center)
    for row in tbl.rows:
        for cell in row.cells:
            tcPr = cell._tc.get_or_add_tcPr()
            for old in tcPr.findall(qn('w:vAlign')):
                tcPr.remove(old)
            va = OxmlElement('w:vAlign'); va.set(qn('w:val'), 'center')
            tcPr.append(va)

    # 2b) 헤더 행(0): 셀 음영 (스키마상 shd는 vAlign 앞)
    for cell in tbl.rows[0].cells:
        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn('w:shd')):
            tcPr.remove(old)
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), TABLE_HEADER_FILL)
        va = tcPr.find(qn('w:vAlign'))
        if va is not None:
            va.addprevious(shd)
        else:
            tcPr.append(shd)

    # 3) 가운데 정렬: 헤더 행 전체 + 구분·기업명 열(모든 행)
    for ri, row in enumerate(tbl.rows):
        for ci, cell in enumerate(row.cells):
            if ri == 0 or ci in CENTER_COLS:
                for p in cell.paragraphs:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


# ---------------------------------------------------------------------------
# 일반 헬퍼
# ---------------------------------------------------------------------------

def _clear_paragraph(paragraph):
    """단락의 모든 run을 비운다."""
    for run in paragraph.runs:
        run.text = ""


def _new_paragraph_after(doc, ref_element):
    """ref_element 뒤에 새 단락을 삽입하고 반환한다."""
    new_element = ref_element.makeelement(qn('w:p'), {})
    ref_element.addnext(new_element)
    return Paragraph(new_element, doc.part)


def _get_or_create(doc, first_paragraph, current_element, is_first):
    """첫 줄이면 기존 단락 사용, 아니면 새 단락 생성."""
    if is_first:
        return first_paragraph
    return _new_paragraph_after(doc, current_element)


def _apply_font(run, font_name: str, size, color, bold: bool = False, italic: bool = False):
    """run에 폰트 스타일을 적용한다."""
    run.font.name = font_name
    run.font.size = size
    run.font.color.rgb = color
    run.bold = bold
    run.italic = italic
    # 한글 폰트 설정 (eastAsia)
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = rPr.makeelement(qn('w:rFonts'), {})
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:eastAsia'), font_name)


def _add_formatted_runs(paragraph, text: str):
    """인라인 볼드(**텍스트**) 등을 처리하여 스타일 적용된 run을 추가한다."""
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            run = paragraph.add_run(part[2:-2])
            _apply_font(run, FONT_NAME, FONT_SIZE, FONT_COLOR, bold=True)
        elif part:
            run = paragraph.add_run(part)
            _apply_font(run, FONT_NAME, FONT_SIZE, FONT_COLOR)
