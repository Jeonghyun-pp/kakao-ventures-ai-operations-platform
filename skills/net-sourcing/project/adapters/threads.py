"""Threads 어댑터 — 3층 수집(2026-07-09 계획). 키워드 단독의 recall 천장을 비키워드
발굴층으로 보강한다. adapters는 필터하지 않는다 — 슬롭 컷 등은 core/judge.

  ① keyword          : burbn/threads-search-scraper 키워드 검색(또는 공식 Graph API).
                       "누가 무슨 말을 했나"로 찾음. 시드 단어를 쓴 글만 잡힘(구조적 한계).
  ①-b watchlist      : 이미 찾은 빌더의 새 글을 계속 봄(burbn `from`). **발굴이 아니라 추적**이다 —
                       진입 조건이 "키워드 층에 이미 잡힘"이라 신규 발굴은 원리적으로 0이다.
                       ②의 대체재가 아니다(그렇게 착각해서 ②를 접었었다).
  ② following_graph  : 시드 계정(진성 빌더) → 팔로잉 1홉 확장 → 각 계정 최근 글.
                       "누구와 연결됐나"로 찾음. **단어를 안 써도 잡힘** = 키워드 천장을 뚫는 층.
  ③ replies          : 앵커 포스트("요즘 뭐 만드세요?"류)의 댓글 수집 → 리플라이로만 런칭 알리는
                       미접촉 창업자 포착. 앵커는 keyword 층 결과에서 **자동 수확**한다.

층 on/off = config.channels.threads.layers. 액터 slug는 config.channels.threads.actors 에서 주입.

액터 스키마 (2026-07-12 라이브 GET으로 확정 — 추측 아님):
  burbn~threads-search-scraper          required=['searchQuery'] · {sort,from,before,after,maxPosts}
  igview-owner~threads-user-following…  required=['username']    · {max_following}  ← **로그인 불필요**
  futurizerush~threads-replies-scraper  required=['post_urls']   · post_urls=**객체 배열**[{url}]
                                        · max_replies 10~50 · include_nested_replies
출력 스키마는 액터마다 다르다 → _post_to_candidate가 폴백으로 흡수하고, 전부 빗나가면
_health()가 **실제 키를 로그에 찍는다**(키 이름을 추측하다 세 번 당했다).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

from . import apify
from .base import Adapter, Candidate, norm_post_url

GRAPH = "https://graph.threads.net/v1.0/keyword_search"
PROJ = Path(__file__).resolve().parent.parent


class ThreadsAdapter(Adapter):
    source = "스레드"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"]["threads"]
        layers = ch.get("layers", ["keyword"])
        kw = self._threads_keywords()
        out: list[Candidate] = []

        # ★ [2026-07-20] 층별 주기(cadence). 전엔 `layers`에 있으면 **무조건 매 실행** 돌았고,
        #   "격주"를 표현할 자리가 없어 사람이 config에서 층을 지웠다 넣었다 해야 했다
        #   (잊으면 조용히 매주 과금으로 복귀 — 로그는 정상으로 보인다).
        #   링크드인 축(`linkedin.py::_axis_due`)과 **같은 어휘**를 쓴다(core/cadence.py 정본).
        state = self._load_layer_state()
        ran: list[str] = []

        def _run(name: str, fn, *args) -> None:
            if name not in layers:
                return
            if not self._layer_due(name, ch, state):
                return                                  # 사유는 _layer_due가 출력한다
            try:
                out.extend(fn(*args))
                ran.append(name)                        # 예외 없이 끝난 층만 후보
            except Exception as e:
                print(f"⚠️  threads {name} 층 실패: {e}")

        _run("keyword", self._collect_keyword, ch, kw.get("keyword", []))
        _run("watchlist", self._collect_watchlist, ch, kw.get("watch_accounts", []))
        _run("following_graph", self._collect_following, ch,
             kw.get("following_seed_accounts", []))
        _run("replies", self._collect_replies, ch, kw.get("reply_anchor_posts", []))

        # ★ 층의 last_run은 **예산이 안 터진 경우에만** 찍는다.
        #   예산 소진으로 뒤쪽 층이 차단됐는데 "돌았다"고 기록하면, 격주 층은 **다음 2주도 건너뛰어
        #   4주간 사라진다.** 그런데 로그는 조용하다 — 백필 커서를 안 전진시키는 아래 규율과 같은 이유다.
        if ran and not apify.budget_tripped():
            self._mark_layers_run(ran, state)
        elif ran:
            print(f"· threads: 예산 소진 — 층 주기({', '.join(ran)})를 기록하지 않는다"
                  "(다음 실행이 다시 돈다)")
        # ★ 백필 커서는 **정상적으로 끝난 날만** 전진시킨다.
        #   예산이 중간에 터져 뒤쪽 층이 통째로 차단됐는데도 "오늘 수집 성공"으로 기록하면,
        #   못 가져온 구간이 백필 창 밖으로 밀려나 **영영 유실**된다.
        if out and not apify.budget_tripped():
            self._mark_success()      # 실제로 뭔가 가져온 날만 = 백필 창의 왼쪽 끝
        elif out:
            print("· threads: 예산 소진으로 일부 층이 차단됐다 — 백필 커서를 전진시키지 않는다"
                  "(다음 실행이 이 구간을 다시 긁는다)")
        return out

    def _threads_keywords(self) -> dict:
        """keywords.yaml threads: 신 스키마(dict) 우선, 구 스키마(list)면 keyword로 승격."""
        kw = self.keywords.get("threads", {})
        if isinstance(kw, list):
            return {"keyword": kw}
        return kw or {}

    # ================= ① 키워드 백본 =================
    def _collect_keyword(self, ch: dict, keywords: list[str]) -> list[Candidate]:
        if not keywords:
            print("· threads keyword 층: 키워드 없음 → skip")
            return []
        mode = ch.get("mode", "apify")
        if mode == "official":
            return self._collect_official(keywords)
        slug = ch.get("actors", {}).get("keyword", "burbn~threads-search-scraper")
        max_posts = ch.get("max_posts", 40)
        # 코드 기본값이 config와 반대였다(3 vs 2, True vs false). config가 이기니 동작은 같았지만,
        # config에서 키를 지우는 순간 조용히 다른 정책으로 돈다. 기본값을 config에 맞춘다.
        max_age = ch.get("max_post_age_days", 2)   # 오래된 글 드롭 (아래 참조)
        drop_replies = ch.get("drop_replies", False)

        # ★ 이 채널은 **매일** 돈다 (사용자 결정 2026-07-11).
        #   max_posts=40 캡은 실측상 **하루치**에서 이미 찬다:
        #     만들었습니다 52건/0일 · Day1 45건/0일 · 출시했습니다 51건/1일 · 런칭했어요 39건/1일
        #   즉 주 1회 실행은 "주간 파이프라인"이 아니라 **일간 스냅샷**이었다. 매일 돌려 DB에
        #   누적하면 같은 캡으로 7일 전체를 덮는다. (max_posts를 300으로 올리는 것보다 싸다 —
        #   Apify는 아이템당 과금이라 저볼륨 키워드가 캡을 안 채우는 만큼 절약된다.
        #   사이드프로젝트는 하루 0.6건뿐이라 주 1회면 65일치 옛 글로 캡을 채워 돈을 버렸다.)
        # ★ `after` — **서버측 날짜 컷.** burbn 스키마에 있는데 안 쓰고 있었다.
        #   Apify는 **아이템당 과금**이라, 낡은 글을 받아서 클라이언트에서 버리면 **이미 낸 돈**이다.
        #   after를 보내면 액터가 애초에 최신 글만 준다 — 돈도 아끼고 데이터도 신선하다.
        #   (클라이언트 컷 _too_old는 폴백으로 남긴다 — after가 무시될 수 있으니.)
        after = self._after_date(ch)

        seen: dict[str, Candidate] = {}
        dupes = stale = replies = 0
        empty_text = 0
        last_items: list[dict] = []      # 헬스 경보 때 **실제 키**를 찍기 위해 보존
        for q in keywords:
            payload = _search_payload(slug, q, max_posts, after)
            try:                                   # 쿼리별 격리 — 한 쿼리 실패가 층 전체를 막지 않음
                items = apify.run_actor(slug, payload)
            except Exception as e:
                print(f"⚠️  threads keyword '{q}' 실패: {e}")
                continue
            if items:
                last_items = items
            for p in items:
                # ⚠️ `text_content`가 빠져 있었다 — meta-threads 액터가 쓰는 키다.
                #   스왑하면 **정상 파싱된 글이 전부 '빈 글'로 집계돼** 30% 드리프트 오경보가 뜬다.
                #   _post_to_candidate의 폴백 체인과 **반드시 같은 키 목록**이어야 한다.
                if not (p.get("captionText") or p.get("text") or p.get("text_content")
                        or p.get("caption") or p.get("content")):
                    empty_text += 1                # 본문 파싱 실패 감지 (액터 스왑 시 조기 경보)
                c = self._post_to_candidate(
                    p, sourcing_method="keyword_search", search_mode="KEYWORD",
                    search_type="RECENT", query=q, is_reply=False, layer="keyword")
                # ★ [SKILL.md CRITICAL #2] 이건 **품질 필터가 아니라 신선도 경계**다. 지우지 마라.
                #   · 낡은 글은 "실시간 신호"가 아니다(이 채널의 존재 이유가 '먼저 발견'이다).
                #   · 저볼륨 키워드는 캡을 낡은 글로 채운다(실측: 사이드프로젝트=65일치 옛 글).
                #   · Apify는 **아이템당 과금**이라, 받아서 여기서 버리면 **이미 낸 돈**이다.
                #     그래서 서버측 `after`가 항상 우선이고, 이 클라이언트 컷은 after가 무시될 때의 폴백이다.
                #   🔴 폴백은 **`after`와 같은 창**을 써야 한다. 예전엔 여기만 항상 "오늘-2일"이라,
                #     쉬었다 돌리면 백필로 산 글을 그대로 버렸다(`_too_old` 주석 참조).
                if after and _too_old(c.released_at, after):
                    stale += 1
                    continue
                # ⚠️ 이건 **품질 필터**다 → 그래서 지금 꺼져 있다(config drop_replies: false).
                #   "리플 전용 작성자 12명이 전원 노이즈"라는 이유로 리플 38/247을 통째로 버렸는데,
                #   그 안에 진성 창업자 @jakecoldbyte 가 있었다. 어댑터가 품질로 자르면
                #   **근거도 없이 조용히 사라진다.** 켜지 마라 — 판단은 judge와 사람이 한다.
                if drop_replies and c.signals.get("is_reply"):
                    replies += 1
                    continue
                key = c.url or f"{c.founder}:{(c.raw_text or '')[:80]}"
                prev = seen.get(key)
                if prev is None:
                    seen[key] = c
                    continue
                dupes += 1
                mk = prev.signals.setdefault("matched_keyword", [])
                if q not in mk:                    # 어느 키워드들에 걸렸는지 = 신호 강도
                    mk.append(q)
        out = list(seen.values())
        print(f"✅ threads keyword: {len(out)}건 · 쿼리 {len(keywords)}개 "
              f"(중복 {dupes} 병합 · {after or '컷없음'} 이전 {stale} 드롭 · 리플 {replies} 드롭)")
        # ★ 파싱 실패 조기 경보 — **조용히 넘기지 않는다.**
        #   링크드인에서 세 번 당한 함정("액터가 주는 키 이름을 추측")을 여기서 막는다.
        #   액터를 스왑하면 출력 스키마가 달라져 본문이 통째로 빌 수 있는데, 그때 로그가
        #   "✅ 300건 수집"만 찍으면 며칠을 모른 채 빈 껍데기를 노션에 쌓게 된다.
        if not out:
            print("🔴 HEALTH: threads keyword 0건 — 쿼리 전부 실패했거나(쿼터/403) 액터가 죽었다. "
                  "위 ⚠️ 로그를 확인하라. (0건은 '오늘 아무도 안 만들었다'는 뜻이 아니다)")
        if out and empty_text > len(out) * 0.3:
            print(f"🔴 HEALTH: 본문이 빈 글 {empty_text}건 — 액터 출력 스키마가 바뀌었을 수 있다. "
                  f"_post_to_candidate의 필드명을 실제 반환값과 대조하라.")
        # ★ 작성자 누락도 잡는다. 스레드는 **인물 1행**이라 작성자가 없으면 그 글은 통째로 버려진다
        #   (fold가 핸들 없는 후보를 폐기한다). 본문만 보고 있어서 이 구멍이 열려 있었다.
        no_author = sum(1 for c in out if not c.founder)
        if out and no_author > len(out) * 0.3 and last_items:
            print(f"🔴 HEALTH: 작성자가 빈 글 {no_author}/{len(out)}건 — 이 글들은 인물 접기에서 "
                  f"**폐기된다.** 액터 실제 키: {sorted(last_items[0].keys())[:12]} "
                  f"→ _post_to_candidate의 username 폴백에 추가하라.\n"
                  f"   (원본은 data/raw/ 에 남아 있다 — 파서만 고치면 **재수집 없이** 재처리된다)")
        self._harvest_watchlist(ch, out)
        self._harvest_anchors(ch, out)     # ③층 앵커 풀을 스스로 키운다
        return out

    # ---- 워치리스트 자동 등록 ----
    def _harvest_watchlist(self, ch: dict, cands: list[Candidate]) -> None:
        """**위조 불가 어휘를 쓴 사람만** 워치리스트에 자동 등록한다.

        "앱스토어 심사", "비공개 테스터", "테스트플라이트", "심사 리젝" 같은 말은
        **실제로 앱을 내보지 않으면 쓸 수 없다.** 뉴스 계정도 쇼핑몰도 강의팔이도 못 쓴다.
        → 이 말을 쓴 사람은 거의 확실히 진짜 빌더다. 그 사람의 **이후 모든 글**을 계속 본다.
        (그냥 착지한 사람을 전부 넣으면 명단이 노이즈로 차서 감시가 무의미해진다)
        """
        triggers = set(ch.get("watchlist_triggers") or [])
        if not triggers:
            return
        watch = self._load_watchlist()
        have, added = set(watch), 0
        for c in cands:
            # 🔴 [2026-07-13] 예전엔 **어떤 쿼리가 이 글을 물어왔나**로 판정했다. 그게 틀렸다:
            #   액터는 구(句)가 아니라 토큰으로 매칭한다. "앱스토어 심사"로 검색하면 '심사'가 든
            #   재건축 안전진단·시청 공약점검·네일샵 글이 딸려온다. 그 사람들이 전부 "위조 불가
            #   어휘를 쓴 빌더"로 자동 등록됐고, 워치리스트 103명 중 **97%가 가짜**였다(실측).
            #   → 판정 기준은 **글 본문에 그 말이 실제로 있는가**다. 쿼리는 증거가 아니다.
            txt = _norm_phrase(c.raw_text or "")
            if not any(_norm_phrase(t) in txt for t in triggers):
                continue
            u = c.founder or ""
            if u and u not in have:
                watch.append(u)
                have.add(u)
                added += 1
        if added:
            # 파일엔 **자동 등록분만** 쓴다. 큐레이션(keywords.yaml)은 _load_watchlist가 매번
            # 앞에 붙이므로, 파일에 섞어 넣으면 나중에 명단에서 빼도 파일에 눌러앉아 안 지워진다.
            curated = set((self.keywords.get("threads") or {}).get("watch_accounts") or [])
            auto = [h for h in watch if h not in curated]
            self.WATCHLIST.parent.mkdir(parents=True, exist_ok=True)
            self.WATCHLIST.write_text(json.dumps(auto, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
            print(f"· threads 워치리스트 자동등록 +{added}명 "
                  f"(큐레이션 {len(curated)} + 자동 {len(auto)}) — 본문에 위조 불가 어휘가 실제로 있는 사람")

    LAST_OK = PROJ / "data" / "threads_last_success.json"

    def _after_date(self, ch: dict) -> str:
        """burbn `after` 파라미터용 날짜 = **마지막 성공 수집일까지 되짚는다**(다운타임 백필).

        ★ 이게 없으면 채널이 멈춰 있던 구간이 **영구 유실**된다. 실제 사고:
          Apify 한도 초과로 07-11부터 스레드가 죽었는데, `after = 오늘 - 2일`이라
          결제가 5일 뒤에 풀리면 그 사이 올라온 창업자 글은 다시는 수집되지 않는다.
          (매일 도는 채널은 '어제부터 오늘까지'만 보면 되지만, **안 돈 날이 있으면 그 전제가 깨진다.**)

        → after = min(오늘-max_post_age_days, 마지막 성공일)  단 backfill_cap_days로 상한.
          상한을 두는 이유: 한 달 멈춘 뒤 재개하면 한 달치를 한 번에 사서 다시 한도를 태운다.
        """
        from datetime import date, timedelta
        days = int(ch.get("max_post_age_days") or 0)
        if not days:
            return ""
        normal = date.today() - timedelta(days=days)
        last_ok = self._load_last_success()
        if not last_ok or last_ok >= normal:
            return normal.isoformat()
        cap = date.today() - timedelta(days=int(ch.get("backfill_cap_days", 14)))
        target = max(last_ok, cap)
        gap = (date.today() - last_ok).days
        print(f"· threads 백필: 마지막 성공 수집이 {last_ok}({gap}일 전) → after={target} 로 되짚는다"
              + (f" (상한 {ch.get('backfill_cap_days', 14)}일에 걸림 — 그 이전 글은 유실)"
                 if target == cap and cap > last_ok else ""))
        return target.isoformat()

    def _load_last_success(self):
        from datetime import date
        if not self.LAST_OK.exists():
            return None
        try:
            return date.fromisoformat(json.loads(self.LAST_OK.read_text(encoding="utf-8"))["date"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def _mark_success(self) -> None:
        """수집이 실제로 뭔가 가져온 날만 기록. 0건(403/장애)인 날은 기록하지 않는다 —
        기록해버리면 그날이 '성공'이 되어 백필 창이 닫히고 그 구간이 영구 유실된다."""
        from datetime import date
        self.LAST_OK.parent.mkdir(parents=True, exist_ok=True)
        self.LAST_OK.write_text(json.dumps({"date": date.today().isoformat()}),
                                encoding="utf-8")

    # ---- 층별 주기(cadence) — 링크드인 `linkedin_axis_state.json`의 스레드판 --------------
    LAYER_STATE = PROJ / "data" / "threads_layer_state.json"

    @classmethod
    def _load_layer_state(cls) -> dict:
        if not cls.LAYER_STATE.exists():
            return {}
        try:
            return json.loads(cls.LAYER_STATE.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            return {}                                   # 깨졌으면 전 층 1회 실행(=도는 쪽)

    @staticmethod
    def _layer_due(layer: str, ch: dict, state: dict) -> bool:
        """이 층을 이번 실행에 돌려야 하나. 안 돌면 **이유를 반드시 출력한다** —
        조용히 안 도는 층은 아무도 눈치채지 못한다(버그로그 패턴 '조용히 사라짐')."""
        from core import cadence as cad

        cadences = ch.get("layer_cadence") or {}
        name = cadences.get(layer, "always")             # 선언 안 된 층 = 현행 유지(매 실행)
        last = (state.get(layer) or {}).get("last_run", "")
        if cad.interval_elapsed(name, last, where=f"threads:{layer}"):
            return True
        nxt = cad.gap_days(name, where=f"threads:{layer}")
        print(f"· threads {layer} 층: 주기 미도래 → skip "
              f"(cadence={name}/{nxt}일, 마지막 실행 {last})")
        return False

    def _mark_layers_run(self, layers: list[str], state: dict) -> None:
        from datetime import date
        today = date.today().isoformat()
        for name in layers:
            state.setdefault(name, {})["last_run"] = today
        self.LAYER_STATE.parent.mkdir(parents=True, exist_ok=True)
        self.LAYER_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    # ================= ①-b 워치리스트 (팔로잉그래프의 대체재) =================
    WATCHLIST = PROJ / "data" / "threads_watchlist.json"

    def _collect_watchlist(self, ch: dict, seeds: list[str]) -> list[Candidate]:
        """**한 번 발견한 빌더의 새 글을 계속 본다.**

        ⚠️ **이 층은 발굴이 아니라 추적이다.** 명단에 있는 사람은 정의상 이미 노션에 있으므로
           신규 인물은 **0명이 정상**이다. 가치는 "이미 아는 빌더가 오늘 뭘 했나"(새 글 = 신호).
           이걸 발굴 층으로 세면 "주 N명 발굴" 숫자가 부풀려진다.

        명단 = 수동 시드(keywords.yaml `watch_accounts`) + 자동 등록(아래 _harvest_watchlist).
        🔴 명단이 **무한 증가**하고 burbn은 계정당 런 1회($0.16)라, 100명이 되면 이 층만 $16/런이다.
           → `watchlist_max_accounts`로 상한을 두고 커서로 돌린다.
        """
        watch = self._load_watchlist()
        # ★ [2026-07-17 팔로업] 로스터 조립 — sources 순서가 곧 우선순위다(캡 로테이션에서 앞이 생존).
        #   notion = Tier=Catch(judge) 또는 타깃 ✅(사람)인 스레드 인물 — "만나고 싶은 소수"를
        #   여기서 소비한다(판단은 judge/사람이 이미 했다). 조회 실패 시 경고 후 나머지로 진행.
        #   🔴 조립은 **여기(소비 지점)서만** 한다 — _load_watchlist에 섞으면 _harvest_watchlist의
        #     쓰기 경로가 노션 로스터를 auto 파일에 눌러앉힌다(Tier가 바뀌어도 안 빠지는 오염).
        #   롤백 = config `watchlist_roster.sources: [curated, auto]` 한 줄.
        ro = ch.get("watchlist_roster") or {}
        sources = list(ro.get("sources") or ["curated", "auto"])
        notion_roster: list[str] = []
        if "notion" in sources:
            try:
                from core import notion
                notion_roster = notion.watch_roster_handles(self.cfg)
                print(f"· threads watchlist 로스터(노션): Tier/타깃 {len(notion_roster)}명")
            except Exception as e:
                print(f"⚠️ watchlist 노션 로스터 조회 실패: {e} — 큐레이션+자동 명단으로 진행")
        pool = {"curated": list(seeds), "notion": notion_roster, "auto": watch}
        accounts = list(dict.fromkeys(h for s in sources for h in pool.get(s, []) if h))
        if not accounts:
            print("· threads watchlist 층: 명단 없음 → skip")
            return []

        cap = int(ch.get("watchlist_max_accounts", 12))
        if len(accounts) > cap:                  # 커서로 돌린다 — 매번 앞에서 12명만 보면 뒤는 영영 안 본다
            # 🔴 [2026-07-13] **로테이션은 글을 잃는다.** 캡 12는 burbn 시절 화석이다
            #   (그땐 계정당 런 1회 = $0.16이라 캡이 곧 돈이었다). 지금 meta는 **20명을 한 런**에
            #   $0.27로 처리한다 — 캡을 올리는 비용이 사실상 없다.
            #   왜 잃는가: 한 사람을 (명단÷캡)번에 한 번 보는데, 액터는 **최근
            #   watchlist_posts_per_account(=10)편**만 준다. 그 사이 10편 넘게 쓰면 넘친 만큼
            #   **영영 못 본다**(이 층엔 날짜 컷이 없어 백필로도 못 줍는다).
            #   수동 실행이면 '한 바퀴'가 몇 주다 → 손실이 커진다.
            #   → **캡은 명단 전체를 덮어야 한다.** 못 덮으면 조용히 넘어가지 말고 크게 찍는다.
            print(f"🔴 워치리스트 캡({cap}) < 명단({len(accounts)}) — 로테이션이 돈다.\n"
                  f"   한 사람을 {len(accounts) / cap:.1f}회에 한 번만 보는데 액터는 최근 "
                  f"{int(ch.get('watchlist_posts_per_account', 10))}편만 준다 → 그 사이 더 쓰면 **글이 유실된다.**\n"
                  f"   → config.channels.threads.watchlist_max_accounts 를 {len(accounts)} 이상으로 올려라 "
                  f"(meta는 20명당 런 1회 ≈ $0.27, 거의 공짜다).")
            cur = self._watch_cursor()
            picked = [accounts[(cur + i) % len(accounts)] for i in range(cap)]
            self._save_watch_cursor(cur + cap)
            accounts = picked

        # ★ 워치리스트는 "특정 유저의 글"을 가져오는 층이다 → **user_posts 액터**를 써야 한다.
        #   keyword 액터(burbn)를 쓰고 있었는데, burbn의 `from`은 **searchQuery가 필수**라
        #   "그 사람의 모든 글"을 못 가져온다(실측 400: "searchQuery must NOT have fewer than 1 characters").
        #   즉 이 층은 지금까지 **구조적으로 불가능**했다.
        A = ch.get("actors", {})
        slug = A.get("user_posts") or A.get("keyword", "burbn~threads-search-scraper")
        per = int(ch.get("watchlist_posts_per_account", 10))
        after = self._after_date(ch)
        out: list[Candidate] = []

        if _accepts_batch(slug):                 # 배치 지원 → 20명씩 묶어서
            # 워치리스트는 매번 전 계정을 도는 층이라 커서가 없다 — 성공 인원수는 안 쓴다.
            items, _ = _run_user_posts(slug, accounts, per, after, "watchlist")
            for p in items:
                u = _handle_of(p)
                out.append(self._post_to_candidate(
                    p, sourcing_method="watchlist", search_mode="KEYWORD", search_type="RECENT",
                    query="", is_reply=False, layer="watchlist",
                    match_reason=f"워치리스트: @{u}"))
        else:
            for user in accounts:                # burbn: `from`이 단일값 → 계정당 런 1회
                try:
                    items = apify.run_actor(slug, _user_posts_payload(slug, [user], per, after))
                except Exception as e:
                    print(f"⚠️  watchlist '@{user}' 실패: {e}")
                    continue
                for p in items:
                    out.append(self._post_to_candidate(
                        p, sourcing_method="watchlist", search_mode="KEYWORD", search_type="RECENT",
                        query="", is_reply=False, layer="watchlist",
                        match_reason=f"워치리스트: @{user}"))
        _health(out, "watchlist", len(accounts))
        return out

    WATCH_CUR = PROJ / "data" / "threads_watch_cursor.json"

    def _watch_cursor(self) -> int:
        try:
            return int(json.loads(self.WATCH_CUR.read_text(encoding="utf-8"))["cursor"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            return 0

    def _save_watch_cursor(self, n: int) -> None:
        p = self.WATCH_CUR.with_suffix(".json.pending")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"cursor": int(n)}), encoding="utf-8")

    def _load_watchlist(self) -> list[str]:
        """큐레이션 명단(keywords.yaml) + 자동 등록분(로컬 파일). **큐레이션이 항상 앞**이다.

        🔴 [2026-07-13] 예전엔 로컬 파일만 읽었다. 그래서 `keywords.yaml: watch_accounts`에
           손으로 검증해 넣은 **진성 한국 빌더 10명이 0/10, 단 한 번도 감시된 적이 없었다.**
           로테이션 커서가 12명을 뽑는데 자동 등록분(당시 103명·대부분 가짜)이 명단을 덮어버렸다.
           큐레이션을 앞에 두는 이유: 이 층의 가치는 **검증된 사람의 새 글**이지 아무나가 아니다.
        """
        auto: list[str] = []
        if self.WATCHLIST.exists():
            try:
                auto = json.loads(self.WATCHLIST.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                auto = []
        curated = list((self.keywords.get("threads") or {}).get("watch_accounts") or [])
        seen, out = set(), []
        for h in curated + auto:                     # 큐레이션 먼저 → 로테이션에서 안 밀린다
            if h and h not in seen:
                seen.add(h)
                out.append(h)
        return out

    def _collect_official(self, keywords: list[str]) -> list[Candidate]:
        token = os.environ.get("THREADS_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("THREADS_ACCESS_TOKEN 없음 — .env 확인 (references/threads.md)")
        out: list[Candidate] = []
        for q in keywords:
            params = {
                "q": q, "search_mode": "KEYWORD", "search_type": "RECENT",
                "fields": "id,text,permalink,timestamp,username",
                "access_token": token,
            }
            r = requests.get(GRAPH, params=params, timeout=30)
            r.raise_for_status()
            for p in r.json().get("data", []):
                out.append(Candidate(
                    source=self.source, name=p.get("username", ""),
                    url=p.get("permalink", ""), founder=p.get("username", ""),
                    released_at=p.get("timestamp", ""), raw_text=p.get("text", ""),
                    signals=self._sig(sourcing_method="keyword_search", search_mode="KEYWORD",
                                      search_type="RECENT", query=q, is_reply=False,
                                      author_username=p.get("username", ""),
                                      raw=p, layer="keyword")))
        return out

    # ================= ② 팔로잉그래프 모니터 =================
    def _collect_following(self, ch: dict, seeds: list[str]) -> list[Candidate]:
        """시드(진성 빌더) → 그가 팔로우하는 계정 1홉 → 각자의 최근 글.

        **"누가 무슨 말을 했나"가 아니라 "누구와 연결됐나"로 찾는다.** 키워드를 한 글자도
        안 써도 잡히므로, 검색이 구조적으로 놓치는 사람을 정의상 회수한다.

        액터(igview-owner)는 **로그인 불필요**다 — 라이브 스키마 GET으로 확정:
          required=['username'] · properties={username, max_following}. sessionId 없음.
        (기존 futurizerush~…-api 하나가 sessionId를 요구했고, 그것만 보고 "원천 봉쇄"로 접었었다.)
        """
        runs = _seed_runs(seeds) + _seed_runs(self._threads_keywords().get("following_seed_trials"))
        if not runs:
            print("· threads following_graph 층: 시드 계정 없음 → skip (keywords.yaml 채우기)")
            return []
        actors = ch.get("actors", {})
        follow_slug = actors.get("following", "")
        posts_slug = actors.get("user_posts", "")
        per_acct = int(ch.get("following_max_per_account", 30))
        posts_per = int(ch.get("following_posts_per_user", 5))
        max_expanded = int(ch.get("following_max_expanded", 60))   # 비용 가드
        after = self._after_date(ch)

        # 🔴 [2026-07-14] 예전엔 `dict[str, str]` + `setdefault` 였다 = **먼저 돈 시드가 먹는다.**
        #   두 시드가 같은 사람을 팔로우하면 **keywords.yaml의 순서**가 귀속을 정했다.
        #   실행 순서가 측정값을 바꾸면 그건 측정이 아니다. 실측 피해: jakecoldbyte의 타깃
        #   정밀도가 귀속 방식에 따라 **15.0% ↔ 6.2%**로 갈렸다.
        #   → **모든 시드를 담는다.** 시드 평가는 두 숫자가 필요하고, 둘이 다른 질문에 답한다:
        #     · 정밀도(이 시드의 팔로잉이 얼마나 좋나) = 중복 포함
        #     · **한계기여(이 시드를 빼면 몇 명을 잃나)** = 중복 제거 ← **시드를 자를 땐 이걸 봐라**
        #   (실측: 820_nam은 타깃 2명을 데려오지만 **혼자만 데려온 건 0명** → 빼도 손실 0.
        #    sunday_builders는 **혼자만 5명** → 빼면 이 층의 타깃이 9→4로 무너진다.)
        expanded: dict[str, set[str]] = {}      # username → 이 사람을 데려온 시드 **전부**
        profiles: dict[str, dict] = {}         # username → igview 원본(이름·팔로워수·인증)
        failed = 0
        per_seed: dict[str, int] = {}          # 시드별 수확 — 킬룰 판정용
        for seed in runs:
            try:
                items = apify.run_actor(follow_slug, {"username": seed,
                                                      "max_following": per_acct})
            except Exception as e:
                failed += 1
                print(f"⚠️  following '{seed}' 확장 실패: {e}")
                continue
            per_seed[seed] = per_seed.get(seed, 0) + len(items)
            if not items:
                continue
            got = 0
            for it in items:
                if not _is_profile_row(it):      # 액터가 섞어 보내는 요약 행 — 프로필이 아니다
                    continue
                u = _handle_of(it)
                if u:
                    expanded.setdefault(u, set()).add(seed)   # 첫 시드가 먹지 않는다 — 전부 담는다
                    profiles.setdefault(u, it)   # full_name·follower_count·is_verified 보존
                    got += 1
            if got == 0:
                # ★ 키 이름을 추측하지 않는다 — **실제 키를 찍는다.** (반복버그 ①)
                print(f"🔴 HEALTH: following '{seed}' {len(items)}건 반환인데 username을 못 뽑았다. "
                      f"실제 키: {sorted(items[0].keys())[:12]} → _handle_of()를 이 키로 고쳐라.")

        if not expanded:
            print(f"🔴 threads following_graph: 확장 0계정 (시드 {len(seeds)} · 실패 {failed}) "
                  f"— 액터가 죽었거나 스키마가 바뀌었다")
            return []

        # ★ 이 층의 존재 근거 = **순증**이다. 이미 아는 사람만 나오면 재방문에 불과하다.
        known = self._known_handles()
        novel = [u for u in expanded if u.lower() not in known]
        print(f"· threads following_graph: 런 {len(runs)} → 확장 {len(expanded)}계정 "
              f"(그중 **처음 보는 계정 {len(novel)}명** — 이 숫자가 이 층의 존재 근거다)")
        if expanded and not novel:
            print("  · 순증 0명 = 시드의 팔로잉을 이미 전부 알고 있다는 뜻이다(버그 아님). "
                  "시드 계정을 늘리거나 교체해야 이 층이 다시 산다.")

        # 시드별 수확 — **킬룰(1런 30명 중 빌더 0 → blacklist) 판정에 필요한 유일한 장부다.**
        # 시드 하나가 0%와 41.5%를 가른다(실측). 이 줄이 없으면 다음에 또 감으로 시드를 고른다.
        for s, n in sorted(per_seed.items(), key=lambda x: -x[1]):
            print(f"  · 시드 @{s}: {n}계정 수확 ({runs.count(s)}런)")

        # 🔴 [2026-07-14] 예전엔 `novel[:max_expanded]`가 60에서 잘랐다. 그런데 07-13 로그는
        #   "확장 108계정 · 처음 보는 계정 108명"이었다 → **이미 돈 주고 산 48명을 버렸다.**
        #   착지는 글이 없어도 $0인데(person stub) 잘라서 없앤 것이다.
        #   48명 × 이 층의 빌더 정밀도 19.1% = **빌더 9명을 비용 $0으로 잃고 있었다.**
        if len(novel) > max_expanded:
            print(f"  🔴 확장 {len(novel)}명 중 {max_expanded}명만 착지한다 — **{len(novel) - max_expanded}명을 "
                  f"돈 주고 사서 버리는 중.** 착지는 $0이다(person stub).\n"
                  f"     → config.channels.threads.following_max_expanded 를 {len(novel)} 이상으로 올려라.")
        targets = novel[:max_expanded]

        # ★★ [2026-07-13] **글이 없어도 사람을 착지한다.**
        #   예전엔 `for p in posts:` 뿐이라, 팔로잉으로 발견한 사람도 최근 2일 안에 글을 안 썼으면
        #   통째로 버려졌다. 이 층의 존재 이유("극초기 창업자는 시드 단어를 안 쓴다 → 말이 아니라
        #   연결로 찾자")와 정면으로 모순이다. **말을 해야만 잡는 층**이 돼 있었다.
        #   핸들은 이미 손에 있으므로 사람 행을 만드는 데 추가 비용이 $0이다.
        out: list[Candidate] = [self._person_stub(u, sorted(expanded[u]), profiles.get(u) or {})
                                for u in targets]

        # 글은 **예산 안에서 커서로 돌아가며** 산다. 비배치 액터로 전원의 글을 매번 사면 런이
        # 인원수만큼 늘어 시작 수수료만 60×$0.16=$9.60이 된다(비용의 대부분이 런 시작이었다).
        #
        # 🔴 [2026-07-13] 그런데 **배치 액터(meta)면 그 전제가 깨진다** — 20명이 런 1회다.
        #   이 층이 착지시킨 인원(following_max_expanded=60) 전원의 글을 3런($0.81)에 살 수 있는데
        #   following_posts_budget(=15)을 그대로 써서 **15명만** 사고 있었다. 나머지 45명은
        #   글 없는 stub으로 남아 judge가 Tier를 못 매긴다 = 착지시켜 놓고 못 쓰는 사람이 된다.
        #   (tools/estimate_cost.py도 배치면 60명으로 계산한다 — 어댑터가 추정과 달랐다.)
        #
        # 🔴 [2026-07-14] **커플링 제거.** 위 줄이 `budget = following_max_expanded` 였는데,
        #   확장 상한을 120으로 올리는 순간 **글 값이 같이 따라 올라간다**(meta 6런 = $1.62).
        #   그런데 이 층의 글은 살 가치가 없다:
        #     · 코퍼스 FG 120명 중 **100명이 글 본문 없음**(글을 안 쓰는 사람들이다)
        #     · 07-13 실행에서 meta 액터가 이 층에 대해 **400 FAILED**로 아예 실패했다
        #   반면 **bio는 이 층의 유일한 신호다** — 라벨 513개가 전부 bio에서 나왔고,
        #   팔로잉 액터는 bio를 **안 준다**. 단가도 bio $0.005 vs 글 $0.0135로 **2.7배 싸다**.
        #   → `following_posts_budget: 0` 으로 글 구매를 끄고, land 후 backfill_bio.py로 bio를 산다.
        posts_slug_batch = _accepts_batch(posts_slug)
        budget = int(ch.get("following_posts_budget", 0))
        cur = self._follow_cursor()
        picks = [targets[(cur + i) % len(targets)] for i in range(min(budget, len(targets)))] if targets else []

        # [W12 · A-F7] 커서는 **실제로 응답을 받은 인원**까지만 전진한다(아래 참조).
        ok_lead = len(picks)
        if picks and posts_slug_batch:
            # 배치 지원 액터(meta)는 한 런에 **최대 20명**. 60명이면 런 3회로 쪼갠다.
            items, ok_lead = _run_user_posts(posts_slug, picks, posts_per, after, "following(글)")
            for p in items:
                u = _handle_of(p)
                sd = sorted(expanded.get(u) or [])
                out.append(self._post_to_candidate(
                    p, sourcing_method="following_graph", search_mode="KEYWORD",
                    search_type="RECENT", query="", is_reply=False,
                    match_reason=f"팔로잉그래프: {' + '.join('@'+s for s in sd) or '@?'} → @{u} (1홉)",
                    layer="following_graph", seed=sd))
        else:
            _broken = False
            ok_lead = 0
            for user in picks:                  # burbn: `from`이 단일 문자열 → 유저당 런 1회
                try:
                    posts = apify.run_actor(posts_slug, _user_posts_payload(
                        posts_slug, [user], posts_per, after))
                except Exception as e:
                    print(f"⚠️  user_posts '{user}' 실패: {e}")
                    _broken = True              # [W12 · A-F7] 이 사람부터는 커서 근거가 아니다
                    continue
                if not _broken:
                    ok_lead += 1
                sd = sorted(expanded.get(user) or [])
                for p in posts:
                    out.append(self._post_to_candidate(
                        p, sourcing_method="following_graph", search_mode="KEYWORD",
                        search_type="RECENT", query="", is_reply=False,
                        match_reason=f"팔로잉그래프: {' + '.join('@'+s for s in sd) or '@?'} → @{user} (1홉)",
                        layer="following_graph", seed=sd))

        if targets:
            # ★ .pending 으로만 쓴다 — 착지 성공 후 commit_seen()이 확정한다.
            #   착지 실패한 날 커서만 전진하면 그 사람들의 글은 **영영 안 산다**.
            #
            # 🔴 [2026-08-13 W12 · 리뷰 A-F7] 위 주석이 막겠다고 선언한 실패("착지 실패한 날")는
            #    막았는데 **수집이 실패한 날**은 안 막았다. `len(picks)` 만큼 전진했으므로
            #    20명 중 12명이 429 로 죽어도 커서는 20 전진했다. 2단계 커밋이 land 축만 보고
            #    collect 축을 안 본 것이다 — 그 12명은 로테이션 한 바퀴가 돌 때까지 안 산다.
            #    이제 **응답을 실제로 받은 선행 인원**까지만 전진한다.
            self._save_follow_cursor(cur + ok_lead)
        _lost = len(picks) - ok_lead
        print(f"· threads following_graph: 인물 {len(targets)}명 착지 "
              f"(그중 {len(picks)}명 글 수집 시도 · 커서 {cur} → {cur + ok_lead})"
              + (f"  ⚠️ {_lost}명은 수집 실패라 커서를 안 넘긴다(다음 실행에 재시도)"
                 if _lost else ""))
        _health(out, "following_graph", len(targets))
        return out

    def _person_stub(self, handle: str, seeds: list[str], prof: dict | None = None) -> Candidate:
        """글 없이 **사람만** 만든 후보. 팔로잉그래프가 발견한 계정 그 자체.

        ★ igview가 주는 full_name·follower_count·is_verified를 반드시 실어 보낸다.
          안 그러면 노션에 `@핸들` 한 줄짜리 행 60개가 쌓이고 **사람이 판단할 근거가 0이다.**
          (액터가 공짜로 주는 정보를 버리는 것 = 이 스킬의 반복버그 ②'계산하고 버림')
        """
        prof = prof or {}
        name = prof.get("full_name") or ""
        n_fol = prof.get("follower_count")
        via = " + ".join(f"@{s}" for s in seeds) or "@?"
        why = f"팔로잉그래프: {via} → @{handle} (1홉, 최근 글 없음)"
        if n_fol is not None:
            why += f" · 팔로워 {n_fol:,}"
        return Candidate(
            source=self.source, name=handle,
            url=f"https://www.threads.com/@{handle}",
            founder=handle, one_liner=name, released_at="", raw_text="",
            # 🔴 `seed`를 **구조화 필드로** 싣는다. 예전엔 match_reason **텍스트에만** 있어서
            #   시드 성과를 재려면 정규식으로 문장을 파싱해야 했다(그래서 아무도 안 쟀다).
            #   가장 값나가는 노브(허브 계정)를 **측정할 수 없는 상태**였다.
            signals=self._sig(sourcing_method="following_graph", search_mode="KEYWORD",
                              search_type="RECENT", query="", is_reply=False,
                              match_reason=why, raw=prof, layer="following_graph",
                              author_username=handle, author_display_name=name,
                              seed=seeds))

    def _known_handles(self) -> set[str]:
        """이미 아는 사람 = 워치리스트 + 시드 + **노션에 착지한 스레드 작성자 전원**.

        🔴 예전엔 워치리스트 10명만 봤다. 그래서 **이미 노션에 있는 89명이 "처음 보는 계정"으로
           집계됐고**, 그 사람들 글을 다시 사고 있었다. 순증 숫자 자체가 거짓이었다.
        """
        # 🔴 [2026-07-20 수정] 시드는 문자열일 수도, `{username, runs}` 딕트일 수도 있다.
        #   07-14에 가중치 스펙을 도입하면서 `_seed_runs`는 두 형식을 받게 고쳤는데 **여기는
        #   안 고쳤다.** 그래서 이 층이 `'dict' object has no attribute 'strip'`로 죽었고,
        #   층 단위 try/except가 그걸 ⚠️ 한 줄로 삼켜 **정밀도 1위 층(19.1%·전체 빌더의 35%)이
        #   조용히 0건을 내고 있었다.** 게다가 크래시는 액터 호출(=지출) **뒤**에 나서,
        #   팔로잉 확장을 사고 나서 전량 버리고 있었다.
        #   → 형식 정규화를 한 곳(_seed_name)에 모은다. 시드 형식을 또 바꾸면 여기만 고치면 된다.
        known = {_seed_name(u) for u in self._load_watchlist()}
        known |= {_seed_name(u)
                  for u in (self._threads_keywords().get("watch_accounts") or [])}
        known |= {_seed_name(u)
                  for u in (self._threads_keywords().get("following_seed_accounts") or [])}
        known |= {_seed_name(u)
                  for u in (self._threads_keywords().get("following_seed_trials") or [])}
        known.discard("")
        try:
            from core import notion
            landed = notion.landed_threads_handles(self.cfg)
            known |= landed
            print(f"· 기존 인지 인물: 노션 {len(landed)}명 + 로컬 {len(known) - len(landed)}명")
        except Exception as e:
            print(f"🔴 노션 기존 작성자 조회 실패: {e}\n"
                  f"   → 순증 판정이 무력해진다(이미 아는 사람 글을 다시 산다). 착지 후 중복은 걸러지지만 돈은 나간다.")
        return known

    FOLLOW_CUR = PROJ / "data" / "threads_follow_cursor.json"

    def _follow_cursor(self) -> int:
        try:
            return int(json.loads(self.FOLLOW_CUR.read_text(encoding="utf-8"))["cursor"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            return 0

    def _save_follow_cursor(self, n: int) -> None:
        p = self.FOLLOW_CUR.with_suffix(".json.pending")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"cursor": int(n)}), encoding="utf-8")

    # ================= ③ 리플라이 크롤 =================
    ANCHORS = PROJ / "data" / "threads_anchors.json"

    def _collect_replies(self, ch: dict, anchors: list[str]) -> list[Candidate]:
        """앵커 포스트("요즘 뭐 만드세요?"류)의 **댓글**을 긁는다.

        리플라이로만 런칭을 알리는 미접촉 창업자를 잡는다. 그런 글의 댓글은 이미 의도 신호가
        강해 오탐이 낮다. 앵커는 수동 시드 + **자동 수확**(_harvest_anchors) 합집합이다.

        액터 실 스키마(라이브 GET):
          post_urls  = **객체 배열** [{"url": "..."}]  ← 문자열 배열이 아니다(이래서 안 돌았다)
                       minItems 1 / maxItems 20
          max_replies = 10~50
          include_nested_replies = bool
        """
        anchors = list(dict.fromkeys(list(anchors) + self._load_anchors()))
        if not anchors:
            print("· threads replies 층: 앵커 없음 → skip "
                  "(keyword 층이 댓글 많은 글을 수확하면 다음 실행부터 자동으로 찬다)")
            return []
        slug = ch.get("actors", {}).get("replies", "")
        cap = max(10, min(int(ch.get("replies_max_per_anchor", 50)), 50))
        batch_n = max(1, min(int(ch.get("replies_max_anchors", 20)), 20))   # 액터 maxItems=20
        batch = anchors[:batch_n]

        try:
            items = apify.run_actor(slug, {
                "post_urls": [{"url": a} for a in batch],      # ★ 객체 배열
                "max_replies": cap,
                "include_nested_replies": False,
            })
        except Exception as e:
            print(f"⚠️  threads replies 실패: {e}")
            return []

        anchor_set = {_norm_post_url(a) for a in batch}
        out: list[Candidate] = []
        originals = 0
        for p in items:
            # 액터는 **원본 포스트 행도 함께** 준다(README 명시). 앵커 계정(허브/VC 본인)이
            # 후보로 섞이면 안 된다 → 원본 행은 버린다.
            purl = _norm_post_url(p.get("reply_url") or p.get("post_url")
                                  or p.get("postUrl") or p.get("url") or "")
            if purl and purl in anchor_set:
                originals += 1
                continue
            out.append(self._post_to_candidate(
                p, sourcing_method="reply_crawl", search_mode="KEYWORD",
                search_type="RECENT", query="", is_reply=True,
                match_reason=f"리플라이 크롤: 앵커 {len(batch)}개",
                layer="replies"))
        print(f"· threads replies: 앵커 {len(batch)}개 · 원본행 {originals} 제외")
        _health(out, "replies", len(batch), items=items)
        return out

    # ---- 앵커 자동 수확 ----
    def _harvest_anchors(self, ch: dict, cands: list[Candidate]) -> None:
        """keyword 층 결과에서 **좋은 앵커**를 골라 다음 실행의 앵커 풀에 등록한다.

        사람이 매주 앵커 URL을 손으로 찾는 건 지속가능하지 않다 → 파이프라인이 스스로 키운다.

        ⚠️ **댓글 수만으로 고르면 안 된다.** 실측: 댓글 82개짜리 1위가 "팔로워 100명 됐당"이었다.
        그런 글의 댓글엔 창업자가 없다 — 앵커의 가치는 **댓글에 빌더가 모여 있는가**이지
        인기가 아니다. 그래서 두 관문을 통과해야 한다:
          ① 댓글 ≥ anchor_min_replies (사람이 모였나)
          ② **묻는 글**이거나 **만든 이야기**여야 한다(_ANCHOR_ASK) — 그리고 이벤트/맞팔 미끼가
             아니어야 한다(_ANCHOR_BAIT). "요즘 뭐 만드세요?"의 댓글은 정의상 빌더가 답한다.
        """
        min_replies = int(ch.get("anchor_min_replies", 8))
        keep = int(ch.get("anchor_keep", 30))
        if not min_replies:
            return
        pool = self._load_anchors()
        have = set(pool)
        added = rejected = 0
        for c in cands:
            n = c.signals.get("comments")
            if not isinstance(n, int) or n < min_replies:
                continue
            text = c.raw_text or ""
            if _ANCHOR_BAIT.search(text) or not _ANCHOR_ASK.search(text):
                rejected += 1                  # 인기는 있으나 빌더가 안 모이는 글
                continue
            u = (c.url or "").strip()
            if u and u not in have:
                pool.insert(0, u)              # 최신 글이 앞으로
                have.add(u)
                added += 1
        if rejected:
            print(f"· threads 앵커 후보 {rejected}개 기각(댓글은 많으나 빌더가 안 모이는 글)")
        if added:
            pool = pool[:keep]
            self.ANCHORS.parent.mkdir(parents=True, exist_ok=True)
            self.ANCHORS.write_text(json.dumps(pool, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
            print(f"· threads 앵커 풀: {len(pool)}개 (+{added}) — 댓글 {min_replies}+ 달린 글")

    def _load_anchors(self) -> list[str]:
        if self.ANCHORS.exists():
            try:
                return json.loads(self.ANCHORS.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return []
        return []

    # ================= 공통: Apify 포스트 → Candidate =================
    #
    # ★ [2026-07-13] 폴백 체인을 **액터 README의 실제 출력 예시**로 검증했다(무과금).
    #   추측이 아니다 — GET /v2/acts/{slug}/builds/default 의 readme에 출력 JSON이 실려 있다.
    #   그 결과 조용히 새던 필드 6개를 잡았다(아래 ★ 표시). 파일: data/fixtures/*.json
    #
    #   실제 출력 키 (README 확인):
    #     burbn                : username · captionText · postUrl · takenAtISO · likeCount ·
    #                            directReplyCount · repostCount · isReply
    #     futurizerush(replies): author_username · author_display_name · text_content · reply_url ·
    #                            post_url · created_at · like_count · reply_count · repost_count ·
    #                            author_is_verified · item_type
    #     futurizerush(meta)   : username · display_name · text_content · post_url · created_at ·
    #                            like_count · reply_count · repost_count · is_verified · is_reply ·
    #                            **bio** · **followers_count** · search_keyword
    def _post_to_candidate(self, p: dict, **meta) -> Candidate:
        username = (p.get("username") or p.get("ownerUsername") or p.get("author_username")
                    or (p.get("user") or {}).get("username", "")
                    or (p.get("author") or {}).get("username", "") or "")
        display = (p.get("name") or p.get("author_display_name")
                   or p.get("display_name")                       # ★ meta-threads
                   or (p.get("user") or {}).get("full_name")
                   or (p.get("user") or {}).get("name", "")
                   or (p.get("author") or {}).get("name", "") or "")
        text = (p.get("captionText") or p.get("text") or p.get("text_content")
                or p.get("caption") or p.get("content", ""))
        return Candidate(
            source=self.source,
            name=username or display,
            url=(p.get("postUrl") or p.get("url") or p.get("permalink")
                 or p.get("reply_url") or p.get("post_url", "")),
            founder=username,
            # 노션에서 사람이 훑을 때 보이는 칸. 비워두면 노션 목록이 핸들만 늘어선 표가 된다
            # (실측: One Liner 0/99). 글 첫 줄이 사실상 제목 역할을 한다.
            one_liner=_first_line(text),
            released_at=(p.get("takenAtISO") or p.get("timestamp") or p.get("created_at")
                         or p.get("publishedAt") or p.get("postedAt", "")),
            raw_text=text,
            signals=self._sig(author_username=username, author_display_name=display,
                              raw=p, **meta),
        )

    def _sig(self, *, raw: dict, sourcing_method: str, search_mode: str, search_type: str,
             query: str, is_reply: bool, layer: str, author_username: str = "",
             author_display_name: str = "", match_reason: str = "",
             seed: list[str] | None = None) -> dict:
        """Candidate.signals → 그물망 소싱 DB 필드.

        `seed` = 팔로잉그래프에서 **이 사람을 데려온 시드 계정 전부**(다른 층은 빈 리스트).
        🔴 노션 `Seed`(multi_select)에 실린다. 옵션에 없는 값은 `_known_options_only`가
           **조용히 버린다** → 시드를 추가하면 `tools/notion_migrate.py --apply`를 **먼저** 돌려라.
        """
        # ★ 액터가 주는 리플 여부. burbn=isReply, meta=is_reply. 둘 다 없으면 층 의도로 폴백.
        raw_reply = raw.get("isReply")
        if raw_reply is None:
            raw_reply = raw.get("is_reply")
        return {
            "channel": "Threads",
            "layer": layer,
            "sourcing_method": sourcing_method,
            "search_mode": search_mode,
            "search_type": search_type,
            "query": query,
            "matched_keyword": [query] if query else [],
            "seed": list(seed or []),           # 팔로잉그래프 귀속 — 노션 `Seed`(multi_select)
            "match_reason": match_reason,
            "is_reply": raw_reply if raw_reply is not None else is_reply,
            "is_quote": bool(raw.get("isQuote") or raw.get("is_quote_post")),
            "author_username": author_username,
            "author_display_name": author_display_name,
            # ★ author_is_verified(replies) · is_verified(meta) 가 빠져 있어 **전원 False**였다
            "author_verified": bool(raw.get("isVerified") or raw.get("verified")
                                    or raw.get("author_is_verified") or raw.get("is_verified")
                                    or (raw.get("user") or {}).get("is_verified")),
            # ★ 스레드엔 headline이 없어 이 칸이 0/99로 비어 있었다. meta-threads는 **bio**를 준다 —
            #   "무엇을 만드는 사람인가"가 여기 적혀 있다. judge가 볼 가장 좋은 근거다.
            "author_headline": raw.get("bio") or raw.get("biography") or "",
            "followers": raw.get("followers_count") or raw.get("follower_count"),
            "content_type": _content_type(raw),
            "media_type": _media_type(raw),
            # ★ like_count / repost_count 가 빠져 있어 Likes·Shares가 **전부 빈칸**이었다.
            "likes": _num(raw, "likeCount", "likesCount", "likes", "like_count"),
            "comments": _num(raw, "directReplyCount", "repliesCount", "reply_count", "comments"),
            "shares": _num(raw, "repostCount", "reshareCount", "repostsCount", "reposts",
                           "repost_count"),
        }


# 앵커 자격 — **댓글에 빌더가 모이는 글**인가. (인기 있는 글이 아니라)
#   · 묻는 글: "요즘 뭐 만드세요?" "프로젝트 공유해주세요" "자랑해주세요" → 댓글이 곧 빌더 명부
#   · 만든 이야기: 런칭/개발/사이드프로젝트 글 → 댓글에 같은 처지의 빌더가 붙는다
_ANCHOR_ASK = re.compile(
    r"(뭐 ?만드|뭘 ?만드|만들고 ?계신|만드시는|어떤 ?(걸|것|서비스|앱|프로젝트)"
    r"|공유해ㅈ|공유해 ?주세요|자랑|보여주세요|소개해ㅈ|소개해 ?주세요|추천해ㅈ"
    r"|사이드프로젝트|인디해커|1인 ?개발|개발자|창업|런칭|출시|배포|피드백|베타|MVP"
    r"|프로젝트|만들었|만들고 있)")
# 앵커 실격 — 이벤트/맞팔/바이럴 잡담. 댓글이 아무리 많아도 창업자가 아니다.
_ANCHOR_BAIT = re.compile(
    r"(맞팔|선팔|팔로워|이벤트|응모|추첨|증정|나눔|공동구매|할인|쿠폰|체험단|리포스트)")


def _first_line(text: str, limit: int = 120) -> str:
    """글 첫 줄(=사실상의 제목). 노션 One Liner 칸에 들어가 사람이 목록에서 훑을 근거가 된다."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:limit]
    return ""


def _num(raw: dict, *keys) -> int | None:
    """첫 번째로 값이 있는 키의 숫자. **0을 None으로 뭉개지 않는다**(`or` 체인의 함정).

    좋아요 0개는 '없음'이 아니라 '0개'다 — 앵커 선정이 comments 값을 보고 판단하므로 중요하다.
    """
    for k in keys:
        v = raw.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _is_profile_row(it: dict) -> bool:
    """igview 팔로잉 액터는 프로필 행 사이에 **요약 행**을 하나 섞어 보낸다.

    {"summary": true, "success": ..., "target_username": ..., "total_following": ...}
    이걸 프로필로 세면 '핸들 추출 실패'로 집계돼 헬스 경보가 오작동한다(액터는 멀쩡한데).
    """
    return not it.get("summary") and bool(it.get("username"))


def _seed_name(s) -> str:
    """시드 1건 → username 문자열. **두 형식을 다 받는다** — `_seed_runs`와 같은 규칙이다.

    🔴 시드 형식이 두 곳에서 따로 해석되면 한쪽만 고쳐지는 사고가 난다. 실제로 07-14에
       `_seed_runs`만 딕트를 받게 고치고 `_known_handles`를 안 고쳐서, 팔로잉그래프 층이
       엿새간 조용히 죽어 있었다(2026-07-20 발견). 정규화는 여기 하나만 쓴다.
    """
    if isinstance(s, dict):
        s = s.get("username") or s.get("handle") or ""
    return str(s or "").strip().lstrip("@").lower()


def _seed_runs(seeds) -> list[str]:
    """시드 스펙 → **실행할 username 리스트**(가중치만큼 반복).

    🔴 [2026-07-14] 같은 시드를 **여러 번 돌리면 새 사람이 온다.** max_following=30은 천장이 아니라
       **랜덤 표본**이다 — jakecoldbyte는 34분 간격 2런의 겹침이 4/30뿐이었다(실측).
       "팔로잉 목록은 1회성 stock이라 재실행이 무의미하다"는 가설은 반증됐다.
       → 좋은 시드(sunday_builders 41.5%)는 여러 번, 나쁜 시드는 아예 안 돌린다.

    두 형식을 다 받는다:
      - "jakecoldbyte"                      → 1런
      - {username: jakecoldbyte, runs: 3}   → 3런
    """
    out: list[str] = []
    for s in (seeds or []):
        if isinstance(s, dict):
            u = (s.get("username") or "").strip()
            n = max(1, int(s.get("runs") or 1))
        else:
            u, n = str(s).strip(), 1
        if u:
            out += [u] * n
    return out


def _norm_phrase(s: str) -> str:
    """구(句) 대조용 정규화 — 공백만 제거한다.

    "앱스토어 심사" / "앱스토어심사" / "앱스토어  심사"를 같게 본다. 워치리스트 자동 등록이
    **본문에 그 말이 실제로 있는지**를 보게 하려면 이 정도 관용은 필요하다(띄어쓰기는 사람마다 다르다).
    그 이상 느슨하게 하면(토큰 분해 등) 액터가 저질렀던 그 실수를 우리가 반복한다.
    """
    return "".join((s or "").split())


def _handle_of(it: dict) -> str:
    """팔로잉 액터의 한 항목 → username. 출력 스키마 미검증이라 폴백을 넓게 잡는다."""
    for k in ("username", "handle", "user_name", "userName", "owner_username", "ownerUsername"):
        v = it.get(k)
        if v:
            return str(v).lstrip("@")
    for nest in ("user", "node", "author"):
        u = (it.get(nest) or {}).get("username") if isinstance(it.get(nest), dict) else None
        if u:
            return str(u).lstrip("@")
    return ""


# _norm_post_url 은 adapters/base.py 로 옮겼다(norm_post_url).
# **쓰는 쪽(어댑터)과 읽는 쪽(core/notion의 본문 블록 대조)이 반드시 같은 정규화를 써야 하기 때문이다.**
# 한 글자만 어긋나면 "이미 붙인 글"을 못 알아보고 매일 같은 글이 본문에 중복 append된다.
_norm_post_url = norm_post_url


def _health(out: list, layer: str, n_targets: int, items: list | None = None) -> None:
    """층별 산출 검증 — **0건인데 ✅를 찍지 않는다.**

    이 팀의 반복 버그 ④(조용히 실패)를 막는다. 실제로 오늘 로그가 '✅ threads watchlist: 0건'
    이었는데 그 0은 10계정 전원 403이었다 — 성공처럼 읽혔다.
    빈 껍데기(작성자 못 뽑음)도 여기서 잡는다: 그대로 두면 노션에 이름 없는 행이 쌓인다.
    """
    if not out:
        if items:
            print(f"🔴 HEALTH: threads {layer} — 액터는 {len(items)}건 줬는데 후보 0건. "
                  f"실제 키: {sorted(items[0].keys())[:12]} → _post_to_candidate 폴백을 이 키로 고쳐라.")
        else:
            print(f"🔴 HEALTH: threads {layer}: 0건 (대상 {n_targets}) — 액터 실패/쿼터 초과 의심")
        return
    empty = sum(1 for c in out if not c.founder)
    print(f"✅ threads {layer}: {len(out)}건 · 대상 {n_targets}")
    if empty > len(out) * 0.3:
        keys = sorted(items[0].keys())[:12] if items else "(원본 미보존)"
        print(f"🔴 HEALTH: threads {layer} — 작성자가 빈 후보 {empty}/{len(out)}건. "
              f"액터 출력 스키마가 다르다. 실제 키: {keys}")


def _search_payload(slug: str, query: str, max_posts: int, after: str) -> dict:
    """액터별 입력 스키마 차이를 흡수한다. **입력 키가 액터마다 완전히 다르다** (라이브 조회):

      burbn~threads-search-scraper          futurizerush~meta-threads-scraper
        searchQuery (문자열, required)        keywords (배열)
        sort: top|recent                     search_filter: top|recent
        maxPosts (max 1000)                  max_posts
        after / before                       start_date / end_date
        —                                    **mode (required)**

    ⚠️ 스왑 시 **출력** 스키마도 다를 수 있다(README에 출력 예시 없음). _post_to_candidate의
    필드 폴백이 흡수하지만, 못 흡수하면 본문이 통째로 빈다 → `empty_text` HEALTH 경고로 감지.
    """
    if _accepts_batch(slug):
        p = {"mode": "search", "keywords": [query],
             "search_filter": "recent", "max_posts": max_posts}
        if after:
            p["start_date"] = after
        return p
    p = {"searchQuery": query, "sort": "recent", "maxPosts": max_posts}
    if after:
        p["after"] = after
    return p


def _accepts_batch(slug: str) -> bool:
    """이 액터가 **여러 대상을 한 런에** 받는가 (keywords/usernames 배열).

    ★ 비용의 핵심이다. Apify는 actor-start를 **메모리 GB당** 물리고 burbn은 최소 4GB라
      런 하나에 $0.16이 붙는다. 팔로잉 확장 60명의 글을 사려면 burbn은 런 60회 = **$9.60**이
      시작 수수료로만 나간다(데이터 값은 $0.45다).
      meta-threads는 usernames를 배열로 받아 **런 1회**($0.02)로 끝난다.
    """
    return "meta-threads-scraper" in slug


MAX_BATCH_USERS = 20          # meta 액터 usernames maxItems=20 (입력 스키마 실측)


def _run_user_posts(slug: str, users: list[str], per_user: int, after: str,
                    label: str) -> tuple[list[dict], int]:
    """유저들의 최근 글을 **20명씩 쪼개** 가져온다. 한 배치가 죽어도 나머지는 살린다.

    쪼개는 이유는 액터 상한(usernames maxItems=20)이다. 60명을 한 번에 보내면 런이 죽는데,
    이전엔 그게 "배치 실패" 한 줄로 끝나 **그 층 전체가 0건**이 됐다(07-13 실제).

    반환: `(아이템, 선행 성공 인원수)`.

    🔴 [2026-08-13 W12 · 리뷰 A-F7] 두 번째 값이 새로 생겼다. 예전엔 아이템만 돌려줬고
       호출부가 **`len(picks)` 만큼 커서를 전진**시켰다 — 배치가 죽어도 그 인원까지
       "처리됨"이 된다. 그 사람들의 글은 로테이션이 한 바퀴 다 돌 때까지 **사실상 영영
       안 산다.** 커서는 진행도인데 실패를 세지 않았으니 단위가 안 맞았다.

       **선행 성공만** 센다(첫 실패에서 멈춘다). 흩어진 실패를 개수로만 세면 뒤쪽 성공분이
       앞쪽 실패분의 자리를 메워 **건너뛰기가 생긴다.** 순차 드레인은 `run_due` 가 밀린
       주기에 쓰는 것과 같은 규율이다 — 재수집이 유실보다 항상 낫다.
    """
    items: list[dict] = []
    ok_lead = 0
    broken = False
    for i in range(0, len(users), MAX_BATCH_USERS):
        chunk = users[i:i + MAX_BATCH_USERS]
        try:
            items += apify.run_actor(slug, _user_posts_payload(slug, chunk, per_user, after))
        except Exception as e:
            print(f"⚠️  {label} 배치 {i // MAX_BATCH_USERS + 1} 실패({len(chunk)}명): {e}")
            broken = True
            continue                      # 뒤 배치는 계속 산다(수집은 최대한 건진다)
        if not broken:
            ok_lead += len(chunk)         # 첫 실패 전까지만 커서 근거로 인정한다
    return items, ok_lead


def _user_posts_payload(slug: str, users: list[str], per_user: int, after: str) -> dict:
    """특정 유저(들)의 최근 글. 배치 지원 액터면 여러 명을 한 번에.

    🔴 [2026-07-13 실측 — 액터 입력 스키마에서 확인. 추측 금지]
      meta-threads-scraper 의 mode:user 는 아래 셋을 어기면 **런이 exit_code 1로 죽는다**:
      ① `start_date` — "Used in Search mode... **Leave empty in other modes — a date set there
         will stop the run with a reminder.**" → mode:user에 넣으면 액터가 **일부러 죽인다.**
         (07-13 워치리스트·팔로잉글 두 층이 이것 하나로 통째로 0건이 됐다.)
         신선도 컷은 서버측 대신 클라이언트 `_too_old()`가 한다.
      ② `usernames` maxItems=**20**. 60명을 한 번에 보내면 죽는다 → 호출부가 20명씩 쪼갠다.
      ③ `max_posts` 는 **1인당**이다("Per user or search keyword"). 인원수를 곱하면 안 된다 —
         곱하면 1인당 300글을 사는 셈이라 **비용이 인원수배로 튄다.** 최소값은 10.
      `search_filter` 는 Search 모드 전용이라 mode:user에선 무시된다 → 안 보낸다.
    """
    if _accepts_batch(slug):
        if len(users) > MAX_BATCH_USERS:
            raise ValueError(f"meta 액터 usernames 상한 {MAX_BATCH_USERS} 초과: {len(users)}명 "
                             f"— 호출부가 쪼개야 한다")
        return {"mode": "user", "usernames": list(users),
                "max_posts": max(10, per_user)}          # ← 1인당. len(users)를 곱하지 마라.
    # burbn: `from`은 단일 문자열이라 유저당 런 1회밖에 안 된다.
    # searchQuery가 required라 빈 문자열을 넣는다(from만으로는 안 돈다).
    p = {"from": users[0], "searchQuery": "", "sort": "recent", "maxPosts": per_user}
    if after:
        p["after"] = after
    return p


def _too_old(released_at: str, cutoff: str) -> bool:
    """게시일이 `cutoff`(YYYY-MM-DD) 이전인가. 파싱 실패 시 False(=살린다, recall 우선).

    🔴 [2026-07-13] 예전엔 `max_age_days`(=2)를 **직접** 받아 "오늘-2일"로 잘랐다. 그게 틀렸다:
       서버측 `after`는 `_after_date()`가 **마지막 성공일까지 되짚어**(백필) 정하는데,
       클라이언트 컷만 항상 2일이라 **산 걸 버렸다.**
         5일 쉬었다 돌린다 → after=5일 전 → 5일치를 **돈 주고 사온다**
                          → 그런데 _too_old가 3일치를 **버린다.** 백필 로직이 통째로 무력화.
       매일 돌 땐 둘 다 2일이라 안 보였다. **수동/주 1회 실행이 정확히 이걸 밟는다.**
       → 두 창은 **반드시 같아야 한다.** 그래서 이제 `after`를 그대로 받는다.
    """
    if not released_at or not cutoff:
        return False
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(released_at.replace("Z", "+00:00"))
        c = datetime.fromisoformat(cutoff).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d < c


def _media_type(p: dict) -> str:
    """Threads DB Media Type: TEXT | IMAGE | VIDEO | CAROUSEL_ALBUM.

    액터가 media_type을 숫자 코드로 줄 수 있어 str 강제(문자열일 때만 라벨 매칭).
    """
    mt = str(p.get("media_type") or p.get("mediaType") or "").upper()
    if mt in ("TEXT", "IMAGE", "VIDEO", "CAROUSEL_ALBUM"):
        return mt
    if p.get("videoUrl") or p.get("video_url"):
        return "VIDEO"
    imgs = p.get("images") or p.get("imageUrls") or []
    if isinstance(imgs, list) and len(imgs) > 1:
        return "CAROUSEL_ALBUM"
    if imgs or p.get("imageUrl") or p.get("image_url"):
        return "IMAGE"
    return "TEXT"


def _content_type(p: dict) -> str:
    """DB Content Type(select): text | image | video (스레드는 article/job 없음)."""
    mt = _media_type(p)
    return {"IMAGE": "image", "VIDEO": "video", "CAROUSEL_ALBUM": "image"}.get(mt, "text")


def commit_seen(cfg: dict) -> None:
    """[2단계 커밋] land 성공 후 호출 — collect가 .pending 으로만 써 둔 커서를 확정한다.

    ★ 왜 2단계인가: 커서를 collect 시점에 바로 전진시키면, **착지가 실패한 날에도 커서만 전진**해
      그 사람들의 글을 영영 안 산다(조용한 영구 유실). 재수집(돈 조금 더)이 유실보다 항상 낫다 —
      이 스킬의 기본 방향이다. pipeline._COMMIT_HOOKS 가 land 성공(실패 0건) 시에만 부른다.
    """
    for final in (ThreadsAdapter.FOLLOW_CUR, ThreadsAdapter.WATCH_CUR):
        pend = final.with_suffix(".json.pending")
        if pend.exists():
            pend.replace(final)
            print(f"· threads 커서 확정: {final.name}")
