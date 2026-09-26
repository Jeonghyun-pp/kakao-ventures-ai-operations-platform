"""App Store 어댑터 — **판단 없는 신규진입 보드**.

방침(사용자 결정 2026-07-11): *"발굴 가치가 큰지는 생각할 필요 없다. 2주 내에 새로 들어온 앱을
나열하고 정보를 보여주면 끝."* → 이 어댑터는 **나열기**다. AI 판별을 하지 않는다.
사람이 노션에서 출시일·개발사 도메인·순위를 보고 직접 거른다.

  수집: 25개 카테고리 × 4개 차트
        무료·유료 = SSR 200위 (rank_lookup.fetch_chart)
        매출·최신출시 = iTunes RSS (여기서 직접)
  필터: ① 차트 신규 진입(과거 스냅샷에 없던 app_id)
        ② **최초 출시일 6개월 이내** ← core/judge가 담당. 이게 사실상 유일한 필터다.
        ③ 대기업/기관 denylist (보조 — 6개월 컷이 대부분 이미 잡는다)
        ④ **확실한 외국 개발사 제외** ← core/kr_origin (config `kr_only`, 2026-08-19 신설)

⚠️ 지우고 온 것들 (같은 실수 반복 금지):
- **kr_signal(한국팀 정규식 추정)**: 넓게 잡으면 424개 중 225개가 한국으로 찍히고(절반이 베트남·중국·
  터키 오탐), 좁게 잡으면 실제 한국팀 123개 중 100개를 놓친다. **정규식으로는 어느 방향으로도 안 된다.**
  → 대신 seller_url(개발사 도메인)을 그대로 노출한다. .cn/.vn/.kr은 사람이 0.5초에 판단한다.

  🔵 [2026-08-19 추기] 위 문단은 **여전히 유효하다** — 그리고 core/kr_origin 은 그 반례가 아니다.
     옛 kr_signal 이 본 건 `artistName`(법인격이 안 보이는 표시명)이었고, kr_origin 이 보는 건
     **`sellerName`(법인 실명)**이다. 후자는 그동안 rank_lookup 이 받아놓고 버리던 필드다.
       artistName "Quvarte" ↔ sellerName "Quvarte Teknoloji Anonim Sirketi"(튀르키예)
     그리고 방향이 반대다: kr_origin 은 "한국을 고르지" 않고 **"확실한 외국만 뺀다"**(3값 판정,
     unknown 은 통과). 한국식 성씨 정규식은 판정이 아니라 **veto로만** 쓴다.
     실측 199건 고정 라벨: 외국 126→105 제거(83%) · 한국 73건 유실 **0건**.
     재측정: `python tools/kr_origin_eval.py` — 규칙을 고쳤으면 반드시 돌려라.
- **genre=36(전체) 단독**: 종합 top-200에는 극초기 앱이 구조적으로 못 든다. 신규진입 29건 중 24건이
  홈플러스·배민·라인의 시즌성 재진입이었다.

세부: references/appstore.md
"""
from __future__ import annotations

import requests

from core import kr_origin
from tools import rank_lookup

from . import _util
from .base import Adapter, Candidate

# 노션 Genre(multi_select)의 **기존 옵션명과 정확히 일치**시킨다. 애플 원문("사진 및 비디오")을 그대로
# 넣으면 기존 옵션("사진비디오")과 별개로 중복 옵션이 생겨 DB가 오염된다.
GENRE_NAMES = {
    6014: "게임", 6016: "엔터테인먼트", 6008: "사진비디오", 6007: "생산성", 6017: "교육",
    6005: "소셜", 6015: "금융", 6023: "식음료", 6024: "쇼핑", 6012: "라이프스타일",
    6013: "건강피트니스", 6000: "비즈니스", 6002: "유틸리티", 6011: "음악", 6009: "뉴스",
    6003: "여행", 6018: "도서", 6004: "스포츠", 6006: "참고", 6010: "내비게이션",
    6001: "날씨", 6020: "의료", 6021: "잡지신문", 6026: "개발자도구", 6027: "그래픽디자인",
}

# 차트 4종. SSR=200위(무료·유료) / RSS=100위(매출) · 110건(최신출시).
SSR_CHARTS = ("top-free", "top-paid")
RSS_FEED = {"top-grossing": "topgrossingapplications", "new": "newapplications"}
CHART_LABEL = {"top-free": "무료", "top-paid": "유료", "top-grossing": "매출", "new": "최신출시"}

SNAP_PREFIX = "appstore"


def commit_seen(cfg: dict) -> None:
    """[2단계 커밋] land 성공 후 pipeline이 호출 — 오늘 스냅샷을 기준선으로 확정.

    land가 실패하면 호출되지 않는다 → 오늘 본 app_id가 기준선에 안 들어가고, 다음 실행에서
    다시 '신규 진입'으로 잡힌다. (유실 대신 재수집 = 안전한 방향)
    """
    n = _util.commit_snapshots(SNAP_PREFIX)
    if n:
        print(f"· appstore 스냅샷 {n}개 확정(기준선 갱신)")


class AppStoreAdapter(Adapter):
    source = "앱스토어"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"]["appstore"]
        genres = ch.get("genres", [])
        charts = ch.get("charts", ["top-free"])
        n = min(ch.get("top_n", 200), 200)
        new_only = ch.get("new_entry_only", True)
        do_enrich = ch.get("enrich", True)
        bridge = tuple(ch.get("baseline_bridge", ["appstore_board"]))
        kr_only = ch.get("kr_only", False)

        # 🔴 국적 판정의 입력(seller_name·seller_url·description)은 **전부 lookup 보강에서 온다.**
        #   enrich 가 꺼져 있으면 판정 재료가 0이고, kr_origin 은 전건 unknown 을 뱉는다
        #   → 필터가 **조용히 아무것도 안 하면서 켜져 있는 척** 한다. 이 스킬의 반복 실패 패턴이라
        #   여기서 끊고 알린다(끄지는 않는다 — 사람이 의도적으로 그렇게 뒀을 수도 있다).
        if kr_only and not do_enrich:
            print("🔴 HEALTH: appstore kr_only=true 인데 enrich=false — "
                  "판정 재료(seller_name/seller_url/description)가 없어 필터가 무력화된다.")

        out: list[Candidate] = []
        for chart in charts:
            # 최신출시(new)는 카테고리 구분이 없다 — 전체 피드 1회만 부른다(110건 하드캡).
            targets = [None] if chart == "new" else genres
            picked_total = 0
            fetched_total = 0
            newentry_total = 0                          # 그중 '차트 최초 등장'이 몇 개인가(신호용)
            noreldate_total = 0                         # 출시일을 못 얻어 버린 개수
            krdrop_total = 0                            # 국적 필터가 버린 개수
            for genre in targets:
                try:
                    apps = self._fetch(genre, chart, n)
                    self._note_fetch(True)
                except Exception as e:                 # 장르별 격리
                    # 격리는 맞다(한 장르가 죽어도 나머지는 살린다). 다만 **세어 둔다** —
                    # 안 세면 전멸했을 때도 '0건 성공'으로 보인다(2026-07-19 사고).
                    self._note_fetch(False)
                    print(f"⚠️  appstore {chart}/{genre} 수집 실패: {e}")
                    continue
                if not apps:
                    continue
                fetched_total += len(apps)

                key = f"{genre or 'all'}_{chart}"
                # [누적 seen-set] 과거 어느 스냅샷에도 없던 app_id = **차트 최초 등장**.
                # ⚠️ [2026-07-14] 이건 이제 **필터가 아니라 라벨이다**(new_entry_only: false).
                #   필터로 쓰던 시절엔 '관측 시작 이전부터 차트에 있던 젊은 앱'을 영구히 지웠다
                #   (실측 319개). 지금은 전량을 내보내고 6개월 컷 + app_id 중복제거가 거른다.
                #   스냅샷은 계속 쌓는다 — 보드의 Δ와 '진짜 신규' 배지가 이걸 쓴다.
                prev = _util.load_seen_ids(SNAP_PREFIX, key, also_prefixes=bridge)
                _util.save_snapshot(SNAP_PREFIX, key, [a["app_id"] for a in apps])

                picked = [a for a in apps if a["app_id"] not in prev] if (new_only and prev) else \
                         ([] if new_only else apps)
                if new_only and not prev:
                    print(f"⚠️  HEALTH: {key} 기준선 없음 — 오늘 기준선만 저장, 신규진입은 다음 실행부터")
                if not picked:
                    continue
                if do_enrich:
                    rank_lookup.enrich(picked)         # 최초 출시일·평점·리뷰수·설명·seller_url
                # 🔴 전체 차트를 훑는 모드(new_only=False)에서는 **출시일 미상을 버린다.**
                #   L0(judge)는 미상을 통과시킨다 — 입력이 '신규진입'뿐일 땐 그게 안전한 recall
                #   정책이었다. 그런데 입력이 **차트 전체**가 되면 얘기가 뒤집힌다: lookup이
                #   레이트리밋/장애로 실패하는 순간 나이를 모르는 앱 **수천 개**가 미상 통과로
                #   노션에 쏟아진다. 검증 못 한 것은 내보내지 않는다.
                if not new_only:
                    before = len(picked)
                    picked = [a for a in picked if a.get("released_at")]
                    noreldate_total += before - len(picked)

                # 🔴 [2026-08-19] 국적 필터. **확실한 외국만** 버린다(core/kr_origin 참조).
                #   판정은 여기서 한 번만 하고 결과를 앱 dict에 실어 둔다 — _to_candidate 가
                #   근거 문자열을 match_reason 에 붙여야 사람이 노션에서 "왜 남았나"를 본다.
                for a in picked:
                    a["kr_verdict"], a["kr_reason"] = self._origin(a)
                if kr_only:
                    before = len(picked)
                    picked = [a for a in picked if a["kr_verdict"] != "foreign"]
                    krdrop_total += before - len(picked)

                for a in picked:
                    out.append(self._to_candidate(a, genre, chart,
                                                  is_new=a["app_id"] not in prev))
                newentry_total += sum(1 for a in picked if a["app_id"] not in prev)
                picked_total += len(picked)

            label = CHART_LABEL.get(chart, chart)
            # 🔴 "받긴 받았는데 emit이 0" = 죽은 층이 **살아 있는 척** 하는 것. 이 스킬의 반복 실패 패턴.
            #
            # ── 최신출시(new) 피드에 대한 사실관계 정정 (2026-07-14 재실측) ──
            #   옛 주석은 "애플이 폐기했다 · 되살릴 방법이 없다"고 단정했다. **과했다.** 스냅샷 실측:
            #     07-08 → 07-11 : 106개 중 **102개 교체** (멀쩡히 돌고 있었다)
            #     07-11 → 07-14 : 새 app_id **0개** (4일째 정지)
            #   → 확실한 건 "4일째 갱신 정지"뿐이다. 영구 폐기인지 일시 정지인지는 **모른다.**
            #   → 그리고 이 층이 0건이던 **진짜 원인은 피드가 아니라 우리 설계였다**: new_entry_only가
            #     첫 실행에서 110개를 통째로 '기준선'으로 삼켜버려 emit이 0이 됐다. 그 110개는
            #     노션에 **한 번도** 안 올라갔다. new_entry_only: false 로 해결됐다.
            #   ⚠️ 장르 파라미터가 무시되는 건 사실이다(실측: 전체=게임=금융 결과 동일).
            # 🔴 국적 필터가 **과하게 먹었는지** 본다. 실측 기준선: 외국 판정률 63%
            #   (W34 204건 중 확실한 외국 109건). 이게 90%를 넘으면 필터가 아니라 사고다 —
            #   lookup 이 설명을 못 가져와 한글비율 0으로 전건 외국 처리되는 경로가 실재한다.
            #   (kr_origin 이 짧은 설명은 판정 안 하도록 막아두긴 했지만, 방어를 두 겹으로 둔다.)
            if kr_only and krdrop_total:
                rate = krdrop_total / (krdrop_total + picked_total)
                if rate > 0.90:
                    print(f"🔴 HEALTH: appstore {label} — 국적 필터가 {rate:.0%}를 버렸다"
                          f"({krdrop_total}건). 평시 ~63%. lookup 장애 의심 — 확인 전엔 믿지 마라.")

            if fetched_total and not picked_total:
                print(f"🔴 HEALTH: appstore {label} — {fetched_total}건을 받았는데 emit **0건**. "
                      f"피드 정지 또는 lookup 전면 실패 의심"
                      + (f" (국적 필터가 {krdrop_total}건 버림)." if krdrop_total else "."))
            elif not new_only:
                # 🔴 lookup이 대량 실패하면 '출시일 미상'이 폭증하고, 미상은 버려진다
                #   → **수집이 성공한 척하며 아무것도 안 준다.** 평시 미상 비율은 0%다(실측 4,971/4,971).
                lost = noreldate_total / fetched_total if fetched_total else 0
                if lost > 0.05:
                    print(f"🔴 HEALTH: appstore {label} — 출시일 미상 {noreldate_total}건"
                          f"({lost:.0%}). lookup 장애/레이트리밋 의심 — 이만큼이 그냥 버려졌다.")
                extra = f" · 출시일 미상 {noreldate_total}건 버림" if noreldate_total else ""
                extra += f" · 외국 {krdrop_total}건 버림" if krdrop_total else ""
                print(f"✅ appstore {label}: {picked_total}건 "
                      f"(그중 차트 최초등장 {newentry_total}건{extra})")
            else:
                kr = f" · 외국 {krdrop_total}건 버림" if krdrop_total else ""
                print(f"✅ appstore {label}: 신규진입 {picked_total}건{kr}")

        return _dedupe_by_app(out)

    # ---------- 국적 판정 ----------
    @staticmethod
    def _origin(a: dict) -> tuple[str, str]:
        """(verdict, reason). enrich 가 안 돌았으면 재료가 없어 항상 unknown = 통과."""
        return kr_origin.classify(
            seller_name=a.get("seller_name", "") or a.get("developer", ""),
            seller_url=a.get("seller_url", ""),
            app_name=a.get("name", ""),
            description=a.get("description", "") or a.get("subtitle", ""),
        )

    # ---------- 차트 소스 분기 ----------
    def _fetch(self, genre: int | None, chart: str, n: int) -> list[dict]:
        if chart in SSR_CHARTS:
            return rank_lookup.fetch_chart(genre, chart)[:n]
        return self._rss(genre, RSS_FEED[chart])

    @staticmethod
    def _rss(genre: int | None, feed: str) -> list[dict]:
        """iTunes RSS. 최신출시(newapplications)는 genre 파라미터가 무시되고 110건 하드캡이다."""
        g = f"/genre={genre}" if genre else ""
        url = f"https://itunes.apple.com/kr/rss/{feed}/limit=200{g}/json"
        # 절전 해제 직후 DNS 미기동 대비 재시도(_util 주석 참조 — 07-19·20 이틀 전멸의 원인)
        r = _util.get_with_retry(url, headers={"User-Agent": rank_lookup.UA}, timeout=30)
        r.raise_for_status()
        out = []
        for rank, e in enumerate(r.json().get("feed", {}).get("entry", []), 1):
            out.append({
                "rank": rank,
                "app_id": e["id"]["attributes"]["im:id"],
                "name": e["im:name"]["label"],
                "developer": e.get("im:artist", {}).get("label", ""),
                "url": (e.get("id") or {}).get("label", ""),
            })
        return out

    # ---------- Candidate 매핑 ----------
    def _to_candidate(self, a: dict, genre: int | None, chart: str,
                      is_new: bool = True) -> Candidate:
        """is_new = 이 app_id가 **차트에 처음 등장**했나(누적 스냅샷 대조).

        🔴 new_entry_only를 끈 뒤엔 emit 대상 대부분이 '이미 차트에 있던 앱'이다.
           그런데 예전 코드는 **전부** `chart_new_entry: True` · match_reason "…신규진입"으로
           박았다 → 노션이 **거짓말을 하게 된다**. 라벨을 실제 값으로 채운다.
        """
        app_id = a["app_id"]
        gname = GENRE_NAMES.get(genre, "전체") if genre else "전체"
        cname = CHART_LABEL.get(chart, chart)
        return Candidate(
            source=self.source,
            name=a.get("name", ""),
            url=a.get("url") or f"https://apps.apple.com/kr/app/id{app_id}",
            one_liner=a.get("subtitle", "") or (a.get("description", "") or "")[:120],
            founder=a.get("developer", ""),
            released_at=a.get("released_at", ""),
            raw_text=(a.get("description") or a.get("subtitle") or "")[:2000],
            app_id=app_id,
            # 개발사 도메인. 한국팀 판별을 정규식으로 하지 않는 대신 **원자료를 그대로 보여준다.**
            homepage=a.get("seller_url", ""),
            signals={
                "channel": "App Store",
                "genre": [gname],                      # 노션 multi_select — 반드시 리스트
                "chart": chart,
                "rank": a.get("rank"),
                "chart_new_entry": is_new,
                # Entry Type 옵션: chart_new(차트 최초등장) · chart_existing(이미 차트에 있던 앱) ·
                #   new_release(최신출시 피드) — chart_existing 은 07-14 신설(notion_migrate 필요).
                "entry_type": ("new_release" if chart == "new"
                               else ("chart_new" if is_new else "chart_existing")),
                "sourcing_method": "appstore_chart_kr",
                "search_mode": "CHART",
                "search_type": ("NEW_RELEASE" if chart == "new"
                                else ("CHART_NEW" if is_new else "TOP")),
                # 국적 판정 근거를 **문장에 남긴다.** kr_only 를 켜면 남은 행은 전부
                #   kr 아니면 unknown 인데, 둘의 뜻이 전혀 다르다("한국 확인" vs "판정 못 함").
                #   구분이 안 보이면 사람이 unknown 뭉치를 한국으로 오해한다.
                "match_reason": (f"{gname} · {cname} {a.get('rank')}위 "
                                 + ("신규진입" if is_new else "차트 진입")
                                 + (f" · 국적 {a['kr_verdict']}({a['kr_reason']})"
                                    if a.get("kr_verdict") else "")),
                "kr_verdict": a.get("kr_verdict"),
                "kr_reason": a.get("kr_reason"),
                # 노션에 `Korea Signal`(multi_select) 열이 있으면 자동으로 채워진다.
                # 없으면 notion.py 의 스키마 루프가 조용히 건너뛴다 — DB 오염 없음.
                "korea_signal": ([a["kr_verdict"]] if a.get("kr_verdict") else []),
                "seller_name": a.get("seller_name", ""),
                "seller_url": a.get("seller_url", ""),
                "artwork": a.get("artwork") or a.get("artwork_url"),
                "rating": (a.get("avg_rating") if a.get("avg_rating") is not None
                           else a.get("rating")),
                "review_count": a.get("review_count") or a.get("rating_count"),
                "updated_at": a.get("updated_at"),
            },
        )


def _dedupe_by_app(cands: list[Candidate]) -> list[Candidate]:
    """같은 앱이 여러 장르·차트에 동시 진입 → 하나로 병합. 어느 장르/차트였는지는 보존."""
    seen: dict[str, Candidate] = {}
    for c in cands:
        prev = seen.get(c.app_id)
        if prev is None:
            seen[c.app_id] = c
            continue
        for g in c.signals.get("genre", []):
            if g not in prev.signals.setdefault("genre", []):
                prev.signals["genre"].append(g)
        # 여러 차트에 동시 진입한 앱은 그 사실 자체가 신호 → match_reason에 누적
        add = c.signals.get("match_reason", "")
        if add and add not in prev.signals.get("match_reason", ""):
            prev.signals["match_reason"] += f" / {add}"
    return list(seen.values())
