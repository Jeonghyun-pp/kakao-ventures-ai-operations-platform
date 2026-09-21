"""중복 착지 페이지 아카이브.

사용법:
    .venv/bin/python tools/archive_dupes.py           # dry-run (목록만)
    .venv/bin/python tools/archive_dupes.py --run      # 실행

🔴 통로(kv_transport.notion)로만 부른다. 전에는 여기서 requests 를 직접 썼는데,
   A1 위반인 것보다 **되읽기가 없다**는 게 실질 문제였다 — status_code 200 을
   성공으로 셌다. 통로의 archive() 는 되읽어 확인하고, 이미 아카이브된 건을
   실패가 아니라 성공으로 센다(멱등).
"""
import json, os, sys, time

os.environ.setdefault("KV_DATA", os.path.expanduser("~/kv-data"))
os.environ.setdefault("KV_CONFIG", os.path.expanduser("~/kv-config"))

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from kvlib import paths
from kv_transport import notion

path = paths.data("_ledger") / "appstore_dupes_to_archive.json"
with open(path) as f:
    targets = json.load(f)

print(f"아카이브 대상: {len(targets)}개")

if "--run" not in sys.argv:
    for t in targets[:10]:
        print(f"  {t['app_id']}  {t['name'][:50]}")
    if len(targets) > 10:
        print(f"  ... 외 {len(targets) - 10}개")
    print("\n--run 플래그를 붙여야 실행됩니다.")
    sys.exit(0)

archived = failed = 0
for i, t in enumerate(targets):
    res = notion.archive(t["page_id"])
    if res.is_measured:
        archived += 1
    else:
        failed += 1
        print(f"  ✗ {t['page_id']} ({t['name'][:30]}): {res.reason}")
    if (i + 1) % 50 == 0:
        print(f"  진행 {i+1}/{len(targets)} (성공 {archived} · 실패 {failed})")
    time.sleep(0.35)

print(f"\n완료: 아카이브 {archived} · 실패 {failed} / 총 {len(targets)}")
