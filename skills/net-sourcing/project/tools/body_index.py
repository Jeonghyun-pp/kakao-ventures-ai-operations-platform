#!/usr/bin/env python3
"""대조원 DB의 **페이지 본문**을 인덱싱한다 — 속성만 읽던 dedup의 사각지대를 메운다.

## 왜 필요한가 (2026-07-21 실측 사고)
스타트업 DB 행 `3420ac54…`는 **`회사명 = 이선민`**(사람 이름)이고, 진짜 회사명
`다이달게임즈 (Daedalus Games)`는 **페이지 본문 블록**에만 있었다. `✅ 대표이사`도 빈칸.
그래서 대조원을 4/4로 붙이고 키를 6,059개로 늘려도 **Example Person J(=이선민)를 영영 못 잡았다.**
매처 문제가 아니라 **인덱싱 범위** 문제다 — dedup이 properties만 읽고 있었다.

## 비용 (실측)
행당 평균 1.70초 · 4.7요청 · 2,195자. 노션 한도 3req/s가 바닥이라 병렬로도 못 줄인다.
  · 스타트업DB 2,286행 ≈ 65분 / 극초기 1,289행 ≈ 37분 / Bplan3.0 453행 ≈ 13분  → 전량 ≈ 115분
→ **매 수집마다 돌릴 수 없다.** 그래서 캐시 + `last_edited_time` 증분이 필수다.
   첫 빌드만 비싸고, 이후엔 바뀐 행만 다시 읽는다(보통 하루 수 건 = 수 초).

## 매칭 정책 — **소프트 플래그지 하드 컷이 아니다**
본문 substring은 오탐이 많다(실측: 토큰 부분일치 7건 중 6건이 오탐 —
`유월` vs `유월의 시현이`, `Clips` vs `Clips by xdge.ai`). 정규화 과정에서 공백이 사라져
단어 경계도 잃는다. 그래서 이 인덱스는 **"사람이 확인할 후보"를 띄우는 용도**이며,
자동 드랍에 쓰면 안 된다. (2026-07-10 확정 "이름 매칭 히트는 '가능중복' 자동 기록만" 과 같은 결.)

## 사용
    python tools/body_index.py build            # 증분 빌드(캐시 갱신)
    python tools/body_index.py build --db 스타트업DB
    python tools/body_index.py check "다이달게임즈" "Anymorph" ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "https://api.notion.com/v1"
CACHE = ROOT / "data" / "dedup_body_cache.json"
LOCK = ROOT / "data" / "body_index.lock"
MIN_LEN = 3          # 이보다 짧은 질의는 오탐 폭발 → 거부
# 🔴 [2026-07-21] 중첩 깊이. 처음에 2로 뒀다가 **정답 케이스를 놓쳤다** —
#    `다이달게임즈`는 toggle > transcription > paragraph > bulleted_list_item = **깊이 3**에 있었다.
#    미팅 녹취(transcription) 블록이 깊게 중첩되는 게 이 워크스페이스의 패턴이다.
#    얕게 자르면 "본문을 읽었다"고 착각하면서 정작 회사명을 놓친다.
MAX_DEPTH = 5


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _token() -> str:
    tok = os.environ.get("NOTION_TOKEN", "")
    if not tok and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("NOTION_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    return tok


def norm(s: str) -> str:
    """공백·기호 제거 소문자화. 매칭과 저장에 같은 함수를 쓴다(불일치 방지)."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def _req(method: str, url: str, token: str, **kw):
    """429/5xx·네트워크 오류 백오프. 본문 인덱싱은 요청이 1만 건대라 재시도가 필수다."""
    delay = 1.0
    for attempt in range(5):
        try:
            r = requests.request(method, url, headers=_headers(token), timeout=30, **kw)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 4:
                raise
            time.sleep(15.0 * (2 ** attempt))       # 절전 해제 DNS 경쟁 대비(core/notion과 같은 정책)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            wait = float(r.headers.get("Retry-After") or delay)
            time.sleep(wait)
            delay *= 2
            continue
        return r
    return r


def _page_body(pid: str, token: str, depth: int = 0) -> str:
    """페이지(또는 블록)의 모든 rich_text를 이어붙인다. 중첩은 MAX_DEPTH까지."""
    out: list[str] = []
    cur = None
    while True:
        url = f"{API}/blocks/{pid}/children?page_size=100" + (f"&start_cursor={cur}" if cur else "")
        r = _req("GET", url, token)
        if r.status_code != 200:
            break
        data = r.json()
        for bl in data.get("results", []):
            t = bl.get("type")
            c = bl.get(t) or {}
            if isinstance(c, dict):
                out.append("".join(x.get("plain_text", "") for x in c.get("rich_text", [])))
            if bl.get("has_children") and depth < MAX_DEPTH:
                out.append(_page_body(bl["id"], token, depth + 1))
        if not data.get("has_more"):
            break
        cur = data.get("next_cursor")
        if not cur:
            break
        time.sleep(0.34)
    return " ".join(x for x in out if x)


def _title_of(props: dict) -> str:
    for v in props.values():
        if v.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in v.get("title", []))
    return ""


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            print("⚠️  캐시 파손 — 새로 만든다")
    return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False))
    tmp.replace(CACHE)                      # 원자적 교체 — 중단돼도 캐시가 안 깨진다


def _acquire_lock() -> bool:
    """빌드 동시 실행을 **원천 차단**한다.

    🔴 [2026-07-21] 실제 사고: 진행 중인 빌드가 끝나길 `pgrep -f "./.venv/bin/python …"` 로
       기다리게 했는데, 실행 중 프로세스의 커맨드라인은 **해석된 절대경로**
       (`/opt/homebrew/…/Python tools/body_index.py …`)라 패턴이 안 맞았다. 대기가 즉시 풀려
       빌드 두 개가 동시에 돌았고, 둘 다 캐시 **전체를 통째로 저장**하므로
       **나중에 저장한 쪽이 상대의 작업을 지웠다**(스타트업DB 10분치가 1행으로 남음).
       → 프로세스 이름 매칭은 신뢰할 수 없다. 락파일로 잠근다.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.strftime('%F %T')}".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            pid = int(LOCK.read_text().split()[0])
            os.kill(pid, 0)                       # 살아 있으면 예외 없음
        except (ValueError, IndexError, ProcessLookupError, OSError):
            print(f"⚠️  죽은 락 발견 — 인계한다 ({LOCK})")
            LOCK.unlink(missing_ok=True)
            return _acquire_lock()
        print(f"🔴 다른 빌드가 이미 실행 중이다 (PID {pid}). "
              f"**동시 실행은 캐시를 서로 덮어써 작업을 잃는다** — 끝난 뒤 다시 실행해라.")
        return False


def build(cfg: dict, only: str | None = None) -> dict:
    if not _acquire_lock():
        return {}
    try:
        return _build(cfg, only)
    finally:
        LOCK.unlink(missing_ok=True)


def _build(cfg: dict, only: str | None = None) -> dict:
    token = _token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return {}
    targets = [(e.get("label") or e["id"], e["id"])
               for e in (cfg.get("notion", {}).get("dedup_db_ids") or []) if e.get("id")]
    if only:
        # ⚠️ 라벨 비교는 **정규화**해서 한다. config 라벨이 "스타트업 DB(본DB)"인데
        #    `--db 스타트업DB`로 부르면 공백 하나 때문에 0건 매칭 → 빌드가 0분에 조용히 끝난다
        #    (2026-07-21 실제로 밟음). 이 파일의 다른 매칭들과 같은 실수 계열이다.
        q = norm(only)
        targets = [t for t in targets if q in norm(t[0])]
    if only and not targets:
        print(f"🔴 '--db {only}' 와 매칭되는 대조원이 없다. config notion.dedup_db_ids 라벨 확인:")
        for e in (cfg.get("notion", {}).get("dedup_db_ids") or []):
            print(f"     · {e.get('label')}")
        return {}
    cache = load_cache()
    t_start = time.time()

    for label, db in targets:
        rows, cur = [], None
        while True:
            r = _req("POST", f"{API}/databases/{db}/query", token,
                     json={"page_size": 100, **({"start_cursor": cur} if cur else {})})
            if r.status_code != 200:
                print(f"⚠️  '{label}' query {r.status_code} — 건너뜀")
                break
            d = r.json()
            rows += d.get("results", [])
            if not d.get("has_more"):
                break
            cur = d.get("next_cursor")
        if not rows:
            continue

        todo = [r for r in rows
                if cache.get(r["id"], {}).get("e") != r.get("last_edited_time")]
        print(f"· '{label}': {len(rows)}행 중 **{len(todo)}행** 갱신 필요"
              f" (캐시 적중 {len(rows) - len(todo)})")
        for i, row in enumerate(todo, 1):
            body = _page_body(row["id"], token)
            cache[row["id"]] = {
                "e": row.get("last_edited_time"),
                "db": label,
                "t": _title_of(row.get("properties", {})),
                "u": row.get("url", ""),
                "b": norm(body),                     # 정규화해 저장 = 매칭과 동일 표현
            }
            if i % 25 == 0:
                save_cache(cache)
                el = time.time() - t_start
                print(f"    {i}/{len(todo)} · {el/60:.1f}분 경과 · "
                      f"남은 예상 {(len(todo)-i)*el/i/60:.1f}분", flush=True)
            time.sleep(0.34)                          # 노션 3 req/s 준수
        save_cache(cache)
    print(f"✅ 캐시 {len(cache)}행 · {(time.time()-t_start)/60:.1f}분")
    return cache


def check(names: list[str]) -> None:
    cache = load_cache()
    if not cache:
        print("🔴 캐시 없음 — 먼저 `build`를 돌려라")
        return
    print(f"본문 인덱스 {len(cache)}행 대조\n")
    for name in names:
        q = norm(name)
        if len(q) < MIN_LEN:
            print(f"  ⏭  '{name}' — {MIN_LEN}자 미만은 오탐이 폭발해 건너뛴다")
            continue
        hits = [v for v in cache.values() if q in v.get("b", "")]
        if not hits:
            print(f"  ✅ '{name}' — 본문에 없음")
            continue
        print(f"  ⚠️ '{name}' — {len(hits)}건")
        for h in hits[:5]:
            print(f"       [{h['db']}] {h['t'] or '(제목없음)'}  {h['u']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="대조원 DB 본문 인덱서(증분)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="증분 빌드")
    b.add_argument("--db", help="라벨 부분일치로 특정 DB만")
    c = sub.add_parser("check", help="이름들이 본문에 있는지")
    c.add_argument("names", nargs="+")
    a = ap.parse_args()
    if a.cmd == "build":
        build(yaml.safe_load((ROOT / "config.yaml").read_text()), a.db)
    else:
        check(a.names)


if __name__ == "__main__":
    main()
