"""인풋 폴더의 문서에 역할을 부여한다.

파일명 패턴과 내용 시그니처를 함께 쓴다. 등기부등본·주주명부·사업자등록증은
대부분 스캔 PDF라 텍스트가 없어 파일명이 유일한 단서이고, 반대로 파일명이
불규칙한 IR 덱은 내용으로 잡는 편이 낫다.

확실하지 않으면 unknown으로 남긴다. 틀린 역할을 자신 있게 부여하는 것보다
"모르겠으니 확인해달라"가 낫다. 실제 코퍼스에서 IR 덱 파일명은
'Example Company IR(...).pdf', 'unx-ir-20260427.pdf', '회사소개서_....pdf',
'Corpus04.pdf'처럼 제각각이라 규칙으로 다 잡을 수 없다.
"""

import re
import unicodedata
from pathlib import Path

# 산출물·템플릿은 인풋이 아니다. 실제 인풋 폴더에 완성된 투심보고서와
# 빈 템플릿이 함께 들어 있어, 걸러내지 않으면 자기 출력을 입력으로 읽는다.
EXCLUDE_PATTERNS = [
    (r"투심보고서|투심\s*보고서|investment\s*memo", "산출물(투심보고서)"),
    (r"^보고서[_\s]", "산출물(보고서)"),
    (r"템플릿|template", "템플릿"),
    (r"\(회사명\)|회사명[_\s]*일자|_회사명_", "빈 템플릿(회사명 자리표시자)"),
    (r"^~\$", "워드 임시파일"),
]

# 파일명 규칙. (정규식, 역할, 신뢰도)
# 스캔 PDF라 내용을 볼 수 없는 문서일수록 여기에 의존한다.
FILENAME_RULES = [
    # 파일명 구분자는 공백만이 아니라 언더스코어·하이픈도 쓰인다
    (
        r"등기|법인등기|등기사항|certificate[\s_\-]*of[\s_\-]*incorporation",
        "등기부등본",
        "high",
    ),
    (r"^법인[_\s(]", "등기부등본", "medium"),      # '법인_(주)코로로직.pdf'
    (r"사업자등록", "사업자등록증", "high"),
    (r"주주명부", "주주명부", "high"),
    (r"cap[_\s]*table|캡테이블", "지분주당단가", "high"),
    (r"정관", "정관", "high"),
    (r"term[_\s]*sheet|주요투자조건", "term_sheet", "high"),
    (r"투자계약서|rcps.*계약|safe.*계약|조건부지분인수|계약서\s*초안", "투자계약서", "high"),
    (r"investment\s*theme|투자\s*테마", "investment_theme", "high"),
    (r"irr|투자수익성", "irr_분석", "high"),
    (r"지분주당단가|주당단가", "지분주당단가", "high"),
    (
        r"financial[_\s]*projection|^fp[_\s]|재무모델|재무추정|손익자료"
        r"|추정.*(매출|손익)|\d+개년.*재무|projection",
        "financial_projection",
        "high",
    ),
    (r"핵심인력|인력.*프로필|career|프로필", "핵심인력_프로필", "high"),
    (r"재무제표|재무재표|대차대조표|손익계산서|주요재무", "재무제표", "high"),
    (
        r"고정비|변동비|판관비|자금계획|투자금.*사용(계획|목적)",
        "재무_보조자료",
        "medium",
    ),
    (r"미팅|meeting|회의|노트|note|레퍼런스|reference[_\s]*call", "미팅노트", "high"),
    (r"qna|q&a|공통응답|질의응답", "질의응답", "medium"),
    (r"시장.*(경쟁사|분석)|경쟁사.*분석|market.*research", "시장자료", "medium"),
    # 'IR_seed', '[AXIOM]IR_FN'처럼 IR 뒤에 언더스코어가 오면 \b가 성립하지
    # 않는다(_는 단어 문자). 구분자를 명시적으로 나열한다.
    (
        r"(?:^|[_\s\-\[\]()])ir(?:[_\s\-]|\b)|ir[_\s-]?deck"
        r"|피칭|pitch|회사소개서|소개서|deck",
        "ir_deck",
        "medium",
    ),
    (r"중소기업확인서|벤처기업확인서|기업부설연구소", "기타증빙", "high"),
    (r"동업자간계약|공동창업자계약|주주간계약", "주주간계약", "high"),
    (r"기술설명|기술소개|백서|whitepaper", "기술자료", "medium"),
    (r"로드맵|roadmap", "기타자료", "low"),
]

# 내용 시그니처. 텍스트가 추출되는 문서에만 적용된다.
# 파일명 규칙보다 강하다 — 파일명은 사람이 마음대로 짓지만 문서 안의
# 법정 문구는 고정되어 있기 때문이다.
#
# 순서가 중요하다. 계약서·정관 같은 긴 문서는 본문에서 "주주명부"를 언급하므로,
# 문서 종류를 스스로 선언하는 규칙을 먼저 두어 선점하게 한다. 실제로 Example Company
# RCPS 투자계약서가 주주명부로 오분류된 적이 있다.
CONTENT_RULES = [
    (r"투\s*자\s*계\s*약\s*서|전환상환우선주.*인수계약|조건부지분인수계약", "투자계약서"),
    (r"주요투자조건\s*협의서|term\s*sheet", "term_sheet"),
    (r"등기사항전부증명서|회사성립연월일", "등기부등본"),
    (r"사업자등록증|법인명\s*\(\s*단체명\s*\)", "사업자등록증"),
    # 문서 제목이 아니라 표 머리글을 본다. 다른 문서가 본문에서
    # "주주명부"를 언급해도 이 열 이름까지 쓰지는 않는다.
    (r"소유주식의\s*수|소유\s*주식의\s*종류", "주주명부"),
    (r"정\s*관\b.*제\s*1\s*조|상호.*목적.*본점", "정관"),
    (r"재무상태표|손익계산서|대차대조표", "재무제표"),
]

# 각 역할이 어느 표/섹션을 채우는지. 에이전트에게 알려주는 용도.
ROLE_TARGETS = {
    "등기부등본": "회사정보(설립일·법인등록번호·주소·업종), 회사히스토리",
    "사업자등록증": "회사정보(사업자등록번호·업종·주소)",
    "주주명부": "투자 전후 지분 구조, 투자내역",
    "term_sheet": "투자조건",
    "투자계약서": "투자조건(주요 조건·제재 조항)",
    "investment_theme": "본문 서술 전반 — 각 섹션으로 해체·재배치",
    "ir_deck": "핵심인력, 현황 및 지표, BM, 시장, 지식재산권",
    "핵심인력_프로필": "핵심인력",
    "재무제표": "재무 현황",
    "financial_projection": "Financial Projection, Investment Analysis",
    "irr_분석": "Investment Analysis(회수방안·투자 수익성)",
    "지분주당단가": "투자 전후 지분 구조",
    "미팅노트": "문제정의·가설, 현황 및 지표, 경쟁우위 — 1차 근거",
    "정관": "참고(주식매수선택권 등)",
    "주주간계약": "참고(지분 관련 특약)",
    "기술자료": "서비스 상세, 경쟁우위",
    "재무_보조자료": "Financial Projection 보조",
    "기타증빙": "참고",
    "기타자료": "참고",
}


def _nfc(text: str) -> str:
    """한글을 NFC로 합성한다.

    macOS 파일시스템은 파일명을 NFD(자모 분리)로 저장한다. '사업자등록증'이
    'ㅅ+ㅏ+ㅇ+ㅓ+ㅂ...'으로 분해되어 있어, NFC로 작성된 정규식 리터럴과
    절대 매치되지 않는다. 정규화하지 않으면 한글 규칙이 전부 조용히 실패한다.
    """
    return unicodedata.normalize("NFC", text)


def _match(patterns, text):
    target = _nfc(text)
    for entry in patterns:
        if re.search(entry[0], target, re.I):
            return entry
    return None


def classify_file(path: str, text: str | None = None) -> dict:
    """파일 하나의 역할을 판정한다.

    Args:
        path: 파일 경로
        text: 이미 추출한 본문 (없으면 파일명만으로 판정)

    Returns:
        {"role": "등기부등본", "confidence": "high",
         "reason": "파일명", "target": "회사정보...", "excluded": False}
    """
    name = Path(path).name

    hit = _match(EXCLUDE_PATTERNS, name)
    if hit:
        return {
            "role": "제외",
            "confidence": "high",
            "reason": hit[1],
            "target": None,
            "excluded": True,
        }

    # 내용 시그니처가 파일명보다 강하다
    if text:
        head = text[:3000]
        hit = _match(CONTENT_RULES, head)
        if hit:
            return {
                "role": hit[1],
                "confidence": "high",
                "reason": "내용 시그니처",
                "target": ROLE_TARGETS.get(hit[1]),
                "excluded": False,
            }

    hit = _match(FILENAME_RULES, name)
    if hit:
        return {
            "role": hit[1],
            "confidence": hit[2],
            "reason": "파일명",
            "target": ROLE_TARGETS.get(hit[1]),
            "excluded": False,
        }

    return {
        "role": "unknown",
        "confidence": "none",
        "reason": "파일명·내용 모두에서 단서 없음",
        "target": None,
        "excluded": False,
    }


def format_report(results: list[dict]) -> str:
    """분류 결과를 에이전트가 읽기 좋은 형태로 만든다."""
    used = [r for r in results if not r["excluded"] and r["role"] != "unknown"]
    unknown = [r for r in results if r["role"] == "unknown"]
    excluded = [r for r in results if r["excluded"]]

    lines = []
    if used:
        lines.append("📑 역할 판정")
        for r in sorted(used, key=lambda x: x["role"]):
            mark = "" if r["confidence"] == "high" else "  ⚠️ 추정"
            lines.append(f"  [{r['role']}] {r['name']}{mark}")
            if r.get("target"):
                lines.append(f"      → {r['target']}")

    if unknown:
        lines.append("")
        lines.append("❓ 역할 불명 — 내용을 읽고 직접 판단할 것")
        for r in unknown:
            lines.append(f"  {r['name']}")

    if excluded:
        lines.append("")
        lines.append("🚫 인풋에서 제외 (산출물·템플릿)")
        for r in excluded:
            lines.append(f"  {r['name']} — {r['reason']}")

    missing = {"등기부등본", "term_sheet", "investment_theme", "ir_deck"} - {
        r["role"] for r in used
    }
    if missing:
        lines.append("")
        lines.append(f"⚠️ 없는 핵심 자료: {', '.join(sorted(missing))}")
        lines.append("   해당 표·섹션은 (미확인)으로 남게 됨")

    return "\n".join(lines)
