"""LinkedIn Listly 어댑터 — 반자동 정밀 탐색 보조 경로(HarvestAPI와 병행).

[C9/D5] 링크드인 = 2개 독립 어댑터. HarvestAPI(무인·대량) = 본체, Listly = 보조.
사람이 링크드인에서 Stealth+거주지 필터로 검색 → Listly 크롬 익스텐션으로 엑셀 export →
지정 드롭 폴더(cfg.channels.linkedin_listly.drop_dir)에 저장하면, 이 어댑터가 그 파일을
읽어 Candidate로 변환한다. 수집(파싱)만 담당 — 판단 없음.

**독립 저장/드롭세이프:** 이 어댑터의 결과는 pipeline이 별도 raw 파일
(linkedin_listly_YYYY-MM-DD.json)로 저장하고, 모든 Candidate에 adapter="linkedin.listly"
provenance 태그가 붙는다. HarvestAPI 어댑터를 꺼도(=config enabled:false) 이쪽은 무손상,
반대도 마찬가지. 병합은 다음 단계(core.dedup/normalize)에서 공통 스키마로 흡수.

거주지 2버킷(A.대한민국 / B.미국 전체+타깃스쿨)은 '사람이 Listly에서 검색할 때' 거는
필터라 이 어댑터의 입력이 아니다. 파일명/시트로 버킷을 구분하고 싶으면 signals.bucket에 기록.

TODO(파싱 상세): Listly 엑셀 컬럼 스키마는 사용자가 export한 실제 파일을 보고 확정.
아래 COLUMN_ALIASES는 흔한 링크드인 검색결과 컬럼명 후보 — 첫 실 파일로 교정한다.
"""
from __future__ import annotations

from pathlib import Path

from .base import Adapter, Candidate

PROJ = Path(__file__).resolve().parent.parent

# Listly/링크드인 검색결과 엑셀의 컬럼명 후보(소문자 비교). 실제 export로 확정 전 방어적 매핑.
COLUMN_ALIASES = {
    "name":         ("name", "full name", "이름", "성명"),
    "linkedin_url": ("profile url", "profile link", "link", "url", "linkedin", "프로필"),
    "headline":     ("headline", "title", "occupation", "직함", "헤드라인"),
    "location":     ("location", "지역", "위치", "거주지"),
    "company":      ("company", "current company", "회사", "현재 회사"),
}


class ListlyAdapter(Adapter):
    source = "링크드인"                      # 채널은 링크드인(극초기 DB '발굴' 값과 일치)

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"].get("linkedin_listly", {})
        drop_dir = PROJ / ch.get("drop_dir", "data/linkedin_listly")
        files = self._pending_files(drop_dir)
        if not files:
            print("· linkedin.listly: 드롭 폴더에 처리할 export 파일 없음 (no-op)")
            return []

        rows = self._read_rows(files)
        if rows is None:                     # openpyxl 미설치 등 — 조용히 스킵(다른 채널 무손상)
            return []

        out: list[Candidate] = []
        for row, bucket in rows:
            cand = self._row_to_candidate(row, bucket)
            if cand:
                out.append(cand)
        print(f"✅ linkedin.listly: {len(out)}건 (파일 {len(files)}개)")
        return out

    # ---------------- 파일 수집 ----------------
    def _pending_files(self, drop_dir: Path) -> list[Path]:
        if not drop_dir.exists():
            return []
        return sorted([p for p in drop_dir.iterdir()
                       if p.suffix.lower() in (".xlsx", ".xls", ".csv")])

    def _read_rows(self, files: list[Path]):
        """엑셀/CSV → [(row_dict, bucket)]. openpyxl 없으면 None(스킵) 반환."""
        rows: list[tuple[dict, str]] = []
        for f in files:
            bucket = self._bucket_of(f)            # 파일명으로 거주지 버킷 추정(kr/us)
            try:
                if f.suffix.lower() == ".csv":
                    rows.extend((r, bucket) for r in _read_csv(f))
                else:
                    xr = _read_xlsx(f)
                    if xr is None:
                        print("⚠️  linkedin.listly: openpyxl 미설치 — xlsx 스킵 "
                              "(pip install openpyxl 또는 CSV로 export)")
                        return None
                    rows.extend((r, bucket) for r in xr)
            except Exception as e:
                print(f"⚠️  linkedin.listly: '{f.name}' 읽기 실패: {e}")
                continue
        return rows

    def _bucket_of(self, f: Path) -> str:
        n = f.name.lower()
        if any(t in n for t in ("kr", "korea", "한국", "대한민국")):
            return "kr"
        if any(t in n for t in ("us", "usa", "미국")):
            return "us"
        return ""

    # ---------------- 행 → Candidate ----------------
    def _row_to_candidate(self, row: dict, bucket: str) -> Candidate | None:
        low = { (k or "").strip().lower(): v for k, v in row.items() }
        get = lambda field: _first_col(low, COLUMN_ALIASES[field])
        name = (get("name") or "").strip()
        li_url = (get("linkedin_url") or "").strip()
        if not name and not li_url:
            return None
        headline = (get("headline") or "").strip()
        return Candidate(
            source=self.source,
            name=name or li_url,
            url=li_url,
            founder=name,
            one_liner=headline,
            raw_text=" · ".join(x for x in (headline, get("company"), get("location")) if x),
            linkedin_url=li_url,                 # [C4] go-forward dedup 키
            adapter="linkedin.listly",           # [C9] provenance
            signals={
                "engine": "listly",
                "headline": headline,
                "location": (get("location") or ""),
                "current_company": (get("company") or ""),
                "bucket": bucket,                # kr | us | ""
            },
        )


# ---------------- io helpers ----------------
def _first_col(low: dict, aliases: tuple[str, ...]):
    for a in aliases:
        if a in low and low[a] not in (None, ""):
            return low[a]
    # 부분일치 폴백(컬럼명이 접미/접두 붙은 경우)
    for key, val in low.items():
        if any(a in key for a in aliases) and val not in (None, ""):
            return val
    return None


def _read_csv(f: Path) -> list[dict]:
    import csv
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_xlsx(f: Path):
    """xlsx → list[dict]. openpyxl 없으면 None."""
    try:
        import openpyxl
    except ImportError:
        return None
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header = [str(h or "") for h in next(rows)]
    except StopIteration:
        return []
    out = []
    for r in rows:
        out.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
    return out
