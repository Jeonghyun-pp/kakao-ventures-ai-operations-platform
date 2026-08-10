# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VC 투자심사보고서(투심보고서) 자동화 MCP 서버. IR 덱, 재무자료, 미팅 메모 등을 `input/`에 넣고 Claude Desktop에서 호출하면 markdown 초안을 자동 생성한다.

## Setup & Run

```bash
cd mcp
python -m venv .venv && source .venv/bin/activate  # 최초 1회
pip install -r requirements.txt
python server.py  # MCP 서버 실행 (Claude Desktop에서 연결)
```

Python 3.11+ 필수 (현재 `.venv`는 3.12). Claude Desktop MCP 등록은 README.md 참조.

## Architecture

### MCP 서버 (8개 tool) — `mcp/server.py`

FastMCP 기반 진입점. 서버 프로세스 내 전역 상태를 공유하므로 **호출 순서가 중요**하다.

| 전역 변수 | 설정 시점 | 참조 시점 |
|-----------|-----------|-----------|
| `_last_scanned_folder` | `scan_inputs` | `read_file`(이미지 저장), `get_prompt`(이미지 목록) |
| `_saved_images_cache` | `read_file` | `get_prompt`(이미지 목록 생성) |
| `_image_descriptions` | `describe_image` | `get_prompt`(이미지 목록 생성) |
| `_images_info_cache` | 자동 빌드/무효화 | `get_prompt` |
| `_report_sections` | `save_section` | `create_report`(최종 생성) |
| `_report_tables` | `save_tables` | `create_report`(docx 표 채우기) |
| `_report_company` / `_report_folder` | `save_section` | `create_report`(폴백) |

`scan_inputs`로 새 폴더를 스캔하면 이미지 캐시 + 누적 섹션(`_report_sections`) + 표 데이터(`_report_tables`)가 모두 리셋된다.

**도구 호출 순서 (섹션별 핑퐁 워크플로우):**
1. `scan_inputs(folder_path)` — 폴더 스캔, 전역 상태 초기화
2. `read_file(file_path)` — 텍스트 추출 + PDF/PPTX/DOCX는 `images/`에 이미지 자동 저장
3. `read_image` / `describe_image` — 이미지 시각 분석 후 설명 캐싱 ("스킵"으로 장식용 마킹)
4. **`save_tables(tables)`** — 등기부등본·주주명부·Term Sheet에서 뽑은 표 데이터 저장.
   스캔 PDF는 `read_file`이 `[스캔 문서]` 배너를 반환하므로 `read_image`로 직접 보고 읽어야 한다
5. 개요 제시 → 사용자 컨펌
6. 섹션마다: `get_prompt(section_name)` → 작성 → **사용자와 핑퐁 수정** → 확정 시 `save_section(section_name, content, company_name, folder_path)` → 작업용 md 즉시 갱신 + 진행률 반환
7. 전체 섹션 완료 후 최종 점검 → `create_report(company_name, folder_path)` — 누적 섹션 + 표로 최종 md + docx 생성 (생략 시 `_report_sections`/`_report_tables` 사용, 명시하면 일회성 생성). 표 데이터가 비어 있으면 경고를 반환한다

### 핵심 모듈

**`mcp/tools/document.py`**
- `read_document_text(file_path)` — 텍스트 전용 추출 진입점
- `save_extracted_images(file_path, output_dir)` — `{output_dir}/images/`에 저장, `[{"path": "images/xxx.png", "source": "Slide 3", "context": "슬라이드 텍스트 앞 100자"}]` 반환
- 이미지 제약: 최대 1MB/장, 50장/문서, 1568px(긴 변). 초과 시 JPEG 폴백 또는 스킵
- EMF/WMF → `soffice --headless`로 PNG 변환 (LibreOffice 필요)
- 레거시 포맷(`.ppt/.doc/.xls`) 즉시 ValueError. 이미지 전용 PDF는 RuntimeError

**`mcp/tools/writer.py`** — markdown(.md) 출력 전용
- `write_markdown(sections, company_name, folder_path, section_images)` — md 초안 생성
- `SECTION_STRUCTURE` 리스트가 md 골격 정의. 이 리스트의 `sections_key` 값이 `create_report`의 `sections` dict key와 정확히 매핑되어야 함
- 유효하지 않은 key는 `get_close_matches`로 유사 key 힌트 제공 후 경고 주석 삽입
- `used_images` set으로 이미지 중복 삽입 방지 (인라인 `![]()` + `section_images` 통합 추적)
- **`투자 포인트` 섹션은 이미지 삽입이 코드 레벨에서 차단됨** (인라인 및 `section_images` 모두)

**`mcp/tools/writer_docx.py`** — Word(.docx) 출력 전용 (템플릿 기반)
- `write_docx(sections, company_name, folder_path, section_images, template_path=None)` — 템플릿 `{{플래그}}`를 내용으로 치환
- 템플릿 위치: `mcp/template/투심보고서_템플릿.docx` (매 실행 시 `shutil.copy2`로 deep copy). `template_path` 인자로 재지정 가능
- `FLAG_TO_KEY` dict이 템플릿 `{{플래그}}` → `sections` key 매핑 정의. 템플릿의 플래그 집합과 정확히 일치해야 함 (`{{회사명}}`·`{{작성일}}`은 단순 치환이라 별도 처리)
- 워드 네이티브 목록(numbering 자체 주입), `주요 경쟁자` 섹션만 실제 docx 표로 렌더(그 외 표는 텍스트 폴백), Figure 번호 캡션, 작성일 16pt 등 실서식 재현
- **`투자 포인트` 섹션은 이미지 삽입이 코드 레벨에서 차단됨** (writer.py와 동일 정책)
- 이미지 크기는 **고정 폭이 아니라 네이티브 비율 기반**(`_picture_size`). 본문 폭
  6.19" 상한, 최소 높이 0.9" 확보. 그래도 0.7" 미만이면 판독 불가로 보고 경고를
  `warnings` 리스트에 실어 `create_report` 반환값으로 올린다
- 생성 마지막에 `normalize_layout(doc)` + `audit_layout(doc)` 실행

**`mcp/tools/ooxml_normalize.py`** — 텍스트를 바꾸지 않는 구조 정규화
- `unwrap_junk_sdt` — 구글 닥스 `goog_rdk_*` sdt 잔해 제거 (표 채우기 전제조건)
- `normalize_layout` — 표 레이아웃 4종 일괄 보정. **템플릿 자체가 원인**이라
  매 산출물에서 재발했다:
  - `unfloat_tables` — `w:tblpPr` 제거. 템플릿 표 8개 중 **3개**(회사 히스토리·
    지식재산권·회수방안)가 부동 표라 앞 본문 길이가 바뀔 때마다 본문을 덮었다
  - `normalize_table_indent` — 420 twip로 통일. 핵심인력 795 / 투자내역 903이
    본문보다 밀려 보였다
  - `trim_cell_trailing_empty` — 셀 끝 빈 문단 제거. `vAlign=center`가 걸려
    있어도 빈 줄까지 포함해 가운데를 잡아 글자가 위에 붙는 증상의 정체
  - `tighten_cell_bullets` — 셀 안 불릿 ind를 220/220으로
- `audit_layout` — 정규화 후 잔존 위반 보고
- `normalize_file(path)` — 파일 제자리 정규화. **화면 텍스트가 한 글자라도
  달라지면 저장하지 않고 예외.** 사람이 Word로 편집한 뒤 재실행용
  (Word에서 표를 드래그하면 부동 표 속성이 다시 붙는다)

**`mcp/tools/tables_fill.py`**
- `TABLE_SPECS[].headers` — 템플릿 헤더가 잘못된 표를 생성 시 교정한다.
  지식재산권은 `("구분","구분")` 2열로 축퇴되어 출원번호를 넣을 자리가 없었고,
  투자내역은 `유치 금액(원)` 헤더에 달러 값이 들어간 딜이 있었다
- `ensure_columns` — 데이터가 표보다 열이 많으면 마지막 셀을 복제해 늘린다.
  이전에는 초과 열을 **조용히 버렸다**. 못 늘린 경우 `report["dropped_columns"]`로 보고

**`mcp/tools/style_check.py`**
- 분량 임계값(중앙값 68자 등) + `BANNED` 금지 표현 정규식. 둘 다 `save_section`
  시점에 검사되어 그 자리에서 고치게 한다
- `BANNED`에는 **오탐 여지가 없는 패턴만** 넣는다. 한 번이라도 정상 문장을
  잡으면 이후 모든 경고가 무시된다 (`검증 가능한`, `종합 진단`은 통과해야 함)

### `create_report`의 유효한 sections key

```
투자 포인트, 문제정의, 가설, 서비스 상세, 지표, BM,
향후 전략 및 계획, Financial Projection, 시장 현황 및 전망,
주요 경쟁자, 경쟁우위 평가, Investment Analysis
```

### 프롬프트 시스템

`prompts/*.md` 편집으로 출력 품질 조정 (코드 수정 불필요).

- `00_공통스타일.md` — 모든 섹션 우선 적용 (어투·양식·금지사항·웹검색·이미지 활용 규칙)
- 섹션 프롬프트 파일명과 `get_prompt`의 `section_name` 인자가 일치해야 함

**작성 순서 (01_투자포인트는 반드시 마지막):**
```
05a_문제정의 → 05b_가설 → 06a_서비스상세 → 06b_지표 → 07_BM →
08_향후전략및계획 → 09_financial_projection → 10_시장현황및전망 →
11_주요경쟁자 → 12_경쟁우위평가 → 13_investment_analysis → 01_투자포인트
```

### 에러 정책

fail-fast. 빈 폴더·이미지 PDF·지원 안 되는 포맷은 즉시 에러. 정보 부족 섹션만 스킵.

## Key Conventions

- 텍스트 파일 읽기 인코딩 폴백 순서: utf-8 → cp949 → latin-1
- 출력 파일명: `투심보고서_{safe_company_name}_{YYYY-MM-DD}.md` (`[^\w\-]`는 `_`로 치환)
- `section_images` 인자는 dict 또는 JSON 문자열 모두 허용 (`sections`도 동일)
