"""launchd 스케줄러 — 매주 화 08:00 무인 실행 등록/해제/상태. [E1/E2]

assets/net-sourcing.plist 를 ~/Library/LaunchAgents/ 에 심볼릭/복사 후 launchctl 로 로드.
대안(Claude 루틴): schedule 스킬 / CronCreate 로 크론에 Claude Code 를 띄워 이 스킬 호출.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

LABEL = "com.kakaoventures.net-sourcing"
PLIST_SRC = Path(__file__).resolve().parent.parent / "assets" / "net-sourcing.plist"
PLIST_DST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# 🔴 스케줄러가 **두 벌** 있다. 둘 다 `main.py run --channel all`을 돌린다.
#    · com.kv.net-sourcing.daily        — install-cron.sh, **매일 08:00** (현재 로드된 것)
#    · com.kakaoventures.net-sourcing   — 이 파일, 매주 화 08:00
#    둘 다 로드되면 **화요일에 두 번 돌아 두 번 과금된다.** 스레드 채널은 실행당 $3~8이다.
DAILY_LABEL = "com.kv.net-sourcing.daily"


def _loaded(label: str) -> bool:
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return any(label in ln for ln in r.stdout.splitlines())


def dispatch(cfg: dict, action: str) -> None:
    if action == "install":
        _install()
    elif action == "uninstall":
        _uninstall()
    else:
        _status()


def _install() -> None:
    if _loaded(DAILY_LABEL):
        print(f"🔴 이미 '{DAILY_LABEL}'(매일 08:00)이 로드돼 있다 — 주간 잡을 **설치하지 않는다.**")
        print("   둘 다 켜면 화요일에 같은 수집이 두 번 돌아 Apify 비용이 두 배로 나간다.")
        print(f"   주간으로 바꾸려면 먼저: ./install-cron.sh remove")
        return
    PLIST_DST.parent.mkdir(parents=True, exist_ok=True)
    PLIST_DST.write_text(PLIST_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(PLIST_DST)], capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST_DST)], check=True)
    print(f"✅ 등록 완료: 매주 화 08:00 → {PLIST_DST}")


def _uninstall() -> None:
    subprocess.run(["launchctl", "unload", str(PLIST_DST)], capture_output=True)
    if PLIST_DST.exists():
        PLIST_DST.unlink()
    print("✅ 해제 완료")


def _status() -> None:
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    hits = [ln for ln in r.stdout.splitlines() if LABEL in ln or DAILY_LABEL in ln]
    print("\n".join(hits) if hits else "미등록 상태")
    if len(hits) > 1:
        print("🔴 스케줄러가 둘 다 로드됐다 — 화요일에 이중 실행/이중 과금된다. 하나를 내려라.")
