"""변이 테스트 — **검사가 실제로 무는지** 확인한다.

## 왜 필요한가

옛 시스템의 계약 게이트가 스스로 적어둔 한계가 있다:

> "선언했다"만 셀 뿐, 그 검사가 실제로 그 불변식을 재는지는 **증명하지 못한다.**

검사가 있다는 것도 선언이다. 방어 코드를 **되돌려 보고 검사가 실패해야** 그 검사를 인정한다.
안 물면 그 검사는 없는 것이다.

    ~/kv/.venv/bin/python tools/mutation_test.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PY = str(ROOT / ".venv/bin/python")

# (이름, 파일, 되돌릴 것, 되돌린 결과)  — 방어를 무력화했을 때 check_intent 가 실패해야 한다
# (검사ID, 이름, 파일, 되돌릴 것, 되돌린 결과)
MUTATIONS = [
    ("A1", "통로 단일화",
     "skills/appstore/run.py",
     "import argparse",
     "import argparse\nimport requests  # 변이: 통로 밖에서 망을 부른다"),
    ("A3", "rc 로 성공 판정 금지",
     "kv_transport/claude.py",
     '    out = (r.stdout or "").strip()',
     '    ok = r.returncode == 0  # 변이: rc 로 성공을 판정한다\n    out = (r.stdout or "").strip()'),
    ("A4", "되읽기",
     "kv_transport/notion.py",
     "    bad = _verify(db_id, page_id, props)\n    if bad:\n        return Fetched.unknown(f\"readback_mismatch:{bad}\", page_id=page_id)\n    return Fetched.measured(page_id)\n\n\ndef update",
     "    return Fetched.measured(page_id)  # 변이: 되읽기 제거\n\n\ndef update"),
    ("A6", "제약 숫자 하드코딩 금지",
     "kv_transport/notion.py",
     "    limit = limit or constraints.get(\"notion.rich_text_limit\")",
     "    limit = limit or 2000  # 변이: 숫자를 코드에 박는다"),
    ("A0", "검사기가 파싱 오류에 죽지 않는다",
     "kv_transport/slack.py",
     "def _post(text: str) -> bool:",
     "def _post(text: str) -> bool  # 변이: 문법 오류\ndef _post(text: str) -> bool:"),
]


def failing() -> set[str]:
    """지금 실패 중인 검사 ID 집합. 전체 통과 여부가 아니라 **대상 검사가 바뀌는지**를 본다.

    (전체 통과를 전제로 하면, 아직 미설치라 정직하게 실패 중인 항목 하나 때문에
     변이 테스트 자체가 못 돈다. 기준선을 잡고 차이를 보는 게 맞다.)
    """
    r = subprocess.run([PY, "check_intent.py"], cwd=ROOT, capture_output=True, text=True)
    return {ln.split()[1] for ln in r.stdout.splitlines() if ln.strip().startswith("🔴 A")}


def main() -> int:
    base = failing()
    if base:
        print(f"  (기준선: 지금 실패 중인 검사 {sorted(base)} — 이건 변이와 무관하다)\n")

    bit, blind = [], []
    for cid, name, relpath, old, new in MUTATIONS:
        label = f"{cid} {name}"
        if cid in base:
            blind.append((label, "이미 실패 중이라 변이 효과를 못 본다"))
            continue
        p = ROOT / relpath
        original = p.read_text(encoding="utf-8")
        if old not in original:
            blind.append((label, "변이 지점을 못 찾았다 — 코드가 바뀌었다"))
            continue
        try:
            p.write_text(original.replace(old, new, 1), encoding="utf-8")
            if cid in failing():
                bit.append(label)
            else:
                blind.append((label, "🔴 방어를 없앴는데 그 검사가 통과했다 — 안 문다"))
        finally:
            p.write_text(original, encoding="utf-8")

    print("=" * 68)
    for n in bit:
        print(f"  ✅ {n} — 방어를 없애자 검사가 물었다")
    for n, why in blind:
        print(f"  🔴 {n} — {why}")
    print("=" * 68)
    print(f"  문다 {len(bit)} · 안 문다 {len(blind)}")

    if failing() != base:
        print("🔴 복원 후 상태가 기준선과 다르다 — 파일이 복구되지 않았다!")
        return 1
    print("  (모든 변이를 되돌렸고 상태가 기준선으로 돌아왔다)")
    return 1 if blind else 0


if __name__ == "__main__":
    sys.exit(main())
