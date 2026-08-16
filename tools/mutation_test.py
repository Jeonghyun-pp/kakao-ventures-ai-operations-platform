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
MUTATIONS = [
    ("A1 통로 단일화",
     "skills/appstore/run.py",
     "import argparse",
     "import argparse\nimport requests  # 변이: 통로 밖에서 망을 부른다"),
    ("A3 rc 로 성공 판정 금지",
     "skills/appstore/claude.py",
     "    if r.returncode != 0:",
     "    if r.returncode == 0 and False:  # 변이: rc 로 판정\n    if r.returncode != 0:"),
    ("A4 되읽기",
     "skills/appstore/notion.py",
     "    bad = _verify(db_id, page_id, props)\n    if bad:\n        return Fetched.unknown(f\"readback_mismatch:{bad}\", page_id=page_id)\n    return Fetched.measured(page_id)\n\n\ndef update",
     "    return Fetched.measured(page_id)  # 변이: 되읽기 제거\n\n\ndef update"),
    ("A6 제약 숫자 하드코딩 금지",
     "skills/appstore/notion.py",
     "    limit = limit or constraints.get(\"notion.rich_text_limit\")",
     "    limit = limit or 2000  # 변이: 숫자를 코드에 박는다"),
]


def run_check() -> bool:
    r = subprocess.run([PY, "check_intent.py"], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    if not run_check():
        print("🔴 변이 전에 이미 검사가 실패한다 — 먼저 그걸 고쳐라")
        return 1

    bit, blind = [], []
    for name, relpath, old, new in MUTATIONS:
        p = ROOT / relpath
        original = p.read_text(encoding="utf-8")
        if old not in original:
            blind.append((name, "변이 지점을 못 찾았다 — 코드가 바뀌었다"))
            continue
        try:
            p.write_text(original.replace(old, new, 1), encoding="utf-8")
            if run_check():
                blind.append((name, "🔴 방어를 없앴는데 검사가 통과했다 — 이 검사는 안 문다"))
            else:
                bit.append(name)
        finally:
            p.write_text(original, encoding="utf-8")

    print("=" * 68)
    for n in bit:
        print(f"  ✅ {n} — 방어를 없애자 검사가 물었다")
    for n, why in blind:
        print(f"  🔴 {n} — {why}")
    print("=" * 68)
    print(f"  문다 {len(bit)} · 안 문다 {len(blind)}")

    if not run_check():
        print("🔴 복원 후에도 검사가 실패한다 — 파일이 복구되지 않았다!")
        return 1
    print("  (모든 변이를 되돌렸고 검사가 다시 통과한다)")
    return 1 if blind else 0


if __name__ == "__main__":
    sys.exit(main())
