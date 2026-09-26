#!/usr/bin/env python3
"""투심보고서 백테스트 — 자동 생성본을 사람 완성본과 대조해 점수를 낸다.

품질을 바꾸는 작업(압축기·서술 섹션 자동화)을 하기 전에 자를 먼저 만든다.
자가 없으면 "좋아진 것 같다"밖에 말할 수 없다.

사용법:
    python scripts/backtest.py compare 자동생성.docx 완성본.docx
    python scripts/backtest.py corpus /path/to/투심보고서   # 하우스 보이스 기준선

주의: 사람 완성본에도 구글 닥스 sdt 잔해가 남아 있어, 정규화 없이 읽으면
문단 텍스트가 빈 문자열로 나온다. load_paragraphs가 항상 정규화한다.
"""

import difflib
import re
import statistics as st
import sys
from pathlib import Path
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

from docx import Document  # noqa: E402

from tools.ooxml_normalize import unwrap_junk_sdt  # noqa: E402

# 이 길이 미만 문단은 제목·라벨로 보고 본문 비교에서 제외한다.
MIN_BODY_CHARS = 15

# 두 문장이 같은 내용이라고 볼 유사도 하한.
# 0.6은 어미·조사만 손본 수준까지 같다고 인정하는 값이다.
MATCH_RATIO = 0.6

# 하우스 보이스 목표치.
# 감이 아니라 완성본 코퍼스 41개 문서의 실측값이다 (`backtest.py corpus`).
# 초기에는 Example Company 한 건만 보고 85자/6%로 잡았으나, 코퍼스 전체는 더 짧다.
TARGET_BULLET_MEDIAN = 68
TARGET_LONG_BULLET_RATIO = 0.04   # 150자 초과 불릿 비율
LONG_BULLET_CHARS = 150


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def load_paragraphs(path: str) -> list[str]:
    """docx의 본문 문단을 읽는다. sdt 정규화를 반드시 거친다."""
    document = Document(path)
    unwrap_junk_sdt(document)
    out = []
    for para in document.paragraphs:
        text = para.text.strip()
        if text:
            out.append(text)
    return out


def load_table_texts(path: str) -> list[str]:
    document = Document(path)
    unwrap_junk_sdt(document)
    out = []
    for table in document.tables:
        for row in table.rows:
            seen = set()
            for cell in row.cells:
                key = id(cell._tc)
                if key in seen:
                    continue
                seen.add(key)
                text = cell.text.strip()
                if text:
                    out.append(text)
    return out


def _bodies(paragraphs: list[str]) -> list[str]:
    return [p for p in paragraphs if len(p) >= MIN_BODY_CHARS]


def _best_ratio(needle: str, haystack: list[str]) -> float:
    return max(
        (difflib.SequenceMatcher(None, needle, h).ratio() for h in haystack),
        default=0.0,
    )


# 논거 커버리지 측정용. 완성본에서 이 표지들이 얼마나 재현됐는지 본다.
# 생존율(문자 유사도)은 같은 논거를 다르게 쓰면 낮게 나오므로 보완 지표가 필요하다.
def _shared_keywords(gen: list[str], ref: list[str]) -> tuple[int, int]:
    """양쪽에 공통으로 등장하는 고유명사·수치 표지의 커버리지.

    문자 유사도는 "니지산지가 홀로라이브를 시총 2배로 이김"을 한 문장으로 쓰든
    세 문단으로 풀든 다르게 채점한다. 논거가 같은지는 고유명사·핵심어로 본다.
    """
    import re

    def marks(paras: list[str]) -> set[str]:
        text = " ".join(paras)
        # 영문 고유명사, 한글 2글자+ 고유명사 후보, 숫자+단위
        out = set(re.findall(r"[A-Za-z][A-Za-z.\-]{2,}", text))
        out |= set(re.findall(r"\d[\d,.]*\s*(?:만|억|명|%|배|원|개사?|시간)", text))
        return {m for m in out if len(m) >= 2}

    gen_marks, ref_marks = marks(gen), marks(ref)
    if not ref_marks:
        return 0, 0
    shared = gen_marks & ref_marks
    return len(shared), len(ref_marks)


def compare(generated_path: str, reference_path: str) -> dict:
    """자동 생성본과 사람 완성본을 대조한다.

    생존율      — 완성본 분량 중 생성본에서 유래한 비율(문자 유사도). 높을수록 좋다.
    논거 커버리지 — 완성본의 고유명사·수치 표지 중 생성본이 담은 비율.
                   같은 논거를 다르게 써도 잡히므로 생존율의 맹점을 보완한다.
    폐기율      — 생성본 문단 중 완성본에 남지 않은 비율. 낮을수록 좋다.
    """
    gen = _bodies(load_paragraphs(generated_path))
    ref = _bodies(load_paragraphs(reference_path))

    ref_total = sum(len(r) for r in ref)
    survived = sum(len(r) for r in ref if _best_ratio(r, gen) > MATCH_RATIO)

    shared, ref_marks = _shared_keywords(gen, ref)

    dropped = [g for g in gen if _best_ratio(g, ref) <= MATCH_RATIO]

    gen_chars = sum(len(g) for g in gen)
    lens = [len(g) for g in gen if MIN_BODY_CHARS < len(g) < 400]

    return {
        "생존율": survived / ref_total if ref_total else 0.0,
        "논거커버리지": shared / ref_marks if ref_marks else 0.0,
        "폐기율": len(dropped) / len(gen) if gen else 0.0,
        "생성본_자수": gen_chars,
        "완성본_자수": ref_total,
        "분량비": gen_chars / ref_total if ref_total else 0.0,
        "불릿_중앙값": st.median(lens) if lens else 0,
        "장문_비율": (
            sum(1 for x in lens if x > LONG_BULLET_CHARS) / len(lens) if lens else 0.0
        ),
        "표_충전": _table_fill_rate(generated_path),
    }


def _table_fill_rate(path: str) -> float:
    """표 값 셀 중 실제로 채워진 비율. 라벨·헤더는 세지 않는다."""
    document = Document(path)
    unwrap_junk_sdt(document)
    filled = total = 0
    for table in document.tables:
        for row in list(table.rows)[1:]:      # 헤더 행 제외
            seen = set()
            cells = []
            for cell in row.cells:
                key = id(cell._tc)
                if key not in seen:
                    seen.add(key)
                    cells.append(cell)
            for cell in cells[1:]:            # 0열은 라벨로 본다
                total += 1
                text = cell.text.strip()
                if text and "미확인" not in text and text not in ("-", "·"):
                    filled += 1
    return filled / total if total else 0.0


def corpus_stats(root: str) -> dict:
    """완성본 코퍼스에서 하우스 보이스 기준선을 뽑는다.

    P3(압축기)의 목표치가 여기서 나온다. 감으로 정하지 않기 위함이다.
    """
    finals = []
    for path in Path(root).rglob("*.docx"):
        name = _nfc(path.name)
        if name.startswith(("~$", ".")):
            continue
        if "투심보고서" not in name and "투심 보고서" not in name:
            continue
        if any(k in name for k in ("템플릿", "template", "회사명")):
            continue
        finals.append(path)

    all_lens, per_doc = [], []
    for path in sorted(finals):
        try:
            bodies = _bodies(load_paragraphs(str(path)))
        except Exception:
            continue
        if len(bodies) < 20:            # 초안·조각 파일 제외
            continue
        lens = [len(b) for b in bodies if len(b) < 400]
        if not lens:
            continue
        all_lens += lens
        per_doc.append(
            {
                "name": _nfc(path.name)[:44],
                "문단": len(bodies),
                "자수": sum(len(b) for b in bodies),
                "중앙값": st.median(lens),
                "장문비": sum(1 for x in lens if x > LONG_BULLET_CHARS) / len(lens),
            }
        )

    return {
        "문서수": len(per_doc),
        "불릿_중앙값": st.median(all_lens) if all_lens else 0,
        "불릿_평균": st.mean(all_lens) if all_lens else 0,
        "장문_비율": (
            sum(1 for x in all_lens if x > LONG_BULLET_CHARS) / len(all_lens)
            if all_lens
            else 0.0
        ),
        "문서별": per_doc,
    }


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    mode = sys.argv[1]

    if mode == "compare":
        if len(sys.argv) != 4:
            print("사용법: backtest.py compare <자동생성.docx> <완성본.docx>")
            return 2
        result = compare(sys.argv[2], sys.argv[3])
        print("=" * 52)
        print("백테스트 — 자동 생성본 vs 완성본")
        print("=" * 52)
        print(f"  생존율        {_fmt_pct(result['생존율']):>6}   (완성본 중 생성본 유래, 문자 유사도)")
        print(f"  논거 커버리지  {_fmt_pct(result['논거커버리지']):>6}   (완성본 고유명사·수치 중 재현)")
        print(f"  폐기율        {_fmt_pct(result['폐기율']):>6}   (생성본 중 버려진 문단)")
        print(f"  표 충전율   {_fmt_pct(result['표_충전']):>6}")
        print()
        print(f"  분량        {result['생성본_자수']:,}자 / {result['완성본_자수']:,}자"
              f"  = {result['분량비']:.2f}배")
        median = result["불릿_중앙값"]
        long_ratio = result["장문_비율"]
        print(f"  불릿 중앙값 {median:>6.0f}자   (목표 {TARGET_BULLET_MEDIAN}자 "
              f"{'✔' if median <= TARGET_BULLET_MEDIAN else '✘ 초과'})")
        print(f"  150자 초과  {_fmt_pct(long_ratio):>6}   (목표 "
              f"{_fmt_pct(TARGET_LONG_BULLET_RATIO)} "
              f"{'✔' if long_ratio <= TARGET_LONG_BULLET_RATIO else '✘ 초과'})")
        return 0

    if mode == "corpus":
        # 기본값에 절대경로를 박지 않는다 — 머신을 옮기면 깨진다.
        root = sys.argv[2] if len(sys.argv) > 2 else str(Path.home() / "Downloads" / "투심보고서")
        stats = corpus_stats(root)
        print("=" * 68)
        print(f"완성본 코퍼스 기준선 — {stats['문서수']}개 문서")
        print("=" * 68)
        for doc in sorted(stats["문서별"], key=lambda d: -d["자수"]):
            print(f"  {doc['name']:<46} {doc['자수']:>6,}자 "
                  f"중앙 {doc['중앙값']:>3.0f} 장문 {_fmt_pct(doc['장문비']):>4}")
        print("-" * 68)
        print(f"  전체 불릿 중앙값 {stats['불릿_중앙값']:.0f}자 / "
              f"평균 {stats['불릿_평균']:.0f}자")
        print(f"  150자 초과 비율  {_fmt_pct(stats['장문_비율'])}")
        return 0

    print(f"알 수 없는 모드: {mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
