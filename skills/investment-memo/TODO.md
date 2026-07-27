# 투자심사보고서 자동화 - TODO

## Step 1: 프로젝트 초기화
- [x] 디렉터리 구조 생성 (mcp/, mcp/tools/, prompts/, input/, output/)
- [x] `config.yaml` 작성
- [x] `mcp/requirements.txt` 작성

## Step 2: MCP 서버 구현
- [x] `mcp/server.py` — FastMCP 기반 서버 진입점
- [x] `mcp/tools/scanner.py` — input/ 폴더 스캔 + 파일명 패턴 분류
- [x] `mcp/tools/document.py` — PDF/PPTX/DOCX/XLSX 파싱
- [x] `mcp/tools/writer.py` — 와들 템플릿 기반 docx 생성
- [x] `mcp/tools/__init__.py`

## Step 3: 프롬프트 파일 작성 (12개 섹션)
- [x] `prompts/01_투자포인트.md`
- [x] `prompts/02_회사히스토리.md`
- [x] `prompts/03_핵심인력.md`
- [x] `prompts/04_투자내역및채무현황.md`
- [x] `prompts/05_문제정의및가설.md`
- [x] `prompts/06_현황및지표.md`
- [x] `prompts/07_BM.md`
- [x] `prompts/08_KPI목표및마일스톤.md`
- [x] `prompts/09_전략.md`
- [x] `prompts/10_시장현황및전망.md`
- [x] `prompts/11_주요경쟁자.md`
- [x] `prompts/12_경쟁우위평가.md`

## Step 4: Claude Desktop 연동
- [x] `README.md` — 설치, 설정, 사용 방법, Claude Desktop MCP 등록 가이드

## Step 5: 검증
- [x] 와들 보고서 인풋 파일로 실제 생성 테스트 (docx 생성 확인)
- [x] scanner.py: 파일 스캔 및 분류 동작 확인
- [x] document.py: PDF 8269자, DOCX 7830자 추출 확인
- [x] writer.py: 투심보고서_와들_2026-04-07.docx 생성 확인 (37,275 bytes)
- [x] 모든 패키지 설치 확인 (pdfplumber, python-pptx, python-docx, openpyxl, mcp)

## Step 6: 이미지 추출 및 보고서 반영 기능
- [x] `mcp/tools/document.py` — 이미지 추출 함수 추가 (PDF/PPTX/DOCX)
- [x] `mcp/server.py` — `read_file` 반환 타입 확장, `save_images` tool 추가, `create_report` 시그니처 변경
- [x] `mcp/tools/writer.py` — markdown 이미지 삽입 지원
- [x] `prompts/00_공통스타일.md` — 이미지 활용 지침 추가
- [x] `mcp/requirements.txt` — Pillow 추가
- [x] `CLAUDE.md` — 아키텍처 문서 업데이트
- [x] 검증: 이미지 포함 문서로 E2E 테스트

## 미해결 (2026-07-20)
- [ ] **floating table 겹침 워드 확인** (P5) — 템플릿 표 3개(회사히스토리·지식재산권·회수방안)가
      floating(tblpPr). 행을 늘리면 LibreOffice에서 다음 제목과 겹쳐 렌더된다.
      사람 완성본에도 floating이 그대로 있어(Example Company 9개 중 4개) LibreOffice 렌더링
      한계로 추정되나 미확증. 검증 파일: invest-report/검증/1_행복제검증.docx 의
      "1) 회사 히스토리" 표 다음 "2) 핵심 인력" 제목이 겹치는지 워드에서 확인할 것.
      겹치면 행이 늘어난 표만 인라인 전환(가로 들여쓰기 tblpX 보존).
- [ ] 투자 전후 지분 구조 = 표가 아니라 **이미지 슬롯**. 완성본은 지분주당단가.xlsx를
      캡처해 붙인다. 자동화하려면 xlsx → 이미지 렌더 필요(LibreOffice 사용 가능).
- [ ] 재무 현황은 완성본에서도 비어 있음(TBD). 자동화 우선순위 낮음.
