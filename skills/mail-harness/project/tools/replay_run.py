"""리플레이 러너: 주어진 프로젝트(원본 or 포크)의 rules+route로 코퍼스를 분류한다.
main.py triage의 판정 로직(규칙 → UNKNOWN 폴백 → 심사역 지명 오버라이드 → 라우팅)을
LLM 없이 그대로 재현한다. 사용: replay_run.py <project_dir> <corpus.json> <out.json>"""
import json
import sys
from pathlib import Path

proj = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(proj))
import yaml  # noqa: E402
from core import route, rules  # noqa: E402

cfg_path = proj / "config.yaml"
if not cfg_path.exists():
    cfg_path = proj.parent / "config.yaml"   # 포크는 상위 통합 config
c = yaml.safe_load(cfg_path.read_text())

corpus = json.loads(Path(sys.argv[2]).read_text())
out = {}
for m in corpus:
    h = rules.classify(m, c) or {"category": "UNKNOWN", "reason": "replay: 규칙 미적중(LLM 미사용)"}
    # main.py triage와 동일한 심사역 지명 오버라이드
    partner = rules.find_named_partner(m, c)
    if partner and h["category"] in (rules.EVENT_SPEAKER, rules.PARTNERSHIP, rules.UNKNOWN):
        act = {"action": "forward", "to": partner}
    else:
        act = route.decide(m, h, c)
    out[m["id"]] = {"category": h["category"], "action": act["action"],
                    "to": act.get("to"),
                    "also_reply_to": (act.get("also_reply") or {}).get("to")}

Path(sys.argv[3]).write_text(json.dumps(out, ensure_ascii=False))
print(f"✅ {proj.name}: {len(out)}건 판정 완료")
