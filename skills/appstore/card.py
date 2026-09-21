"""앱스토어 슬랙 카드 서식 — 서식은 도메인이라 통로에 두지 않는다."""
from __future__ import annotations

from kv_transport import slack


def send(rows: list[dict], *, ch: str, dry_run: bool = False) -> bool:
    """오늘의 앱 카드. 뭘 하는 앱인지 한 줄 + 링크."""
    lines = [f"*오늘의 앱스토어 {len(rows)}개*"]
    for r in rows:
        name = r.get("서비스명") or r.get("App ID")
        dev = r.get("Developer") or ""
        one = (r.get("One Liner") or "").strip().replace("\n", " ")[:90]
        site = r.get("Homepage") or ""
        aid = r.get("App ID")
        lines.append(
            f"\n• *{name}* — {dev}\n  {one}\n  "
            f"<https://apps.apple.com/kr/app/id{aid}|앱스토어>"
            + (f" · <{site}|개발사>" if site else "")
            + f" · <https://www.notion.so/{str(r.get('_page_id','')).replace('-','')}|노션>"
        )
    text = "\n".join(lines)
    return slack.send_text(ch, text, dry_run=dry_run)
