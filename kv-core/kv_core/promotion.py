"""승격 게이트 — **무수정률을 재서 auto-send 로 올릴지 정한다.** [W10]

## 성숙곡선의 마지막 칸

    draft  →  approve  →  auto
     W5        W8        W10 ← 이 모듈이 그 문턱을 지킨다

로드맵이 정한 조건: **무수정률 ≥ 95% · 21일 · n ≥ 5.**
"심사역이 우리가 만든 초안을 **고치지 않고 그대로 보냈나**"를 재고, 그 비율이 기준을
넘을 때만 자동 발송을 켤 자격이 생긴다.

## 왜 측정을 먼저 만드나

발송은 이 시스템에서 **가장 비가역적인 행위**다 — 메일은 회수가 안 된다. 그래서 "이제 될
것 같다"는 느낌으로 켜면 안 되고, **숫자가 켜라고 할 때** 켜야 한다. 그 숫자를 만들 수
없으면 애초에 켤 자격이 없다.

이 모듈은 **아무것도 켜지 않는다.** 재서 보고할 뿐이고, 승격 스위치는 사람이 누른다.

## 어떻게 재나

    승인 레코드(payload.body)   = 우리가 만든 초안 원문
    Gmail 보낸편지함             = 실제로 나간 것
      → subject·수신자로 짝을 맞추고 본문을 정규화 비교

**정규화**가 핵심이다. 서명·인용부·줄바꿈·공백 차이는 "수정"이 아니다.
그걸 수정으로 세면 무수정률이 0에 수렴해 게이트가 영원히 안 열린다(가짜 엄격).
반대로 너무 관대하면 실제 수정을 놓친다 — 그래서 **유사도와 함께 원문 diff 요약도** 낸다.

⚠️ 읽기 전용. 이 모듈에도 `.send()` 는 없다(AST 검사 대상).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# 승격 기준 — 로드맵 W10 원문
MIN_RATE = 0.95
MIN_N = 5
WINDOW_DAYS = 21

# 서명·인용·머리말 — 사람이 "고친 것"이 아니라 메일 클라이언트가 붙이는 것
_SIG = re.compile(r"\n-{2,}\s*\n.*$|\n\s*보낸\s*사람\s*:.*$|\n\s*On .*wrote:.*$", re.S)
_QUOTE = re.compile(r"^\s*>.*$", re.M)


def normalize(text: str) -> str:
    """비교용 정규화 — 서명·인용·공백 차이는 수정이 아니다."""
    t = _SIG.sub("", text or "")
    t = _QUOTE.sub("", t)
    t = re.sub(r"\s+", " ", t)                    # 줄바꿈·연속 공백 → 한 칸
    return t.strip()


@dataclass
class Pair:
    """초안 하나와 그것이 실제로 나간 결과."""
    key: str
    subject: str
    similarity: float
    modified: bool
    sent_at: str = ""
    diff_sample: str = ""


@dataclass
class Verdict:
    n: int
    unmodified: int
    rate: float
    window_days: int
    pairs: list[Pair] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        """auto-send 로 올릴 **자격**이 있나 — 켜는 것은 사람이 한다."""
        return self.n >= MIN_N and self.rate >= MIN_RATE

    def report(self) -> str:
        L = [f"═══ W10 승격 게이트 (최근 {self.window_days}일)",
             f"  대조 성사 {self.n}건 · 무수정 {self.unmodified}건 · "
             f"무수정률 {self.rate * 100:.1f}%",
             f"  기준: n ≥ {MIN_N} · 무수정률 ≥ {MIN_RATE * 100:.0f}%",
             ""]
        if self.eligible:
            L.append("  ✅ **자격 충족** — 승격 여부는 사람이 정한다(이 도구는 켜지 않는다)")
        else:
            why = []
            if self.n < MIN_N:
                why.append(f"표본 부족(n={self.n} < {MIN_N})")
            if self.rate < MIN_RATE:
                why.append(f"무수정률 미달({self.rate * 100:.1f}% < {MIN_RATE * 100:.0f}%)")
            L.append(f"  ⏳ 아직 아니다 — {' · '.join(why)}")
        if self.pairs:
            L.append("")
            L.append("  ── 건별 ──")
            for p in sorted(self.pairs, key=lambda x: x.similarity):
                mark = "✏️ 수정" if p.modified else "✅ 무수정"
                L.append(f"   {mark}  유사도 {p.similarity * 100:5.1f}%  {p.subject[:46]}")
                if p.modified and p.diff_sample:
                    L.append(f"        {p.diff_sample[:150]}")
        if self.unmatched:
            L.append("")
            L.append(f"  ── 대조 실패 {len(self.unmatched)}건 (초안은 있는데 보낸 기록을 못 찾음) ──")
            L.append("     아직 안 보냈거나, 제목을 바꿔 보냈거나, 다른 계정으로 보냈다.")
            for k in self.unmatched[:6]:
                L.append(f"     · {k}")
        return "\n".join(L)


def _diff_sample(a: str, b: str, n: int = 3) -> str:
    """무엇이 바뀌었나 — 사람이 룰을 고칠 수 있게 실물을 보여준다."""
    out = []
    for line in difflib.unified_diff(a.split(". "), b.split(". "), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        out.append(line)
        if len(out) >= n * 2:
            break
    return " | ".join(out)


def measure(*, window_days: int = WINDOW_DAYS, threshold: float = 0.98,
            records=None, sent=None) -> Verdict:
    """승인 레코드 ↔ 보낸편지함을 대조해 무수정률을 낸다.

    `threshold` 는 "같은 메일로 볼 유사도" — 서명 등을 정규화한 뒤의 값이라 높게 잡는다.
    `records`·`sent` 를 주입하면 외부 호출 없이 테스트할 수 있다(시간·네트워크 주입).
    """
    from . import approval, gmail

    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()
    if records is None:
        records = [r for r in approval._iter_queue()
                   if r.get("kind") == "mail" and r.get("draft_id")
                   and str(r.get("executed_at") or "")[:10] >= cutoff]
    if sent is None:
        sent = gmail.sent_messages(days=window_days)

    by_subject: dict[str, list[dict]] = {}
    for m in sent:
        by_subject.setdefault(normalize(m.get("subject", "")), []).append(m)

    pairs: list[Pair] = []
    unmatched: list[str] = []
    for r in records:
        p = r.get("payload") or {}
        subj = normalize(str(p.get("subject", "")))
        cands = by_subject.get(subj) or []
        if not cands:
            unmatched.append(str(r.get("key", "?")))
            continue
        draft_body = normalize(str(p.get("body", "")))
        best, best_sim = None, 0.0
        for m in cands:
            sim = difflib.SequenceMatcher(None, draft_body, normalize(m.get("body", ""))).ratio()
            if sim > best_sim:
                best, best_sim = m, sim
        modified = best_sim < threshold
        pairs.append(Pair(key=str(r.get("key", "?")), subject=str(p.get("subject", ""))[:60],
                          similarity=best_sim, modified=modified,
                          sent_at=str((best or {}).get("date", "")),
                          diff_sample=_diff_sample(draft_body, normalize((best or {}).get("body", "")))
                          if modified else ""))

    n = len(pairs)
    unmod = sum(1 for p in pairs if not p.modified)
    return Verdict(n=n, unmodified=unmod, rate=(unmod / n if n else 0.0),
                   window_days=window_days, pairs=pairs, unmatched=unmatched)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="W10 승격 게이트 — 무수정률 측정 (읽기 전용)")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--threshold", type=float, default=0.98)
    args = ap.parse_args(argv)
    from . import secrets
    secrets.load()
    print(measure(window_days=args.days, threshold=args.threshold).report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
