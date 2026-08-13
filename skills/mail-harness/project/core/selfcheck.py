"""selfcheck — 파이프라인 실행 전 기계 검증 (stage 0).

이 단계만 유일하게 전체를 중단시킨다: 자격증명 없이 반쯤 도는 실행은
상태(레저·processed)를 오염시켜서, 아무것도 안 도는 것보다 나쁘다.

검사 항목 (SKILL.md의 기계 검증 표와 1:1):
  1. config 파싱 + autonomy 불변식            ❌ Error
  2. Gmail 토큰 유효 + 담당자/비플랜 라벨 존재  ❌ Error
  3. 상태 파일(JSON) 무결성                    ❌ Error
  4. 템플릿 변경 미동기화(체크섬 드리프트)      ⚠️ Warning
종료 코드: 0 = OK(경고 허용) / 2 = Error
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml

from kv_core import paths, secrets

PROJ = Path(__file__).resolve().parent.parent
NEVER_AUTO_REQUIRED = {"PARTNERSHIP_DECLINE", "TRUSTED_REF", "RECRUIT", "IMPERSONATION"}
TEMPLATE_CHECKSUM_FILE = paths.data("mail-harness", "harness") / "templates_checksum.txt"


def _gmail_service(token_path: Path):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(
        str(token_path), ["https://www.googleapis.com/auth/gmail.modify"])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("토큰 무효 + refresh 불가 — reauth 필요(브라우저)")
    return build("gmail", "v1", credentials=creds)


def _templates_digest() -> str:
    h = hashlib.sha256()
    for f in sorted((PROJ / "triage" / "templates").glob("*.txt")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def run() -> int:
    errors, warnings = [], []

    # ── 1. config + autonomy 불변식 ──────────────────────────────
    try:
        cfg = yaml.safe_load((PROJ / "config.yaml").read_text())
        auto = cfg["autonomy"]
        missing = NEVER_AUTO_REQUIRED - set(auto.get("never_auto_send", []))
        if missing:
            errors.append(f"never_auto_send에 필수 항목 누락: {missing}")
        overlap = set(auto.get("auto_send_categories", [])) & set(auto.get("never_auto_send", []))
        if overlap:
            errors.append(f"auto_send와 never_auto_send 동시 등재(모순): {overlap}")
        if auto.get("send_enabled") and not auto.get("auto_send_categories"):
            warnings.append("send_enabled=true인데 auto_send_categories가 비어 있음 — 발송할 것이 없다")
        for key in ("daily_send_cap", "veto_window_hours", "unknown_alert_ratio"):
            if key not in auto:
                errors.append(f"autonomy.{key} 누락")
    except Exception as e:
        errors.append(f"config.yaml 파싱 실패: {e}")
        cfg = None

    # ── 2. Gmail 토큰 + 라벨 ─────────────────────────────────────
    if cfg:
        # triage·bplan이 같은 계정을 쓴다(refresh_token 동일). 토큰이 1벌이 됐으므로
        # 연결 확인은 한 번이면 되지만, **라벨은 서로 다르다** — 라벨 검증은 둘 다 돈다.
        _tok = secrets.credential("google/token.json")
        for name, token in (("triage", _tok), ("bplan", _tok)):
            try:
                svc = _gmail_service(token)
                labels = {l["name"] for l in
                          svc.users().labels().list(userId="me").execute().get("labels", [])}
                need = cfg["assignee"]["gmail_label"] if name == "triage" \
                    else cfg["bplan"]["gmail_label"]
                if need not in labels:
                    errors.append(f"[{name}] Gmail 라벨 '{need}' 없음")
            except Exception as e:
                errors.append(f"[{name}] Gmail 연결 실패: {e}")

    # ── 3. 상태 파일 무결성 ──────────────────────────────────────
    # 🔴 [2026-08-13 W12] 이 목록이 **재빌드 뒤 죽은 파일 둘을 검사하고 있었다.**
    #    `bplan/{sync,drop}_ledger.json` 은 은퇴한 `bplan/main.py` 의 장부다. 새 잡은
    #    `bplan_sync_floor.json`·`bplan_drop_sent.jsonl` 을 쓴다. 그런데 옛 파일이
    #    디스크에 **아직 남아 있어서**(재빌드 전 실행의 잔재) 검사는 매번 통과했다 —
    #    죽은 파일을 검증하며 초록을 내고, 살아 있는 파일은 한 번도 안 봤다.
    #    "부재면 continue" 규칙과 맞물려 **영원히 거짓 초록**이 되는 형태다(근원 ④).
    _live = [paths.data("mail-harness", "triage") / "processed.json",
             paths.data("mail-harness") / "bplan_sync_floor.json",
             paths.data("mail-harness") / "bplan_drop_sent.jsonl",
             paths.data("mail-harness", "harness") / "sent_watch.jsonl"]
    for f in _live:
        if not f.exists():
            continue  # 아직 안 생긴 상태 파일은 정상(드랍은 컷오버 전이라 없는 게 맞다)
        try:
            if f.suffix == ".jsonl":
                for line in f.read_text().splitlines():
                    if line.strip():
                        json.loads(line)
            else:
                json.loads(f.read_text())
        except Exception as e:
            errors.append(f"상태 파일 손상: {f.name} — {e} (수동 확인 전까지 실행 금지)")

    # 은퇴한 장부가 남아 있으면 알린다 — 지우지는 않는다(사람의 복구 근거일 수 있다).
    # 남아 있는 동안은 "옛 상태를 현재로 오독할" 위험이 있고, 그게 위 결함의 본체였다.
    _dead = [p for p in (paths.data("mail-harness", "bplan") / "sync_ledger.json",
                         paths.data("mail-harness", "bplan") / "drop_ledger.json")
             if p.exists()]
    if _dead:
        warnings.append(
            "은퇴한 bplan 장부 잔재: " + ", ".join(p.name for p in _dead) +
            f" ({_dead[0].parent}) — 새 잡은 bplan_sync_floor.json·bplan_drop_sent.jsonl 를 쓴다. "
            "이 파일들은 현재 상태가 아니다. 보관이 끝났으면 지워라.")

    # ── 4. 템플릿 드리프트 (Warning) ─────────────────────────────
    digest = _templates_digest()
    if TEMPLATE_CHECKSUM_FILE.exists():
        if TEMPLATE_CHECKSUM_FILE.read_text().strip() != digest:
            warnings.append("templates/*.txt 변경 감지 — Notion 「Hello 메일 템플릿」 원문도 "
                            "같이 고쳤는지 확인 후 `selfcheck --ack-templates` 실행")
    else:
        TEMPLATE_CHECKSUM_FILE.parent.mkdir(parents=True, exist_ok=True)
        TEMPLATE_CHECKSUM_FILE.write_text(digest)

    # ── 결과 ─────────────────────────────────────────────────────
    for w in warnings:
        print(f"⚠️  {w}")
    for e in errors:
        print(f"❌ {e}")
    result = {"kind": "selfcheck", "ok": not errors, "errors": errors, "warnings": warnings}
    print(f"@@HARNESS_RESULT@@ {json.dumps(result, ensure_ascii=False)}", flush=True)
    return 2 if errors else 0


if __name__ == "__main__":
    if "--ack-templates" in sys.argv:
        TEMPLATE_CHECKSUM_FILE.parent.mkdir(parents=True, exist_ok=True)
        TEMPLATE_CHECKSUM_FILE.write_text(_templates_digest())
        print("✅ 템플릿 체크섬 갱신 (Notion 동기화 완료로 간주)")
        sys.exit(0)
    sys.exit(run())
