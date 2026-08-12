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

# 서명·인용·머리말 — 사람이 "고친 것"이 아니라 메일 클라이언트가 붙이는 것.
# [적대리뷰 HIGH-1] 전에는 `re.S` 로 `\n--\n.*$` 를 썼다. 그러면 `.` 이 개행을 먹어
# **본문 중간의 구분선 뒤가 통째로** 사라진다. 남는 건 인사말뿐이고 양쪽 인사말은 늘 같아서
# **초안을 전부 새로 써도 유사도 1.0** 이 나왔다(프로브: 5건 전부 재작성 → rate 100%,
# eligible=True). 게이트가 열려선 안 될 때 열리는 것 — 이 모듈의 최악의 실패다.
# 이제 ①줄 단위 앵커로 잡고 ②마지막 구분선부터 자르고 ③너무 많이 지워지면 절단을 취소한다.
_SIG_LINE = re.compile(r"^\s*(-{2,}|_{3,})\s*$", re.M)
_HEAD_LINE = re.compile(r"^\s*(보낸\s*사람\s*:|From\s*:|On .{0,120}wrote:)\s*$", re.M)
_QUOTE = re.compile(r"^\s*>.*$", re.M)

# [2차 적대리뷰 M4] 예전엔 "40% 이상 남으면 자른다"는 **비율** 가드였다. 비율은 짧은 문서에서
# 항상 뒤집힌다 — 30자 회신에 20자 서명이 붙으면 절단이 취소되고, 그 경로만 무수정률 0% 로
# 고정돼 `all()` 때문에 전체 자격까지 막혔다("사람이 고쳤다"가 아니라 "절단이 꺼졌다"가 원인).
# 꼬리의 **절대 길이**로 바꾼다: 서명은 짧다. 길면 그건 본문이다.
# (그런데 길이만으로도 부족했다 — 바로 아래 `_SENT_END` 참조.)
_MAX_SIG = 300
# 문장 종결부호 — **본문에는 있고 서명에는 없다.** 길이만으로는 못 가른다:
# 테스트(G13)가 잡은 실패가 그것이다. 구분선 아래 120자짜리 본문("…미팅을 요청드립니다.
# 투자 규모와 일정은 논의하면서 맞춰가면 좋겠습니다. 감사합니다.")이 300자 상한 안이라
# 서명으로 잘려나갔다. 반대로 `박정현 | 카카오벤처스 투자팀 / 010-…` 은 종결부호가 없다.
_SENT_END = re.compile(r"[.!?。]")


def _cut_signature(text: str) -> str:
    """마지막 구분선/머리말 줄부터 잘라낸다 — **서명일 때만.**

    서명 판정은 두 조건을 모두 요구한다: ①꼬리가 짧고(≤300자) ②문장 종결부호가 없다.
    한쪽만 보면 각각 다르게 부러진다 —
      길이만  → 구분선 아래 짧은 **본문**(조건·일시 블록)을 통째로 지운다. 그러면
                양쪽 인사말만 남아 **초안을 전부 새로 써도 유사도 1.0** 이 된다(HIGH-1 재발).
      비율만  → 짧은 회신에서 절단이 취소돼 서명 비대칭이 남고, 그 경로 무수정률이
                0% 로 고정된다(M4).
    """
    cuts = [m.start() for m in _SIG_LINE.finditer(text)] + \
           [m.start() for m in _HEAD_LINE.finditer(text)]
    if not cuts:
        return text
    at = max(cuts)                                # 서명은 보통 맨 뒤에 붙는다
    tail = text[at:]
    if len(tail) > _MAX_SIG or _SENT_END.search(tail):
        return text                               # 길거나 문장이 있으면 본문이다
    return text[:at]


def normalize(text: str) -> str:
    """비교용 정규화 — 서명·인용·공백 차이는 수정이 아니다."""
    t = _cut_signature(text or "")
    t = _QUOTE.sub("", t)
    t = re.sub(r"\s+", " ", t)                    # 줄바꿈·연속 공백 → 한 칸
    return t.strip()


# ── HIGH-4: 글자 거리로는 못 잡는 것 ──
# 임계 0.98 은 "심사역이 자판을 얼마나 덜 두드렸나"를 잰다. 그런데 이 게이트가 물어야 할 것은
# **"자동발송했어도 맞았을까"** 다. 프로브 실측:
#     날짜 20일→27일 sim 0.9921 · 금액 5억→50억 0.9961 · '좋겠습니다'→'어렵겠습니다' 0.9882
# 심사역이 매번 미팅 시각만 고쳐도 무수정률 100% 가 나오고, 그대로 켜면 매번 틀린 시각이 나간다.
# 그래서 **의미 토큰이 다르면 유사도와 무관하게 수정**으로 본다.
#
# 🔴 [2차 적대리뷰 H1] 그런데 그 가드가 정본 템플릿을 **구조적으로 수정으로 만들었다.**
# 템플릿은 `(팀명)`·`OOO 대표님`·`OOO 심사역`·`000 드림` 네 자리표시자를 일부러 남기고
# (`meeting_mail_job.py:83` — "그대로 둔다, 발송 시 사람이 채운다"), 심사역은 보내기 전에
# **반드시** 그걸 채운다. `000` 이 숫자로 잡히니 무수정률이 영원히 0% 였다 —
# 그리고 리포트는 그 이유를 "사람이 매번 고쳤다"로 **틀리게 설명**한다.
# 자리표시자·연락처는 **양쪽에서 지우고** 비교한다. 채워 넣는 것은 수정이 아니다.
_MASKS = (
    re.compile(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}"),          # 이메일
    re.compile(r"https?://\S+"),                              # URL
    re.compile(r"\d{2,3}-\d{3,4}-\d{4}"),                    # 전화 — 서명에만 있고 초안엔 없다
    re.compile(r"\(\s*팀\s*명\s*\)"),                         # (팀명)
    re.compile(r"(?<![\dA-Za-z])(?:0{3,}|[Oo]{3,}|ㅇ{3,})(?![\dA-Za-z])"),  # 000 · OOO
)

# [2차 적대리뷰 M1] frozenset 이면 **자리바꿈을 못 잡는다** — `8월 10일 오후 3시` →
# `8월 3일 오후 10시` 가 같은 집합이라 sim 0.9952 로 무수정이었다. 순서를 보존한다.
_NUM = re.compile(r"\d[\d,.:/-]*")

# [2차 적대리뷰 M2] 어간 매칭이라 ㅂ불규칙(어렵다→어려울/어려워/어려우)에서 전부 빠졌다.
# `어려울 것 같습니다`·`힘들 것 같습니다`·`곤란합니다`·`보류하겠습니다` — 가장 흔한 거절
# 표현들이 하나도 안 걸렸다. 한국어에서 제일 흔한 부정소 `안 ~` 도 없었다.
# 반대로 `부담없이`(정본 템플릿 본문에 있다)는 오탐이었다.
_NEG_FALSE = re.compile(r"부담\s*없이|가감\s*없이|못지\s*않|어김\s*없이|다름\s*없")
_NEG = re.compile(r"않|못\s|못하|안\s|아니|없|어렵|어려[우울워]|힘들|곤란|보류|사양|반려|거절|불가")


def semantic_keys(text: str) -> tuple[tuple, tuple]:
    """의미가 걸린 토큰 — (숫자 순서열, 부정 표현 순서열).

    [2차 적대리뷰 M3] `normalize()` 가 아니라 **원문**에서 뽑는다. 서명 절단이 지운 구역도
    의미 가드는 봐야 한다 — 예전엔 두 방어선이 같은 맹점을 공유해서, 구분선 아래에 적은
    `5억원 → 50억원` 이 유사도 1.0 · 의미토큰 동일로 통과했다.
    """
    t = str(text or "")
    for m in _MASKS:
        t = m.sub(" ", t)
    t = re.sub(r"\s+", " ", t)
    return tuple(_NUM.findall(t)), tuple(_NEG.findall(_NEG_FALSE.sub(" ", t)))


@dataclass
class Pair:
    """초안 하나와 그것이 실제로 나간 결과."""
    key: str
    subject: str
    similarity: float
    modified: bool
    reason: str = ""                 # 왜 수정으로 봤나 — 유사도만으로는 설명이 안 된다
    sent_at: str = ""
    diff_sample: str = ""
    ambiguous: bool = False          # 후보가 여럿이라 짝짓기를 확신할 수 없었다


@dataclass
class Verdict:
    n: int
    unmodified: int
    rate: float
    window_days: int
    pairs: list[Pair] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)     # 본문을 못 얻어 측정 불가
    truncated: bool = False                              # 보낸편지함 조회가 잘렸다
    by_task: dict = field(default_factory=dict)          # task 별 (n, unmod)

    @property
    def ambiguous_n(self) -> int:
        return sum(1 for p in self.pairs if p.ambiguous)

    @property
    def eligible(self) -> bool:
        """auto-send 로 올릴 **자격**이 있나 — 켜는 것은 사람이 한다.

        [적대리뷰 MED-3] 조회가 잘렸으면 **측정 자체가 무효**다. 잘린 건은 `unmatched` 로
        빠져 n 만 줄이는데, 그걸 "표본 부족"으로 읽으면 원인을 틀리게 안다.
        [적대리뷰 MED-4] 하나의 전역 숫자로 판정하지 않는다 — 비플랜 드랍 메일은 본문이
        고정 템플릿이라 구조적으로 무수정률 100% 다. 그 5건으로 자격이 생기면 정작 위험한
        경로(개별 회신)에 대한 근거는 0 인 채 승격 논의가 열린다.
        """
        if self.truncated or self.ambiguous_n:
            return False
        if not self.by_task:
            return False
        return all(nn >= MIN_N and (uu / nn) >= MIN_RATE for nn, uu in self.by_task.values())

    def report(self) -> str:
        L = [f"═══ W10 승격 게이트 (최근 {self.window_days}일)",
             f"  대조 성사 {self.n}건 · 무수정 {self.unmodified}건 · "
             f"무수정률 {self.rate * 100:.1f}%",
             f"  기준: **경로별로** n ≥ {MIN_N} · 무수정률 ≥ {MIN_RATE * 100:.0f}%", ""]
        if self.truncated:
            L.append("  🔴 **측정 무효** — 보낸편지함 조회가 상한에서 잘렸다(창을 좁혀 다시 재라)")
        if self.ambiguous_n:
            L.append(f"  🔴 **측정 보류** — 짝짓기가 모호한 건 {self.ambiguous_n}건"
                     "(같은 제목·수신자의 발송이 여럿이다)")
        if self.by_task:
            L.append("  ── 경로별 ──")
            for task, (nn, uu) in sorted(self.by_task.items()):
                ok = "✅" if (nn >= MIN_N and uu / nn >= MIN_RATE) else "⏳"
                L.append(f"   {ok} {task:22} n={nn:<3} 무수정 {uu:<3} ({uu / nn * 100:.0f}%)")
        L.append("")
        L.append("  ✅ **자격 충족** — 승격 여부는 사람이 정한다(이 도구는 켜지 않는다)"
                 if self.eligible else "  ⏳ 아직 아니다")
        if self.pairs:
            L.append("")
            L.append("  ── 건별 ──")
            for pr in sorted(self.pairs, key=lambda x: x.similarity):
                mark = "✏️ 수정" if pr.modified else "✅ 무수정"
                amb = " ⚠️모호" if pr.ambiguous else ""
                L.append(f"   {mark}{amb}  유사도 {pr.similarity * 100:5.1f}%  {pr.subject[:42]}")
                if pr.modified:
                    L.append(f"        사유: {pr.reason}  {pr.diff_sample[:110]}")
        if self.invalid:
            L.append("")
            L.append(f"  ── 측정 불가 {len(self.invalid)}건 (본문을 못 얻었다 — 모수에서 제외) ──")
            L.append("     빈 본문·디코드 실패. **무수정으로 세지 않는다**(침묵을 성공으로 오독 금지).")
            for k in self.invalid[:6]:
                L.append(f"     · {k}")
        if self.unmatched:
            L.append("")
            L.append(f"  ── 대조 실패 {len(self.unmatched)}건 (초안은 있는데 보낸 기록을 못 찾음) ──")
            L.append("     아직 안 보냈거나, 제목·수신자를 바꿔 보냈거나, 다른 계정으로 보냈다.")
            for k in self.unmatched[:6]:
                L.append(f"     · {k}")
        return "\n".join(L)


def _diff_sample(a: str, b: str, n: int = 3) -> str:
    """무엇이 바뀌었나 — 사람이 룰을 고칠 수 있게 실물을 보여준다."""
    out = []
    for line in difflib.unified_diff(re.split(r"(?<=[.!?。])\s+", a),
                                     re.split(r"(?<=[.!?。])\s+", b), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        out.append(line)
        if len(out) >= n * 2:
            break
    return " | ".join(out)


def _addr(v: str) -> str:
    """수신자 주소만 뽑아 정규화 — `홍길동 <person-0fe72fbb1632@example.com>` → `person-0fe72fbb1632@example.com`."""
    m = re.search(r"[\w.%+-]+@[\w.-]+", str(v or ""))
    return m.group(0).lower() if m else ""


def _sent_at(v: str):
    """RFC2822 발송 시각 → datetime(UTC). 못 읽으면 None."""
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(str(v))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _executed_at(r: dict):
    """승인 실행 시각. `2026-08-10` 과 `2026-08-10T00:00:00Z` 두 형태를 다 받는다.

    [적대리뷰 LOW-1] 못 읽는 값을 '오래된 것'으로도 '최신'으로도 치지 않는다 — None 을
    돌려주고 호출부가 창 밖으로 뺀다. 모르는 시각을 통과시키면 창 밖의 옛 초안이
    표본에 섞인다.
    """
    from datetime import datetime as _dt
    raw = str(r.get("executed_at") or "")
    for cut, fmt in ((19, "%Y-%m-%dT%H:%M:%SZ"), (10, "%Y-%m-%d")):
        try:
            return _dt.strptime(raw[:cut], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def measure(*, window_days: int = WINDOW_DAYS, threshold: float = 0.98,
            records=None, sent=None, truncated: bool | None = None) -> Verdict:
    """승인 레코드 ↔ 보낸편지함을 대조해 무수정률을 낸다.

    [적대리뷰 HIGH-2] 짝짓기는 **(제목 + 수신자)** 로 버킷하고, 시각이 초안 생성 이후인
    것만 후보로 두고, **한 번 쓴 발송은 다시 쓰지 않는다**(greedy 1:1). 예전엔 제목만 보고
    가장 비슷한 것을 골랐고 소비가 없어서, **깨끗한 발송 1건이 모든 초안의 짝**이 됐다
    (프로브: 같은 제목 5건 중 1건만 그대로인데 rate 100%).
    후보가 여럿이면 조용히 최선치를 고르지 않고 **모호로 표시**한다 — 이 게이트에서
    조용한 최선치는 가장 위험한 기본값이다.
    """
    from . import approval, gmail

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    if records is None:
        records = []
        for r in approval._iter_queue():
            if r.get("kind") != "mail" or not r.get("draft_id"):
                continue
            # [적대리뷰 MED-5] 승인된 것만·고아 초안 제외 — 모수 정의를 느슨하게 두지 않는다
            if r.get("decision") != "approved" or r.get("orphan_draft_id"):
                continue
            ex = _executed_at(r)
            if ex is None or ex < cutoff:
                continue
            records.append(r)
    if sent is None:
        sent, truncated = gmail.sent_messages(days=window_days)
    truncated = bool(truncated)

    # (정규화 제목, 수신자) 버킷
    buckets: dict[tuple, list[dict]] = {}
    for m in sent:
        buckets.setdefault((normalize(m.get("subject", "")), _addr(m.get("to", ""))), []).append(m)
    used: set[str] = set()

    pairs: list[Pair] = []
    unmatched: list[str] = []
    invalid: list[str] = []
    by_task: dict[str, list[int]] = {}

    for r in records:
        p = r.get("payload") or {}
        key = str(r.get("key", "?"))
        task = str(r.get("task") or "?")
        draft_body = normalize(str(p.get("body") or ""))
        if not draft_body:
            invalid.append(f"{key} (초안 본문 없음)")
            continue
        want_to = _addr(p.get("founder_email") or p.get("to") or "")
        ex = _executed_at(r)
        cands = [m for m in buckets.get((normalize(str(p.get("subject") or "")), want_to), [])
                 if m.get("id") not in used]
        # 초안 생성 **이후**에 나간 것만 (1시간 여유)
        if ex:
            cands = [m for m in cands
                     if (_sent_at(m.get("date")) or now) >= ex - timedelta(hours=1)]
        if not cands:
            unmatched.append(key)
            continue

        scored = []
        for m in cands:
            if not m.get("body_ok", True) or not normalize(m.get("body") or ""):
                continue
            scored.append((difflib.SequenceMatcher(None, draft_body,
                                                  normalize(m["body"]), autojunk=False).ratio(), m))
        if not scored:
            invalid.append(f"{key} (발송 본문을 못 얻음)")
            continue
        scored.sort(key=lambda x: -x[0])
        sim, best = scored[0]
        used.add(best["id"])

        # 의미 토큰이 다르면 유사도와 무관하게 수정이다
        sem_draft = semantic_keys(str(p.get("body") or ""))
        sem_sent = semantic_keys(best.get("body") or "")
        if sem_draft != sem_sent:
            modified, reason = True, f"의미 토큰 변경 {sem_draft} → {sem_sent}"
        elif sim < threshold:
            modified, reason = True, f"유사도 {sim * 100:.1f}% < {threshold * 100:.0f}%"
        else:
            modified, reason = False, ""

        pr = Pair(key=key, subject=str(p.get("subject", ""))[:60], similarity=sim,
                  modified=modified, reason=reason, sent_at=str(best.get("date", "")),
                  ambiguous=len(scored) > 1,
                  diff_sample=_diff_sample(draft_body, normalize(best.get("body") or ""))
                  if modified else "")
        pairs.append(pr)
        b = by_task.setdefault(task, [0, 0])
        b[0] += 1
        b[1] += 0 if modified else 1

    n = len(pairs)
    unmod = sum(1 for x in pairs if not x.modified)
    return Verdict(n=n, unmodified=unmod, rate=(unmod / n if n else 0.0),
                   window_days=window_days, pairs=pairs, unmatched=unmatched,
                   invalid=invalid, truncated=truncated,
                   by_task={k: (v[0], v[1]) for k, v in by_task.items()})


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
