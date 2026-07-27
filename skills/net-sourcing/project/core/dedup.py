"""2단 중복 제거 — 기존과 겹치는 후보를 걸러 신규만 통과. [C6]

  ① 주간 소싱 DB   : LinkedIn URL / 홈페이지 URL 정확매칭 (go-forward, 완벽)   [C4]
  ② 레거시 2개 DB : 극초기 + 스타트업 DB 이름(대표이사·회사명) 퍼지매칭 (소프트)
  (③ 노션 전역검색 소프트 안전망 = 판단/착지 단계에서 '가능중복' 태깅 — 여기선 하드드롭 안 함)

후보는 여러 매칭 키(ext_id/URN · li:URL · app_id · home:URL · name)를 갖고, 그중 하나라도
기존 집합에 있으면 중복으로 본다(base.Candidate.match_keys 규칙과 동일).
"""
from __future__ import annotations

from adapters.base import (_normalize, _normalize_linkedin_url, _normalize_url,
                           canonical_linkedin_urn)


def filter_new(cfg: dict, raws: list[dict]) -> list[dict]:
    """기존(주간+레거시 DB) 및 배치 내부 중복을 제거하고 신규만 반환."""
    from core import notion
    seen = notion.weekly_url_keys(cfg)        # ① 주간 DB URL 정확매칭 키
    seen |= notion.existing_dedup_keys(cfg)   # ② 레거시 DB 이름 퍼지 키
    fresh = []
    for c in raws:
        keys = _keys(c)
        # ★ 변화 감지 후보는 dedup을 통과시킨다.
        #   이미 노션에 있는 사람이지만 **프로필이 바뀌었다**(예: 회사명 Stealth → 실제 사명).
        #   그게 우리가 찾는 가장 이른 신호인데, URL이 같다고 버리면 그 순간을 영영 놓친다.
        if (c.get("signals") or {}).get("profile_changed"):
            fresh.append(c)
            continue
        if any(k in seen for k in keys):
            continue
        fresh.append(c)
        seen.update(keys)                     # 이번 배치 내 중복도 제거
    return fresh


def _keys(c: dict) -> list[str]:
    """dict 후보의 매칭 키 전부 (Candidate.match_keys 미러)."""
    keys: list[str] = []
    if c.get("ext_id"):
        # ★ [2026-07-27] 링크드인 URN은 스크래퍼 모드마다 3번째 글자와 꼬리가 바뀐다.
        #   정규화 안 하면 같은 사람이 두 키로 갈려 dedup을 통과한다 —
        #   실제로 노션에 **84행**이 그렇게 생겼다. base.Candidate.match_keys 와 같이 고쳤다.
        keys.append("ext:" + (canonical_linkedin_urn(c["ext_id"]) or c["ext_id"]))
    if c.get("linkedin_url"):
        keys.append("li:" + _normalize_linkedin_url(c["linkedin_url"]))
    if c.get("app_id"):
        keys.append(f"app:{c['app_id']}")
    # 🔴 [2026-07-14] **앱스토어는 home: 키를 쓰지 않는다.**
    #   앱스토어의 homepage = `seller_url` = **개발사 홈페이지**이지 앱의 신원이 아니다.
    #   같은 개발사가 새 앱을 내면 seller_url이 같으므로 **새 앱인데 중복으로 버려진다.**
    #   실측(07-14 수집 493건): "한강 수온 측정 앱"이 같은 개발사의 기존 앱 "글자수 세기" 때문에
    #   드롭됐다. 비율은 0.2%지만 **개발사가 DB에 쌓일수록 커지고, 앱을 여러 개 내는 1인 개발자
    #   (= 정확히 우리가 찾는 프로필)에게 집중적으로 걸린다.**
    #   그리고 이 오작동은 **중복 행을 만드는 게 아니라 진짜 새 앱을 지운다** → 눈에 안 보인다.
    #   ✅ 대신 `app:{app_id}`가 정확 고유키다(notion.weekly_url_keys가 App ID 칸을 읽는다).
    #   ※ 링크드인/스레드는 그대로 유지 — 링크드인 homepage는 실측 0% 채워짐이라 영향이 없고,
    #     회사 URL이 채워지기 시작하면 그땐 **공동창업자 2명이 충돌**하므로 같이 재검토할 것.
    #   🔴 [2026-07-27] **링크드인도 `home:`을 쓰지 않는다 — 앱스토어와 똑같은 이유다.**
    #      오늘 `Homepage`에 **회사 LinkedIn URL**을 싣기 시작했다(실측 94.4% 채워짐).
    #      그런데 회사는 **공동창업자가 공유한다** → 두 번째 창업자가 `home:` 키에 걸려 사라진다.
    #      실측: 착지 745행 중 **같은 회사 URL을 쓰는 행이 27곳/145행**
    #      (stealth-startup-community ×62 · playad-ai ×5 · coupang ×3).
    #      이번 실행엔 안 터졌다 — 그 회사 URL들이 **오늘 처음 생겼기** 때문이다. 다음 실행부터 터진다.
    #      ⚠️ 바로 위 앱스토어 주석이 이 상황을 예언했다("회사 URL이 채워지기 시작하면 공동창업자
    #         2명이 충돌하므로 같이 재검토할 것"). 그 조건이 오늘 충족됐다.
    if c.get("homepage") and not str(c.get("adapter", "")).startswith(("appstore", "linkedin")):
        keys.append("home:" + _normalize_url(c["homepage"]))
    if c.get("url"):
        # 글 단위 채널(스레드)의 유일한 안정 키. 이게 없으면 아래 name 키를 빼는 순간
        # 같은 글을 여러 키워드 쿼리가 중복 반환한 것(247건 중 141건)이 전부 되살아난다.
        keys.append("url:" + _normalize_url(c["url"]))
    # name 키는 ②단 레거시 DB(극초기·스타트업 — 대표이사·회사명을 제목에 저장) 퍼지대조용이다.
    # 스레드의 name 은 사람·회사명이 아니라 **핸들**(@zenonplane)이라 그 대조에 쓸 수 없다.
    # 게다가 핸들로 대조하면 "이미 착지한 빌더의 새 글"이 영구 차단돼 워치리스트 층이 통째로 죽는다.
    #   🔴 [2026-07-27] **링크드인도 `name:`을 쓰지 않는다.**
    #      이 키는 회사명·대표명이 제목인 **레거시 DB 대조용**인데, 링크드인은 제목이 사람 이름이라
    #      흔한 이름이 통째로 오제외된다. 실측(495명): `li:` 키가 **492명**을 잡았고 `name:`이
    #      추가로 잡은 건 **3명뿐**인데 그 3명이 전부 오제외 의심이었다 —
    #      `Minsu Kim`(AutoLabs Co-Founder/COO) · `Minsu Lee` · `Example Person G`.
    #      → 중복 방지력은 `li:`(프로필 URL 정확매칭)가 사실상 전부 담당한다. name은 손해만 남는다.
    if c.get("name") and not str(c.get("adapter", "")).startswith(("threads", "linkedin")):
        keys.append("name:" + _normalize(c["name"]))
    return keys
