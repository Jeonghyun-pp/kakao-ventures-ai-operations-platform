"""현행 동작 고정 테스트.

    cd ~/kv && ./.venv/bin/python -m unittest discover -s tests -t . -v

## 이 테스트가 재는 것과 안 재는 것

**재는 것** — 지금 코드가 *하는 일*. 옳은지가 아니다.
    결함으로 알려진 동작도 그대로 고정한다(이름에 `_known_defect` 가 붙는다).
    그 결함을 고치면 그 테스트가 깨지고, 그때 기대값을 **의도적으로** 바꾼다.
    조용히 바뀌는 것만 막는다.

**안 재는 것** — claude 판정, 지메일·슬랙·노션에 닿는 모든 것.
    이 디렉토리의 어떤 테스트도 망을 부르지 않고 메일함을 건드리지 않는다.

## 자료

    리포 안   합성 입력만. 실제 메일의 제목·주소를 넣지 않는다
    리포 밖   ~/kv-data/mail/fixtures/ — 실제 기록에서 얼린 자료(개인정보 포함, 권한 600)
              없으면 그 테스트는 **건너뛰고 그 사실을 말한다.** 조용히 통과하지 않는다
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# Tests never inherit a developer's live credentials or production data.
_ROOT = Path(__file__).resolve().parent
_CONFIG = Path(tempfile.mkdtemp(prefix="kv-test-config-"))
for _name in ("connections.yaml", "people.yaml", "deployment.yaml"):
    shutil.copyfile(_ROOT / "fixtures" / "config" / _name, _CONFIG / _name)
(_CONFIG / ".env").write_text(
    "NOTION_TOKEN=test\nSLACK_BOT_TOKEN=test\nAPIFY_TOKEN=test\n"
    "GMAIL_TOKEN_FILE=/tmp/test-gmail-token.json\n"
    "GOOGLE_CREDENTIALS_FILE=/tmp/test-google-credentials.json\n",
    encoding="utf-8",
)
(_CONFIG / ".env").chmod(0o600)
os.environ["KV_CONFIG"] = str(_CONFIG)
os.environ["KV_DATA"] = tempfile.mkdtemp(prefix="kv-tests-")
