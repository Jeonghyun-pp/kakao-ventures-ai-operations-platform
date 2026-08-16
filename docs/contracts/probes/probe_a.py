#!/usr/bin/env python3
"""[0] 미실측 3종 실측 — Gmail 라벨 · 구글시트 헤더 · Slack 채널. **읽기 전용.**

호출: labels().list · gspread get_all_values(헤더/카운트만 사용) · conversations.info
쓰기 계열(send·create·update·PATCH) 0. 시트는 **헤더 행과 행 수만** 쓰고 데이터 값은 출력하지 않는다
(개인정보). 실패해도 다음 항목으로 계속 간다 — 하나가 막혀 나머지를 못 보면 안 된다.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

REPO = Path.home() / "kv-automation"
sys.path.insert(0, str(REPO / "kv-core"))
sys.path.insert(0, str(REPO / "skills" / "mail-harness" / "project"))

from kv_core import secrets  # noqa: E402

secrets.load()
OUT: dict = {"gmail": {}, "sheet": {}, "slack": {}, "errors": []}


def _err(where: str, e: Exception) -> None:
    OUT["errors"].append({"where": where, "type": type(e).__name__, "msg": str(e)[:300]})
    print(f"🔴 {where}: {type(e).__name__}: {str(e)[:200]}")


# ─────────────────────────── ① Gmail 라벨 ───────────────────────────
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    tok = secrets.credential("google/token.json")
    creds = Credentials.from_authorized_user_file(
        str(tok), ["https://www.googleapis.com/auth/gmail.modify"])
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    user = [l for l in labels if l.get("type") == "user"]
    OUT["gmail"] = {
        "total": len(labels),
        "system": sorted(l["name"] for l in labels if l.get("type") == "system"),
        "user": sorted(l["name"] for l in user),
        "user_ids": {l["name"]: l["id"] for l in user},
        "scope_note": "gmail.modify — send 를 허용한다. 무발송 보장은 스코프가 아니라 코드에 send 호출 0",
    }
    prof = svc.users().getProfile(userId="me").execute()
    OUT["gmail"]["address"] = prof.get("emailAddress")
    OUT["gmail"]["messages_total"] = prof.get("messagesTotal")
    print(f"✅ Gmail: 라벨 {len(labels)}개(사용자 {len(user)}) · {prof.get('emailAddress')}")
except Exception as e:  # noqa: BLE001
    _err("gmail", e)

# ─────────────────────────── ② 구글시트 헤더 ───────────────────────────
try:
    import gspread
    from jobs.bplan_sync_job import _COLS, _TS_COL

    cfg = {"sheet": os.environ.get("SPREADSHEET_URL"),
           "ws": os.environ.get("WORKSHEET_NAME", "비플랜DB2"),
           "creds": os.environ.get("GOOGLE_CREDENTIALS_FILE")}
    gc = gspread.service_account(filename=cfg["creds"])
    sh = gc.open_by_url(cfg["sheet"])
    ws = sh.worksheet(cfg["ws"])
    rows = ws.get_all_values()
    headers = rows[0] if rows else []

    # `_resolve_columns` 와 **같은 규칙**으로 대조 (접두 매칭 · 정확히 1개여야 통과)
    resolved, ambiguous, missing = {}, {}, []
    for prop, prefix in {**_COLS, "타임스탬프": _TS_COL}.items():
        hit = [i for i, h in enumerate(headers) if (h or "").strip().startswith(prefix)]
        if len(hit) == 1:
            resolved[prop] = hit[0]
        elif len(hit) > 1:
            ambiguous[prop] = hit
        else:
            missing.append(prop)

    OUT["sheet"] = {
        "worksheet": cfg["ws"],
        "worksheets_available": [w.title for w in sh.worksheets()],
        "n_rows_incl_header": len(rows),
        "n_cols": len(headers),
        "headers": headers,                       # 폼 질문 문구 — 개인정보 아님
        "resolved": resolved,
        "ambiguous": ambiguous,
        "missing": missing,
        # `tools/bplan_backfill_key.py` 가 **위치 인덱스**로 가정하는 자리
        "positional_assumptions": {
            "r[0] (backfill_key: 타임스탬프)": headers[0] if len(headers) > 0 else None,
            "r[1] (backfill_key: 이메일)": headers[1] if len(headers) > 1 else None,
            "r[13] (backfill_key: 기업명)": headers[13] if len(headers) > 13 else None,
        },
    }
    print(f"✅ 시트: {len(rows)}행 × {len(headers)}열 · 해결 {len(resolved)}/{len(_COLS)+1} "
          f"· 모호 {len(ambiguous)} · 누락 {len(missing)}")
except Exception as e:  # noqa: BLE001
    _err("sheet", e)

# ─────────────────────────── ③ Slack 채널 ───────────────────────────
try:
    import requests

    tok = secrets.get("SLACK_BOT_TOKEN", required=False)
    ch = secrets.get("SLACK_APPROVAL_CHANNEL", required=False)
    if not tok or not ch:
        raise RuntimeError(f"미설정 — token={bool(tok)} channel={bool(ch)}")
    h = {"Authorization": f"Bearer {tok}"}
    info = requests.get("https://slack.com/api/conversations.info",
                        headers=h, params={"channel": ch}, timeout=15).json()
    auth = requests.get("https://slack.com/api/auth.test", headers=h, timeout=15).json()
    c = info.get("channel") or {}
    OUT["slack"] = {
        "auth_ok": auth.get("ok"), "bot_user": auth.get("user"), "team": auth.get("team"),
        "channel_id": ch, "channel_ok": info.get("ok"), "channel_error": info.get("error"),
        "channel_name": c.get("name"), "is_private": c.get("is_private"),
        "is_member": c.get("is_member"), "is_archived": c.get("is_archived"),
        "num_members": c.get("num_members"),
        "webhook_set": bool(secrets.get("SLACK_WEBHOOK_URL", required=False)),
    }
    print(f"✅ Slack: bot={auth.get('user')} · #{c.get('name')} "
          f"· member={c.get('is_member')} · private={c.get('is_private')}")
except Exception as e:  # noqa: BLE001
    _err("slack", e)

# ─────────────────────────── 저장 ───────────────────────────
dst = Path(__file__).resolve().parent / "probe_a_result.json"
dst.write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ {dst}")
print(f"오류 {len(OUT['errors'])}건" + ("" if not OUT["errors"] else
      ": " + ", ".join(e["where"] for e in OUT["errors"])))
