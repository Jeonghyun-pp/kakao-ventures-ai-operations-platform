import logging
import re
from datetime import date
from difflib import get_close_matches
from pathlib import Path

logger = logging.getLogger(__name__)

PLACEHOLDER = "[작성 필요]"

# (heading_level, title, sections_key_or_None, skip_body)
# sections_key_or_None: str → sections dict에서 읽음 / None → 빈 템플릿
# skip_body: True → 제목만 출력 (하위 항목이 내용을 담당)
SECTION_STRUCTURE = [
    # 1. Executive Summary
    (1, "Executive Summary", None, True),
    (2, "1) 투자 대상 회사", None, False),
    (2, "2) 투자 조건", None, False),
    (2, "3) 투자 포인트", "투자 포인트", False),
    (2, "4) 투자 전후 지분 구조", None, False),

    # 2. Company Overview & 향후 계획
    (1, "Company Overview & 향후 계획", None, True),
    (2, "1) 회사 히스토리", None, False),
    (2, "2) 핵심 인력", None, False),
    (2, "3) 투자내역 및 채무 현황", None, False),
    (2, "4) 재무 현황", None, False),
    (2, "5) 회사의 문제정의 및 가설", None, True),
    (3, "a) 문제정의", "문제정의", False),
    (3, "b) 가설", "가설", False),
    (2, "6) 현황 및 지표", None, True),
    (3, "a) 서비스 상세", "서비스 상세", False),
    (3, "b) 지표", "지표", False),
    (2, "7) BM", "BM", False),
    (2, "8) 향후 전략 및 계획", "향후 전략 및 계획", False),
    (3, "a) Financial Projection", "Financial Projection", False),

    # 3. Market/Industry Landscape
    (1, "Market/Industry Landscape", None, True),
    (2, "1) 시장 현황 및 전망", "시장 현황 및 전망", False),
    (2, "2) 주요 경쟁자", "주요 경쟁자", False),
    (2, "3) 경쟁우위 평가", "경쟁우위 평가", False),

    # 4. Investment Analysis
    (1, "Investment Analysis (회수 방안 및 투자 수익성 분석)", "Investment Analysis", False),
]


HEADING_PREFIX = {1: "#", 2: "##", 3: "###"}


def write_markdown(
    sections: dict,
    company_name: str,
    folder_path: str,
    section_images: dict | None = None,
) -> str:
    output_dir = Path(folder_path).resolve()

    today = date.today().strftime("%Y-%m-%d")
    safe_name = re.sub(r'[^\w\-]', '_', company_name)
    filename = f"투심보고서_{safe_name}_{today}.md"
    output_path = output_dir / filename

    # 섹션 key validation — 유효하지 않은 key 경고
    valid_keys = {entry[2] for entry in SECTION_STRUCTURE if entry[2] is not None}
    warnings: list[str] = []
    for key in sections:
        if key not in valid_keys:
            close = get_close_matches(key, valid_keys, n=1, cutoff=0.6)
            hint = f" (혹시 '{close[0]}'?)" if close else ""
            warnings.append(f"⚠️ 알 수 없는 섹션 key: '{key}'{hint}")
            logger.warning("알 수 없는 섹션 key: '%s'%s", key, hint)

    lines: list[str] = []
    used_images: set[str] = set()

    if warnings:
        lines.append("<!-- 경고: " + " | ".join(warnings) + " -->")
        lines.append("")

    for level, title, key, skip_body in SECTION_STRUCTURE:
        prefix = HEADING_PREFIX[level]
        lines.append(f"{prefix} {title}")
        lines.append("")

        if skip_body:
            continue

        if key is None:
            lines.append(f"> {PLACEHOLDER}")
            lines.append("")
        else:
            content = sections.get(key, "")
            if not content or not str(content).strip():
                lines.append(f"> {PLACEHOLDER}")
                lines.append("")
            else:
                # 본문 내 인라인 이미지 중복 제거
                content_lines = str(content).strip().split("\n")
                filtered = []
                for cl in content_lines:
                    img_matches = re.findall(r'!\[.*?\]\((.*?)\)', cl)
                    if img_matches:
                        skip_line = False
                        for img_path in img_matches:
                            if key == "투자 포인트" or img_path in used_images:
                                skip_line = True
                                break
                            used_images.add(img_path)
                            # 상대경로 → 절대경로 변환
                            abs_path = str((output_dir / img_path).resolve())
                            cl = cl.replace(img_path, abs_path)
                        if skip_line:
                            continue
                    filtered.append(cl)
                lines.append("\n".join(filtered))
                lines.append("")

        # 섹션에 매핑된 이미지 삽입 (투자 포인트는 이미지 삽입 제외)
        if section_images and key and key in section_images and key != "투자 포인트":
            for img_ref in section_images[key]:
                caption = img_ref.get("caption", "")
                file_path = img_ref.get("file", "")
                if file_path and file_path not in used_images:
                    used_images.add(file_path)
                    abs_file_path = str((output_dir / file_path).resolve())
                    lines.append(f"![{caption}]({abs_file_path})")
                    if caption:
                        lines.append(f"*{caption}*")
                    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return str(output_path.resolve())
