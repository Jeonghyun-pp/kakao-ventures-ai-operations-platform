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
