"""
투자심사보고서 자동화 MCP 서버

Claude Desktop에서 사용하는 MCP 서버.
지정된 폴더의 파일을 읽어 투심보고서 초안 markdown을 생성한다.
"""

import json
import sys
from difflib import get_close_matches
from pathlib import Path

# server.py가 어느 경로에서 실행되더라도 tools/ 패키지를 찾을 수 있도록
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.fastmcp.utilities.types import Image  # noqa: E402

from tools.classify import classify_file, format_report  # noqa: E402
from tools.document import read_document_text, has_images, save_extracted_images  # noqa: E402
from tools.style_check import check as style_check  # noqa: E402
from tools.style_check import format_result as format_style_result  # noqa: E402
from tools.writer import write_markdown  # noqa: E402
from tools.writer_docx import write_docx  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# read_document_text가 다룰 수 있는 형식만 인풋으로 본다.
# 이걸 걸러내지 않으면 .zip·.json·.py 같은 부산물이 '역할 불명'으로 쌓인다.
SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".docx", ".xlsx", ".txt", ".md"}

# 자료 추출용 프롬프트. 본문 서술 스타일 가이드를 주입하지 않는다.
EXTRACTION_PROMPT_PREFIXES = ("20_", "21_", "22_")

mcp = FastMCP("investment-memo")

# scan_inputs에서 설정, get_prompt에서 참조
_last_scanned_folder: str | None = None
# read_file에서 저장한 이미지 캐시 (동일 파일 재파싱 방지)
_saved_images_cache: dict[str, list[dict]] = {}
# describe_image로 저장된 이미지 설명 (get_prompt에서 사용)
_image_descriptions: dict[str, str] = {}
# 이미지 목록 문자열 캐시 (get_prompt에서 재사용)
_images_info_cache: str | None = None
# save_section으로 확정 누적되는 섹션 내용 (create_report에서 최종 생성)
_report_sections: dict[str, str] = {}
# save_tables로 누적되는 표 데이터 (create_report에서 docx 표 채우기에 사용)
_report_tables: dict = {}
# save_section에서 기억하는 회사명·폴더 (create_report 폴백용)
_report_company: str | None = None
_report_folder: str | None = None


def _valid_section_keys() -> set[str]:
    """SECTION_STRUCTURE에서 유효한 sections key 집합을 구한다."""
    from tools.writer import SECTION_STRUCTURE
    return {entry[2] for entry in SECTION_STRUCTURE if entry[2] is not None}


def _valid_table_keys() -> set[str]:
    """TABLE_SPECS에서 유효한 tables key 집합을 구한다."""
    from tools.tables_fill import TABLE_SPECS
    return {spec["key"] for spec in TABLE_SPECS}


@mcp.tool()
def scan_inputs(folder_path: str) -> str:
    """
    지정된 폴더를 스캔하여 파일 목록을 반환한다.

    [투심보고서 작성 워크플로우 — 반드시 이 순서대로]
    1. scan_inputs(폴더경로) 호출 → 파일 목록 확인
    2. read_file(파일경로) 호출 → 각 파일의 텍스트 읽기 + 이미지 자동 저장
       - 이미지가 포함된 문서는 images/에 자동 저장되며, 경로 목록이 반환됨
    2b. 저장된 이미지마다 read_image로 확인 → describe_image(경로, 한줄설명) 호출
       - 예: describe_image("images/img_3.png", "시장 규모 TAM $50B 차트")
       - 장식용 이미지(로고, 배경, 아이콘)는 describe_image("images/img_1.png", "스킵")
       - 설명이 서버에 저장되어 get_prompt에서 자동 포함됨
       - IR 덱은 25~40페이지라 전부 열면 컨텍스트가 남지 않는다.
         get_prompt("21_IR덱읽기")로 페이지 선별 기준을 먼저 확인할 것
       ※ 이미지 설명 과정은 사용자에게 출력하지 말 것
    2c. [노션 미팅록 확보 — 표·본문 작성 전에 반드시 수행]
       get_prompt("22_노션연동")으로 지침을 로드한 뒤, 노션 스타트업 DB에서
       해당 회사 페이지를 찾아 미팅록과 DB 속성을 읽는다.
       - 투심보고서의 핵심 근거 상당수(고객사 실명, 미팅에서 나온 수치,
         레퍼런스 콜 결론, 투자자 우려와 해소)는 IR 덱에도 웹에도 없고
         여기에만 있다. 건너뛰면 IR 덱 요약본이 나온다
       - 회사정보 표의 담당자·발굴경위도 이 페이지의 DB 속성에서 나온다

    2d. [표 데이터 추출 — 본문 작성 전에 반드시 수행]
       get_prompt("20_표추출")로 지침을 로드한 뒤 save_tables(tables) 호출.
       보고서의 표 8개는 본문 섹션과 별개 경로이며, 이 단계를 건너뛰면 표가
       전부 빈 채로 출력된다.
       - 지침에 출처 우선순위(설립일은 등기부등본 회사성립연월일 등), 표별 명세,
         저장 전 자기 점검 항목이 들어 있다. 지침 없이 추측으로 채우지 말 것
       - 등기부등본·주주명부·사업자등록증은 대부분 **스캔 PDF**라 read_file이
         [스캔 문서] 배너를 반환한다. 이 경우 read_image로 페이지 이미지를
         직접 보기 전까지 값을 쓰지 말 것
       ※ 추출 과정은 사용자에게 출력하지 말 것
    3. [작성 개요 제시 및 합의 — 본격 작성 전 필수] 전체 본문을 작성하기 전에,
       investment theme의 내용을 핵심 섹션별로 어떻게 해체해 넣을지 전반적인 개요
       (어느 섹션에 어떤 핵심 내용이 들어갈지)를 사용자에게 먼저 제시할 것.
       - 이 단계에서는 전체 본문을 작성하지 말고, 섹션별로 담길 내용의 개요만 제시함.
       - 사용자와 대화하며 내용 구성을 조정하고, 구성이 확정(사용자 승인)된 뒤에만
         다음 단계(4. 섹션별 상세 작성)로 진행함.
    4. 섹션별 순차 작성 + 핑퐁 — 절대 프롬프트를 한꺼번에 로드하지 말 것.
       반드시 아래 a~f를 섹션마다 1회씩 반복:
       a) "📝 [섹션명] 작성 시작" 출력
       b) get_prompt(섹션명) 호출 → 해당 섹션 작성 지침 1개만 로드
       c) 지침에 따라 해당 섹션 내용 작성
          - 반드시 웹 검색(web search)을 활용하여 부족한 정보를 보완할 것
          - 이미지 설명을 참고하여 필요한 이미지가 있으면 read_image로 개별 로드하여 내용 분석 후 반영
       d) [핑퐁] 작성한 내용을 사용자에게 제시하고 함께 수정함. 사용자가 그 섹션을 확정할 때까지 대화로 다듬을 것.
       e) [확정 저장] 사용자가 확정하면 save_section(섹션명, 확정내용, 회사명, 폴더경로) 호출
          → 작업용 md 파일이 즉시 갱신되고 진행률이 반환됨.
       f) "✅ [섹션명] 작성 완료" 출력 후 다음 섹션으로 이동
       ※ 프롬프트가 없는 섹션은 수기 작성 섹션이므로 내용을 생성하지 말 것(save_section도 호출 안 함). '[작성 필요]'로 남음.
    5. 모든 섹션을 핑퐁·확정·저장한 뒤, 전체 md를 한 번 더 점검(섹션 간 일관성·중복·흐름)하여 최종 정리.
    6. create_report(회사명, 폴더경로) 호출 → 누적된 섹션 + 표로 최종 markdown + Word(docx) 생성.
       - 누적 저장된 내용을 그대로 쓰므로 sections/tables 인자는 생략 가능
         (생략 시 save_section·save_tables 누적분 사용).
       - section_images: 섹션별 삽입 이미지 dict (선택). 예: {"서비스 상세": [{"file": "images/xxx.png", "caption": "설명"}]}
       - 반환값에 "표 데이터가 없어 ... 비어 있음" 경고가 있으면 2d를 건너뛴 것이므로
         save_tables를 호출한 뒤 다시 생성할 것

    [프롬프트 파일명 목록 — get_prompt 호출 시 아래 순서대로 사용할 것]
    05a_문제정의, 05b_가설, 06a_서비스상세, 06b_지표,
    07_BM, 08_향후전략및계획, 09_financial_projection, 10_시장현황및전망,
    11_주요경쟁자, 12_경쟁우위평가, 13_investment_analysis,
    01_투자포인트 (※ 전체 종합이므로 반드시 마지막에 작성)

    Args:
        folder_path: 스캔할 폴더의 절대경로

    Returns:
        폴더 내 파일 리스트 (포맷된 문자열)

    Raises:
        ValueError: 폴더가 비어있거나 존재하지 않을 때
    """
    folder = Path(folder_path).resolve()

    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"유효하지 않은 폴더: {folder}")

    def _is_input(p) -> bool:
        if not p.is_file():
            return False
        parts = p.relative_to(folder).parts
        # 점으로 시작하는 디렉터리(.bkit, .omc, .git) 안의 파일도 제외해야 한다.
        # p.name.startswith(".")만 보면 '.bkit/audit/2026-05-29.jsonl'이 통과한다.
        if any(part.startswith(".") for part in parts):
            return False
        if "images" in parts:
            return False
        return p.suffix.lower() in SUPPORTED_SUFFIXES

    files = sorted(p for p in folder.rglob("*") if _is_input(p))
    skipped = sum(
        1
        for p in folder.rglob("*")
        if p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(folder).parts)
        and "images" not in p.relative_to(folder).parts
        and p.suffix.lower() not in SUPPORTED_SUFFIXES
    )

    if not files:
        raise ValueError(
            f"폴더에 읽을 수 있는 문서가 없음: {folder}\n"
            f"지원 형식: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    global _last_scanned_folder
    global _images_info_cache
    global _report_company, _report_folder
    if str(folder) != _last_scanned_folder:
        _saved_images_cache.clear()
        _image_descriptions.clear()
        _images_info_cache = None
        _report_sections.clear()
        _report_tables.clear()
        _report_company = None
        _report_folder = None
    _last_scanned_folder = str(folder)

    # 파일명만으로 역할을 판정한다. 내용 시그니처는 read_file에서 텍스트를
    # 읽은 뒤에야 쓸 수 있으므로, 여기서는 1차 추정만 제공한다.
    results = []
    for f in files:
        info = classify_file(str(f))
        info["name"] = f.name
        results.append(info)

    lines = [f"📂 폴더 스캔 결과: {folder_path}", ""]
    lines.append(format_report(results))
    lines.append("")
    usable = sum(1 for r in results if not r["excluded"])
    tail = f"총 {len(files)}개 중 인풋 {usable}개"
    if skipped:
        tail += f" (지원하지 않는 형식 {skipped}개는 목록에서 제외)"
    lines.append(tail)
    lines.append(
        "※ 역할은 파일명 기준 1차 추정임. read_file로 내용을 읽으면 더 정확해지며, "
        "'추정' 표시가 붙은 것과 역할 불명은 내용을 보고 직접 판단할 것."
    )
    return "\n".join(lines)


@mcp.tool()
def read_file(file_path: str) -> str:
    """
    단일 파일(PDF, PPTX, DOCX, XLSX, TXT 등)을 읽어 텍스트를 반환한다.

    이미지가 포함된 문서의 경우, 텍스트 반환과 동시에 images/에 자동 저장된다.
    저장된 이미지의 경로·소스·컨텍스트 정보가 함께 반환되므로,
    컨텍스트를 참고하여 필요한 이미지만 read_image로 확인할 것.

    Args:
        file_path: 읽을 파일의 절대경로

    Returns:
        추출된 텍스트 내용 (이미지 포함 시 이미지 수 안내)

    Raises:
        ValueError: 지원하지 않는 파일 형식
        RuntimeError: 텍스트 추출 실패 (이미지 PDF 등)
    """
    global _images_info_cache
    text = read_document_text(file_path)

    # 이미지가 포함될 수 있는 포맷이고 스캔된 폴더가 있으면 자동 저장
    if has_images(file_path) and _last_scanned_folder:
        saved = save_extracted_images(file_path, _last_scanned_folder)
        if saved:
            _saved_images_cache[file_path] = saved
            _images_info_cache = None  # 새 이미지 저장 시 캐시 무효화
            text += f"\n\n📷 {len(saved)}개 이미지가 images/에 자동 저장됨."
            for s in saved:
                ctx = f" — {s['context']}" if s.get('context') else ""
                text += f"\n  - {s['path']} ({s['source']}{ctx})"
    elif has_images(file_path):
        text += f"\n\n📷 이미지가 포함된 문서임. scan_inputs를 먼저 호출하면 read_file 시 이미지가 자동 저장됨."
    return text


@mcp.tool()
def save_section(
    section_name: str,
    content: str,
    company_name: str,
    folder_path: str,
) -> str:
    """
    핑퐁으로 확정된 섹션 내용을 저장하고 작업용 markdown을 즉시 갱신한다.

    섹션을 하나 확정할 때마다 호출한다. 누적된 내용은 이후 create_report에서
    최종 md + docx 생성에 사용된다. 동일 섹션을 다시 호출하면 내용이 갱신된다.

    Args:
        section_name: 섹션 key (예: "BM", "향후 전략 및 계획", "Investment Analysis")
        content: 사용자와 확정한 해당 섹션의 최종 내용 (markdown)
        company_name: 투자 대상 회사명 (파일명에 사용됨)
        folder_path: 보고서를 저장할 폴더의 절대경로

    Returns:
        저장 확인 + 진행률 (채워진/남은 섹션)

    Raises:
        ValueError: 유효하지 않은 섹션 key
    """
    global _report_company, _report_folder

    valid_keys = _valid_section_keys()
    if section_name not in valid_keys:
        close = get_close_matches(section_name, valid_keys, n=1, cutoff=0.5)
        hint = f" (혹시 '{close[0]}'?)" if close else ""
        raise ValueError(
            f"유효하지 않은 섹션 key: '{section_name}'{hint}\n"
            f"사용 가능: {', '.join(sorted(valid_keys))}"
        )

    _report_sections[section_name] = content
    _report_company = company_name
    _report_folder = folder_path

    # 작업용 md 즉시 갱신 (단일 파일, 누적분 렌더)
    md_path = write_markdown(_report_sections, company_name, folder_path)

    filled = {k for k in valid_keys if str(_report_sections.get(k, "")).strip()}
    remaining = sorted(valid_keys - filled)
    lines = [
        f"✅ 섹션 저장: {section_name}",
        f"📄 작업용 md 갱신됨: {md_path}",
        f"진행률: {len(filled)}/{len(valid_keys)} 섹션 작성됨",
    ]

    if remaining:
        lines.append(f"남은 섹션: {', '.join(remaining)}")
    else:
        lines.append("모든 섹션 작성 완료 → create_report(회사명, 폴더경로)로 최종 생성 가능")

    # 하우스 보이스 검사. 저장은 막지 않되 위반을 즉시 알려 그 자리에서 고치게 한다.
    # 구버전 파이프라인은 완성본보다 1.66배 길게 써서 생성 문단의 71%가 버려졌다.
    style_report = format_style_result(style_check(content, section_name), section_name)
    if style_report:
        lines.append("")
        lines.append(style_report)

    return "\n".join(lines)


@mcp.tool()
def save_tables(tables: dict | str) -> str:
    """
    소스 문서에서 추출한 표 데이터를 저장한다. create_report에서 docx 표를 채운다.

    등기부등본·주주명부·사업자등록증은 대부분 스캔 PDF라 텍스트가 추출되지 않는다.
    read_file이 [스캔 문서] 배너를 반환하면 read_image로 해당 이미지를 직접 보고
    값을 읽어낸 뒤 이 도구로 저장할 것.

    표 종류별 자료형:
      회사정보, 투자조건        → {"라벨": "값"} dict
      회사히스토리, 핵심인력,    → [[셀, 셀, ...], ...] 행 리스트
      투자내역, 주요경쟁자,
      지식재산권, 회수방안

    추출 규칙:
      - 설립일은 사업자등록증의 개업연월일이 아니라 **등기부등본의 회사성립연월일**을 쓴다
      - 핵심인력은 등기부등본 임원란이 아니라 IR 덱·Investment Theme에서 뽑는다
        (등기부에는 감사가 들어가고 정작 CTO 같은 비등기 핵심 인물이 빠진다)
      - 값을 못 찾으면 지어내지 말고 "(미확인 — 필요한 자료)" 형식으로 남긴다
      - 셀 안 줄바꿈은 <br>로 표기한다

    Args:
        tables: 표 key → 데이터 dict 또는 JSON 문자열

    Returns:
        저장 확인 + 표별 항목 수

    Raises:
        ValueError: 유효하지 않은 표 key
    """
    if isinstance(tables, str):
        tables = json.loads(tables)

    valid = _valid_table_keys()
    unknown = set(tables) - valid
    if unknown:
        hints = []
        for key in sorted(unknown):
            close = get_close_matches(key, valid, n=1, cutoff=0.5)
            hints.append(f"'{key}'" + (f" (혹시 '{close[0]}'?)" if close else ""))
        raise ValueError(
            f"유효하지 않은 표 key: {', '.join(hints)}\n"
            f"사용 가능: {', '.join(sorted(valid))}"
        )

    _report_tables.update(tables)

    lines = ["✅ 표 데이터 저장됨"]
    for key in sorted(_report_tables):
        payload = _report_tables[key]
        size = f"{len(payload)}개 항목" if isinstance(payload, dict) else f"{len(payload)}행"
        lines.append(f"  - {key}: {size}")
    missing = sorted(valid - set(_report_tables))
    if missing:
        lines.append(f"미저장 표: {', '.join(missing)}")
    return "\n".join(lines)


@mcp.tool()
def create_report(
    company_name: str | None = None,
    folder_path: str | None = None,
    sections: dict | str | None = None,
    section_images: dict | str | None = None,
    tables: dict | str | None = None,
) -> str:
    """
    투심보고서 최종본(markdown + Word docx)을 생성한다.

    save_section으로 누적된 섹션을 사용하는 것이 기본이며(sections 생략),
    sections를 명시하면 그 내용으로 일회성 생성도 가능하다.
    sections에 없는 섹션은 '[작성 필요]' 플레이스홀더로 채운다.

    Args:
        company_name: 회사명 (생략 시 save_section에서 저장된 값 사용)
        folder_path: 저장 폴더 절대경로 (생략 시 save_section에서 저장된 값 사용)
        sections: 섹션명 → 내용 dict 또는 JSON 문자열 (생략 시 save_section 누적분 사용).
                  지원 키: 투자 포인트, 문제정의, 가설, 서비스 상세, 지표, BM,
                           향후 전략 및 계획, Financial Projection, 시장 현황 및 전망,
                           주요 경쟁자, 경쟁우위 평가, Investment Analysis
        section_images: 섹션별 삽입 이미지 dict 또는 JSON 문자열 (선택).
                        예: {"서비스 상세": [{"file": "images/xxx.png", "caption": "대시보드 스크린샷"}]}

    Returns:
        생성된 markdown(.md)과 Word(.docx) 파일의 절대경로 (두 파일 모두 생성됨)

    Raises:
        ValueError: 사용할 섹션/회사명/폴더가 없을 때
        RuntimeError: 보고서 생성 실패
    """
    if isinstance(sections, str):
        sections = json.loads(sections)
    if isinstance(section_images, str):
        section_images = json.loads(section_images)
    if isinstance(tables, str):
        tables = json.loads(tables)

    if not sections:
        sections = dict(_report_sections)
    if not tables:
        tables = dict(_report_tables)
    company_name = company_name or _report_company
    folder_path = folder_path or _report_folder

    # 표만 채운 중간 산출물도 정당한 결과다. 표 추출(2c)은 본문 작성보다
    # 먼저 끝나므로, 섹션이 비었다는 이유로 생성을 막으면 안 된다.
    if not sections and not tables:
        raise ValueError(
            "생성할 내용이 없음. save_section(본문) 또는 save_tables(표)로 "
            "먼저 저장하거나 sections/tables를 전달할 것."
        )
    if not company_name or not folder_path:
        raise ValueError("company_name과 folder_path가 필요함 (save_section 호출 또는 인자 전달).")

    md_path = write_markdown(sections, company_name, folder_path, section_images)
    build_warnings: list[str] = []
    docx_path = write_docx(
        sections, company_name, folder_path, section_images,
        tables=tables, warnings=build_warnings,
    )

    note = ""
    if not tables:
        note = (
            "\n⚠️ 표 데이터가 없어 docx의 표 8개가 비어 있음. "
            "save_tables로 저장한 뒤 다시 생성할 것."
        )
    if build_warnings:
        # 코드가 자동으로 못 고치는 것들이다. 로그로만 남기면 아무도 안 보므로
        # 반환값에 실어 사용자에게 그대로 전달한다.
        note += "\n\n⚠️ 사람이 확인해야 하는 항목:\n" + "\n".join(
            f"  - {w}" for w in build_warnings
        )
    return f"markdown: {md_path}\ndocx: {docx_path}{note}"


@mcp.tool()
def read_image(file_path: str):
    """
    저장된 이미지 파일 1개를 읽어 반환한다.

    read_file에서 자동 저장된 이미지를 섹션 작성 시 개별적으로 확인할 때 사용한다.
    이미지 내용(차트, 도표, 다이어그램 등)을 분석하여 보고서 작성에 반영할 수 있다.

    Args:
        file_path: 이미지 파일의 절대경로

    Returns:
        이미지 내용 (Claude가 시각적으로 분석 가능)
    """
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"이미지 파일을 찾을 수 없음: {path}")

    blob = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    return Image(data=blob, format=ext)


@mcp.tool()
def describe_image(image_path: str, description: str) -> str:
    """
    이미지에 대한 설명을 서버에 저장한다.

    read_image로 이미지를 확인한 후, 한 줄 설명을 저장하면
    이후 get_prompt 호출 시 이미지 목록에 설명이 자동 포함된다.
    장식용 이미지(로고, 배경)는 "스킵"으로 저장할 것.

    Args:
        image_path: 이미지 파일의 상대경로 (예: "images/img_1.png")
        description: 이미지 한 줄 설명 (예: "시장 규모 TAM $50B 차트")

    Returns:
        저장 확인 메시지
    """
    global _images_info_cache
    _image_descriptions[image_path] = description
    _images_info_cache = None  # 설명 변경 시 이미지 목록 캐시 무효화
    return f"✅ 설명 저장: {image_path} — {description}"


def _build_images_info() -> str:
    """사용 가능한 이미지 목록 문자열을 생성한다."""
    if not _last_scanned_folder:
        return ""

    cached_images: dict[str, dict] = {}
    for file_results in _saved_images_cache.values():
        for r in file_results:
            cached_images[r["path"]] = r

    images_dir = Path(_last_scanned_folder) / "images"
    if not images_dir.exists():
        return ""

    img_files = sorted(images_dir.glob("*"))
    if not img_files:
        return ""

    img_lines = ["\n📷 사용 가능한 이미지 (images/ 디렉토리):"]
    for img_file in img_files:
        if img_file.suffix.lower() in (".png", ".jpeg", ".jpg"):
            rel_path = f"images/{img_file.name}"
            desc = _image_descriptions.get(rel_path)
            cached = cached_images.get(rel_path)
            if desc:
                img_lines.append(f"- {rel_path} — {desc}")
            elif cached and cached.get("context"):
                img_lines.append(f"- {rel_path} ({cached['source']} — {cached['context']})")
            elif cached:
                img_lines.append(f"- {rel_path} ({cached['source']})")
            else:
                img_lines.append(f"- {rel_path}")
    img_lines.append("")
    img_lines.append("→ 섹션 내용에 적합한 이미지를 read_image로 확인 후 ![캡션](images/파일명) 형태로 삽입할 것")
    return "\n".join(img_lines)


@mcp.tool()
def get_prompt(section_name: str) -> str:
    """
    섹션별 작성 프롬프트를 조회한다.
    보고서 각 섹션 작성 전에 반드시 이 툴을 호출하여 작성 지침을 확인할 것.

    Args:
        section_name: 프롬프트 파일명 (확장자 제외)
                      예: "01_투자포인트", "05a_문제정의", "07_BM"

    Returns:
        공통 스타일 가이드 + 해당 섹션의 작성 프롬프트 내용
    """
    # 공통 스타일 가이드 로드.
    # 00_공통스타일.md는 본문 서술용(볼드 헤드라인·불릿·어투) 규칙이라
    # 추출 계열 프롬프트에는 해롭다. 20_·21_ 접두에는 주입하지 않는다.
    style_content = ""
    if not section_name.startswith(EXTRACTION_PROMPT_PREFIXES):
        style_path = PROMPTS_DIR / "00_공통스타일.md"
        if style_path.exists():
            style_content = style_path.read_text(encoding="utf-8")

    # 섹션 프롬프트 로드
    section_path = PROMPTS_DIR / f"{section_name}.md"
    if not section_path.exists():
        available = [f.stem for f in sorted(PROMPTS_DIR.glob("*.md")) if f.name != "00_공통스타일.md"]
        raise ValueError(
            f"프롬프트 '{section_name}'을 찾을 수 없음.\n"
            f"사용 가능: {', '.join(available)}"
        )

    section_content = section_path.read_text(encoding="utf-8")

    # 사용 가능한 이미지 목록 (캐시 활용)
    global _images_info_cache
    if _images_info_cache is None:
        _images_info_cache = _build_images_info()
    images_info = _images_info_cache

    header = f"📝 [{section_name}] 프롬프트 로드됨\n{'=' * 60}\n\n"
    style_block = ""
    if style_content:
        style_block = (
            f"## 공통 스타일 가이드\n\n{style_content}\n\n{'=' * 60}\n\n"
        )

    return f"{header}{style_block}## 작성 지침\n\n{section_content}{images_info}"


if __name__ == "__main__":
    mcp.run()
