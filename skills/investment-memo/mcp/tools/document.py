import logging
import re
from io import BytesIO
from pathlib import Path

# Image는 server.py의 read_image tool에서만 사용

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB per image (base64 인코딩 후 ~1.3MB)
MAX_IMAGES_PER_DOC = 50
MAX_IMAGE_DIMENSION = 1568  # Claude vision 최적 해상도

# 페이지당 이 글자 수 미만이면 "텍스트 없음"으로 간주한다.
# 스캔 PDF(등기부등본·주주명부·사업자등록증)는 페이지당 0~20자만 잡히므로
# 이 값을 넘지 못하면 vision으로 읽어야 한다.
MIN_CHARS_PER_PAGE = 20

# 빈 페이지 비율이 이 값 이상이면 문서 전체를 스캔본으로 본다.
SCAN_RATIO_THRESHOLD = 0.5

SCAN_BANNER = (
    "[스캔 문서] 텍스트 레이어 없음 ({empty}/{total} 페이지 추출 실패). "
    "이 문서는 반드시 이미지(vision)로 읽을 것. "
    "아래 텍스트만으로 내용을 판단하지 말 것."
)

PARTIAL_SCAN_BANNER = (
    "[일부 페이지 이미지] {total}페이지 중 {empty}페이지에 텍스트가 없음 "
    "(페이지 헤더에 '텍스트 없음'으로 표시됨). "
    "해당 페이지 내용이 필요하면 이미지(vision)로 확인할 것."
)


def diagnose_broken_ooxml(path: Path) -> str:
    """열리지 않는 OOXML(docx/xlsx/pptx)의 원인을 사람이 읽을 수 있게 진단한다.

    "폴더에 문서를 넣으면 알아서" 파이프라인에는 깨진 파일이 반드시 섞여 들어온다.
    'Package not found' 같은 메시지로는 사용자가 조치할 수 없으므로 원인을 특정한다.
    """
    try:
        head = path.open("rb").read(8)
        size = path.stat().st_size
    except OSError as e:
        return f"파일을 읽을 수 없음 ({e})"

    if size < 1024 and not head.startswith(b"PK"):
        return (
            f"실제 문서가 아니라 Word 임시/잠금 파일로 보임 ({size}바이트). "
            "원본 문서를 다시 받을 것"
        )

    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return (
            "확장자는 OOXML인데 실제로는 레거시 형식(.doc/.xls/.ppt)임. "
            "해당 앱에서 최신 형식으로 저장할 것"
        )

    if head.startswith(b"PK"):
        # zip 헤더는 맞지만 끝의 중앙 디렉터리(EOCD)가 없으면 잘린 파일이다
        try:
            with path.open("rb") as fh:
                fh.seek(max(0, size - 66_000))
                if fh.read().rfind(b"PK\x05\x06") == -1:
                    return (
                        f"파일이 잘려 있음 (크기 {size:,}바이트, 압축 종료 표시 없음). "
                        "다운로드가 완료되지 않았을 가능성이 큼. 다시 받을 것"
                    )
        except OSError:
            pass
        return "압축 구조가 손상됨. 원본을 다시 받을 것"

    return f"알 수 없는 형식 (선두 바이트 {head[:4]!r}, {size:,}바이트)"


def _read_xlsx(path: Path) -> str:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
        sheets = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(cell) if cell is not None else "" for cell in row]
                if cells:
                    rows.append("\t".join(cells))
            sheets.append(f"--- Sheet: {sheet_name} ---\n" + "\n".join(rows))
        result = "\n\n".join(sheets)
    except Exception as e:
        raise RuntimeError(
            f"XLSX를 읽을 수 없음: {path.name} — {diagnose_broken_ooxml(path)}"
        ) from e

    if not result.strip():
        raise RuntimeError(f"No text extracted from XLSX: {path}")
    return result


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp949", "latin-1"):
        try:
            result = path.read_text(encoding=encoding)
            if result.strip():
                return result
        except (UnicodeDecodeError, ValueError):
            continue
        except Exception as e:
            raise RuntimeError(f"Failed to read text file {path}: {e}") from e

    raise RuntimeError(f"No text extracted from file: {path}")


def _convert_emf_to_png(raw_bytes: bytes) -> bytes | None:
    """EMF/WMF 바이트를 LibreOffice로 PNG 변환한다."""
    import subprocess
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            emf_path = Path(tmpdir) / "temp.emf"
            emf_path.write_bytes(raw_bytes)
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "png", str(emf_path), "--outdir", tmpdir],
                capture_output=True, timeout=30,
            )
            png_path = Path(tmpdir) / "temp.png"
            if png_path.exists():
                return png_path.read_bytes()
    except Exception as e:
        logger.warning("EMF→PNG 변환 실패: %s", e)
    return None


def _normalize_image_bytes(raw_bytes: bytes, fmt: str = "png") -> tuple[bytes, str] | None:
    """이미지를 Claude API 호환 형태로 정규화한다.

    - RGBA/P 등 → RGB 변환
    - 긴 변이 MAX_IMAGE_DIMENSION 초과 시 리사이즈
    - PNG가 크면 JPEG로 폴백
    - 결과가 MAX_IMAGE_SIZE 초과 시 None 반환

    Returns:
        (정규화된 바이트, 포맷 문자열) 또는 None
    """
    from PIL import Image as PILImage
    from PIL.Image import Resampling

    # EMF/WMF → PNG 변환 (LibreOffice 사용)
    if fmt in ("x-emf", "emf", "x-wmf", "wmf"):
        converted = _convert_emf_to_png(raw_bytes)
        if converted is None:
            return None
        raw_bytes = converted
        fmt = "png"

    try:
        img = PILImage.open(BytesIO(raw_bytes))

        # RGB 변환 (RGBA, P, LA 등 처리)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # 리사이즈
        max_dim = max(img.size)
        if max_dim > MAX_IMAGE_DIMENSION:
            ratio = MAX_IMAGE_DIMENSION / max_dim
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Resampling.LANCZOS)

        # PNG로 먼저 시도, 크면 JPEG로 폴백
        save_fmt = "JPEG" if fmt in ("jpeg", "jpg") else "PNG"
        buf = BytesIO()
        img.save(buf, format=save_fmt)
        result = buf.getvalue()
        out_fmt = "jpeg" if save_fmt == "JPEG" else "png"

        if len(result) > MAX_IMAGE_SIZE and save_fmt == "PNG":
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            result = buf.getvalue()
            out_fmt = "jpeg"

        if len(result) > MAX_IMAGE_SIZE:
            return None
        return (result, out_fmt)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 이미지 포함 문서 읽기
# ---------------------------------------------------------------------------

def read_document_text(file_path: str) -> str:
    """문서에서 텍스트만 추출한다. 이미지는 save_extracted_images()로 별도 처리."""
    path = Path(file_path)
    ext = path.suffix.lower()

    legacy_formats = {".ppt": ".pptx", ".doc": ".docx", ".xls": ".xlsx"}
    if ext in legacy_formats:
        raise ValueError(
            f"레거시 포맷({ext})은 지원하지 않음. "
            f"{legacy_formats[ext]}로 변환 후 다시 시도할 것."
        )

    if ext == ".pdf":
        return _read_pdf_text(path)
    elif ext == ".pptx":
        return _read_pptx_text(path)
    elif ext == ".docx":
        return _read_docx_text(path)
    elif ext == ".xlsx":
        return _read_xlsx(path)
    elif ext in (".txt", ".md"):
        return _read_text(path)
    else:
        raise ValueError(f"지원하지 않는 파일 형식: {ext!r}")


def has_images(file_path: str) -> bool:
    """문서에 이미지가 포함되어 있는지 빠르게 확인한다."""
    ext = Path(file_path).suffix.lower()
    return ext in (".pdf", ".pptx", ".docx")


def _read_pdf_text(path: Path) -> str:
    """PDF 텍스트를 추출한다.

    스캔 PDF는 예외를 던지지 않고 배너를 붙여 반환한다. 등기부등본·주주명부처럼
    정당하게 스캔본인 문서가 파이프라인을 중단시키면 안 되고, 대신 호출자가
    vision 경로로 라우팅할 수 있도록 신호를 준다.
    """
    import pdfplumber

    pages_text: list[str] = []
    empty_pages = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                if len(text) < MIN_CHARS_PER_PAGE:
                    empty_pages += 1
                    marker = " (텍스트 없음 — 이미지로 읽을 것)"
                else:
                    marker = ""
                pages_text.append(f"--- Page {i}{marker} ---\n{text}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse PDF {path}: {e}") from e

    total_pages = len(pages_text)
    if total_pages == 0:
        raise RuntimeError(f"PDF에 페이지가 없음: {path}")

    result_text = "\n\n".join(pages_text)

    # 페이지 마커가 항상 non-empty라서 result_text.strip()으로는
    # 빈 스캔본을 걸러낼 수 없다. 실제 본문 길이로 판정한다.
    if empty_pages:
        ratio = empty_pages / total_pages
        fully_scanned = ratio >= SCAN_RATIO_THRESHOLD
        logger.warning(
            "%s: %s (%d/%d 페이지 텍스트 없음)",
            "스캔 PDF 감지" if fully_scanned else "일부 페이지 이미지",
            path.name, empty_pages, total_pages,
        )
        template = SCAN_BANNER if fully_scanned else PARTIAL_SCAN_BANNER
        banner = template.format(empty=empty_pages, total=total_pages)
        result_text = f"{banner}\n\n{result_text}"

    return result_text


def pdf_text_coverage(file_path: str) -> dict:
    """PDF의 텍스트 레이어 상태를 조사한다. vision 라우팅 판단용.

    Returns:
        {"total_pages": 3, "empty_pages": 3, "chars": 0, "is_scanned": True}
    """
    import pdfplumber

    total = empty = chars = 0
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            total += 1
            text = (page.extract_text() or "").strip()
            chars += len(text)
            if len(text) < MIN_CHARS_PER_PAGE:
                empty += 1

    return {
        "total_pages": total,
        "empty_pages": empty,
        "chars": chars,
        # 문서 전체를 vision으로 읽어야 하는가 (일부 페이지만 빈 경우는 False)
        "is_scanned": total > 0 and (empty / total) >= SCAN_RATIO_THRESHOLD,
        "has_empty_pages": empty > 0,
    }


def _read_pptx_text(path: Path) -> str:
    from pptx import Presentation

    try:
        prs = Presentation(str(path))
        slides_text: list[str] = []

        for i, slide in enumerate(prs.slides, start=1):
            texts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        # para.text는 run 연결에 더해 줄바꿈까지 보존한다
                        line = para.text
                        if line.strip():
                            texts.append(line)
                if shape.has_table:
                    table = shape.table
                    for row in table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        if any(cells):
                            texts.append("\t".join(cells))
            slides_text.append(f"--- Slide {i} ---\n" + "\n".join(texts))

        result_text = "\n\n".join(slides_text)
    except Exception as e:
        raise RuntimeError(
            f"PPTX를 읽을 수 없음: {path.name} — {diagnose_broken_ooxml(path)}"
        ) from e

    if not result_text.strip():
        raise RuntimeError(f"No text extracted from PPTX: {path}")
    return result_text


def _read_docx_text(path: Path) -> str:
    from docx import Document

    try:
        doc = Document(str(path))
        parts: list[str] = []

        # 본문 단락
        for p in doc.paragraphs:
            if p.text.strip():
                parts.append(p.text)

        # 테이블 내 텍스트
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    parts.append("\t".join(cells))

        result_text = "\n".join(parts)
    except Exception as e:
        raise RuntimeError(
            f"DOCX를 읽을 수 없음: {path.name} — {diagnose_broken_ooxml(path)}"
        ) from e

    if not result_text.strip():
        raise RuntimeError(f"No text extracted from DOCX: {path}")
    return result_text


# ---------------------------------------------------------------------------
# 이미지 파일 저장 (보고서 삽입용)
# ---------------------------------------------------------------------------

def save_extracted_images(file_path: str, output_dir: str) -> list[dict]:
    """문서에서 이미지를 추출하여 {output_dir}/images/에 저장한다.

    Returns:
        [{"filename": "slide_3_img_1.png", "path": "images/slide_3_img_1.png"}, ...]
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    images_dir = Path(output_dir).resolve() / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    raw_images: list[tuple[bytes, str, str, str]] = []  # (bytes, format, source, context)
    # 파일명에서 공백·괄호 등 markdown 경로에 문제되는 문자를 언더스코어로 치환
    doc_stem = re.sub(r'[^\w\-]', '_', path.stem)

    if ext == ".pdf":
        raw_images = _extract_pdf_images(path)
    elif ext == ".pptx":
        raw_images = _extract_pptx_images(path)
    elif ext == ".docx":
        raw_images = _extract_docx_images(path)
    else:
        return []

    if len(raw_images) > MAX_IMAGES_PER_DOC:
        logger.warning(
            "이미지 개수 초과로 잘림: %s (%d장 중 %d장만 저장). "
            "누락분은 보고서에 반영되지 않음.",
            path.name, len(raw_images), MAX_IMAGES_PER_DOC,
        )

    results: list[dict] = []
    for idx, (img_bytes, fmt, source, context) in enumerate(raw_images[:MAX_IMAGES_PER_DOC], start=1):
        filename = f"{doc_stem}_img_{idx}.{fmt}"
        filepath = images_dir / filename
        filepath.write_bytes(img_bytes)
        results.append({
            "filename": filename,
            "path": f"images/{filename}",
            "source": source,
            "context": context,
        })

    return results


def _extract_pdf_images(path: Path) -> list[tuple[bytes, str, str, str]]:
    import pdfplumber

    results: list[tuple[bytes, str, str, str]] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if page.images:
                try:
                    buf = BytesIO()
                    page.to_image(resolution=150).save(buf, format="PNG")
                    normalized = _normalize_image_bytes(buf.getvalue(), "png")
                    if normalized:
                        img_bytes, out_fmt = normalized
                        context = (page.extract_text() or "")[:100].replace("\n", " ")
                        results.append((img_bytes, out_fmt, f"Page {i}", context))
                    else:
                        logger.warning(
                            "PDF page %d 이미지 누락 (%s): 정규화 후에도 크기 초과",
                            i, path.name,
                        )
                except Exception as e:
                    logger.warning("PDF page %d 이미지 추출 실패 (%s): %s", i, path.name, e)
    return results


def _extract_pptx_images(path: Path) -> list[tuple[bytes, str, str, str]]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    results: list[tuple[bytes, str, str, str]] = []
    prs = Presentation(str(path))
    for i, slide in enumerate(prs.slides, start=1):
        # 슬라이드 텍스트 수집 (이미지 컨텍스트용)
        slide_texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = para.text.strip()
                    if line:
                        slide_texts.append(line)
        slide_context = " | ".join(slide_texts[:3])[:100]

        for shape in slide.shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    img = shape.image
                    fmt = img.content_type.split("/")[-1]
                    normalized = _normalize_image_bytes(img.blob, fmt)
                    if normalized:
                        img_bytes, out_fmt = normalized
                        results.append((img_bytes, out_fmt, f"Slide {i}", slide_context))
                    else:
                        logger.warning(
                            "PPTX slide %d 이미지 누락 (%s): 포맷 %s 정규화 실패",
                            i, path.name, fmt,
                        )
                except Exception as e:
                    logger.warning("PPTX slide %d 이미지 추출 실패 (%s): %s", i, path.name, e)
    return results


def _extract_docx_images(path: Path) -> list[tuple[bytes, str, str, str]]:
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    results: list[tuple[bytes, str, str, str]] = []
    doc = Document(str(path))
    img_idx = 0
    for rel in doc.part.rels.values():
        if rel.reltype == RT.IMAGE:
            img_idx += 1
            try:
                blob = rel.target_part.blob
                ct = rel.target_part.content_type
                fmt = ct.split("/")[-1]
                normalized = _normalize_image_bytes(blob, fmt)
                if normalized:
                    img_bytes, out_fmt = normalized
                    results.append((img_bytes, out_fmt, f"Image {img_idx}", ""))
                else:
                    logger.warning(
                        "DOCX 이미지 %d 누락 (%s): 포맷 %s 정규화 실패",
                        img_idx, path.name, fmt,
                    )
            except Exception as e:
                logger.warning("DOCX 이미지 추출 실패 (%s): %s", path.name, e)
    return results
