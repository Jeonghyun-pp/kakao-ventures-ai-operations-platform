# 투자심사보고서 자동화 MCP 서버

IR 덱, 재무자료, 미팅 메모 등을 `input/` 폴더에 넣고 Claude Desktop에서 "투심보고서 만들어줘"라고 입력하면 `.docx` 초안이 자동 생성됩니다.

---

## 시스템 요구사항

- Python 3.11+
- [Claude Desktop](https://claude.ai/download)
- **LibreOffice** (선택이지만 권장)
  - PPTX/DOCX에 들어 있는 **EMF/WMF 도표를 PNG로 변환**할 때 필요합니다.
    없으면 해당 이미지는 조용히 누락됩니다.
  - 생성된 docx를 PDF로 변환해 서식을 눈으로 검증할 때도 씁니다.
  - `brew install --cask libreoffice`

---

## 설치

```bash
cd mcp
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

> `.venv`는 만든 머신의 절대경로에 묶여 있어 **다른 컴퓨터로 복사하면 동작하지
> 않습니다.** 저장소를 새로 받았다면 `.venv`를 지우고 위 절차를 다시 실행하세요.
> MCP 등록 시에도 이 `.venv/bin/python`의 **절대경로**를 지정해야 합니다.

---

## MCP 등록

경로는 모두 **절대경로**여야 하고, python은 위에서 만든 `.venv/bin/python`을
가리켜야 합니다.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "investment-memo": {
      "command": "/절대경로/investment-memo/mcp/.venv/bin/python",
      "args": ["/절대경로/investment-memo/mcp/server.py"]
    }
  }
}
```

Claude Desktop을 **재시작**하면 연결됩니다.

### Claude Code

```bash
claude mcp add investment-memo -- \
  /절대경로/investment-memo/mcp/.venv/bin/python \
  /절대경로/investment-memo/mcp/server.py
```

`--scope project`를 붙이면 저장소 루트의 `.mcp.json`에 기록되어 팀과 공유됩니다.
확인은 `claude mcp list`, 세션 안에서는 `/mcp`.

---

## 사용 방법

### 1. 파일 준비

`input/` 폴더에 분석할 파일을 넣습니다.

**지원 형식:** PDF, PPTX, DOCX, XLSX, TXT, MD

`scan_inputs`는 파일 목록만 반환하고 역할 분류는 하지 않습니다. 어떤 파일이
IR 덱이고 어떤 파일이 등기부등본인지는 Claude가 내용을 읽고 판단합니다.

**넣으면 좋은 자료:**

| 자료 | 채워지는 곳 |
|------|------------|
| 법인등기부등본 | 회사정보(설립일·법인등록번호), 회사 히스토리 |
| 사업자등록증 | 사업자등록번호, 업종, 주소 |
| 주주명부 | 지분 구조 |
| Term Sheet / 투자계약서 | 투자 조건 |
| IR 덱 | 핵심 인력, 제품·지표, 시장 |
| Investment Theme | 본문 서술 전반 |
| 미팅 노트 / 레퍼런스 콜 | 문제정의·현황·경쟁우위의 1차 근거 |

> 등기부등본·주주명부·사업자등록증은 대개 **스캔 PDF**라 텍스트가 추출되지
> 않습니다. 이 경우 `read_file`이 `[스캔 문서]` 배너를 반환하고, Claude가
> 페이지 이미지를 직접 보고 값을 읽습니다.

### 2. Claude Desktop에서 실행

```
투심보고서 만들어줘
```

또는 회사명을 명시:

```
와들 투심보고서 만들어줘
```

### 3. 결과 확인

`output/` 폴더에 `투심보고서_[회사명]_[날짜].docx` 파일이 생성됩니다.

---

## 섹션별 처리 방식

**본문 서술** — `get_prompt` → 작성 → `save_section`

| 섹션 | 비고 |
|------|------|
| 회사의 문제정의 및 가설, 현황 및 지표, BM, 향후 전략 및 계획, 경쟁우위 평가 | 인풋 문서 기반 |
| 시장 현황 및 전망, 주요 경쟁자 | 웹 검색 허용 (출처 표기 필수) |
| Investment Analysis | Financial Projection·IRR 엑셀 필요 |
| 투자 포인트 | 전체 종합이므로 **반드시 마지막** |

**표** — `save_tables`로 저장 → `create_report`가 채움

| 표 | 주 소스 |
|------|--------|
| 투자 대상 회사 | 등기부등본, 사업자등록증 |
| 투자 조건 | Term Sheet, 투자계약서 |
| 회사 히스토리 | 등기부등본 |
| 핵심 인력 | IR 덱, Investment Theme |
| 투자내역 및 채무 현황 | 주주명부, 등기부등본 |
| 주요 경쟁자 | IR 덱, 웹 검색 |
| 지식재산권 | IR 덱 |
| M&A (회수방안) | IRR 분석 |

> 투자 조건 표의 법률 문구(투자금 사용처 실사, 대상거래 처리 방안, 위약벌
> 이자율)는 템플릿에 들어 있으며 `save_tables`에서 값을 주지 않으면 그대로
> 유지됩니다.

**아직 수동** — `[작성 필요]`로 남음

| 섹션 | 이유 |
|------|------|
| 투자 전후 지분 구조 | 표가 아니라 엑셀 캡처 **이미지 슬롯** |
| 재무 현황 | 실제 완성본에서도 비어 있는 경우가 대부분 |
| Financial Projection | 별도 엑셀 첨부 |

---

## 프롬프트 튜닝

`prompts/` 폴더의 `.md` 파일을 수정하면 각 섹션의 작성 품질을 개선할 수 있습니다. 코드를 건드리지 않아도 됩니다.

```
prompts/
├── 01_투자포인트.md
├── 02_회사히스토리.md
├── ...
└── 12_경쟁우위평가.md
```

---

## 에러 처리

| 상황 | 동작 |
|------|------|
| `input/` 폴더가 비어있음 | 즉시 오류 (`ValueError`) |
| 이미지 PDF (텍스트 추출 불가) | 즉시 오류 (`RuntimeError`) |
| 정보 부족 섹션 | 스킵 후 이유 안내 |
| 그 외 오류 | 즉시 중단 (fail-fast) |
