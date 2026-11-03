"""스케줄 — 선언은 스킬 안에, 설치물은 생성물.

## 왜 스케줄러를 안 만드나

launchd 가 이미 "때가 되면 실행"을 한다. 옛 시스템은 그 위에 due 판정·catch-up·
워터마크를 345줄로 다시 구현했다. 우리는 **선언만 모으고 실행은 launchd 에 맡긴다.**

## 왜 중앙 yaml 이 아니라 SKILL.md 인가

중앙에 목록을 두면 두 진실원이 생긴다 — 스킬은 있는데 목록에 없거나 반대거나.
옛 시스템에서 실제로 `tasks.yaml` 15개 중 5개가 미러 문서에서 빠져 있었다.

정본은 **스킬 안의 선언 하나**고, 전체 보기와 plist 는 전부 **생성물**이다.
사본을 만들 바엔 생성물로 만든다.

## 🔴 맥 전용 코드는 **이 파일에만** 있다

다른 기계로 옮길 때 고칠 곳이 여기뿐이다. `check_intent` 의 A13 이 그걸 상시 확인한다.

    이 파일에만 있는 것   launchctl · ~/Library/LaunchAgents · plist 형식
    옮길 때 할 일         plist() 옆에 crontab() 이나 systemd_unit() 을 하나 더 만든다.
                        loaded() 는 `launchctl list` 대신 `crontab -l` 을 읽게 한다.
                        선언(SKILL.md)·스킬·장부·통로는 **안 건드린다** — 그게 이 구조의 요점이다

리눅스(cron)는 들어 있다 [2026-09-25]: `sys.platform` 이 darwin 이 아니면 crontab 블록을 만들고 `crontab -l` 을 읽는다.
`claude -p` 로그인은 **계정**에 묶이는 것이라 서버에서 hello@ 로 `claude login` 한 번이면 된다(브라우저 URL 을 받아 붙인다).
API 로 바꾸지 않는다 — 비용 축이 "쿼터"에서 "돈"으로 바뀌는 걸 ADR 0003 이 피했다.

## 🔴 check 가 이 파일의 존재 이유다

"매일 07시에 돈다"고 SKILL.md 에 적어놓고 실제 launchd 에 없으면, 그게 정확히
**선언은 있는데 발동하지 않는 가드**다. 옛 시스템이 반복해서 밟은 1번 함정이다.

    python schedule.py show      선언 전체 (생성물)
    python schedule.py check     선언 ↔ 실제 설치 대조
    python schedule.py install   plist 생성·적재 (사람이 명시적으로 부른다)
"""
from __future__ import annotations

import html
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from kvlib import paths

ROOT = Path(__file__).resolve().parent
LABEL = "com.kv.{skill}.{cmd}"
AGENTS = Path.home() / "Library/LaunchAgents"
MAC = sys.platform == "darwin"                       # 아니면 cron. 두 백엔드의 차이는 이 파일 안에서 끝난다
_CRON_BEGIN, _CRON_END = "# kv-schedule begin (생성물 — 정본은 skills/*/SKILL.md)", "# kv-schedule end"

_WHEN = re.compile(r"(매일|월|화|수|목|금|토|일)\s+(\d{1,2}):(\d{2})")
# 🔴 주기 실행. "때가 되면"이 아니라 "N분마다"가 필요한 일이 있다 —
#    사람이 슬랙에 이모지를 누르는 시점을 우리가 못 정하기 때문이다.
#    맥이 자는 동안은 안 돈다. 깨어나면 launchd 가 **한 번만** 몰아서 발화한다
#    (밀린 횟수만큼 반복하지 않는다).
_EVERY = re.compile(r"(\d+)\s*분마다")
# 🔴 잡의 PATH 는 **설치한 셸의 PATH** 를 그대로 싣는다. launchd·cron 의 기본 PATH(/usr/bin:/bin…)엔 홈브루가 없다 —
#    2026-10-21 09:20 bplan run 이 덱 PDF 에서 `pdftotext`(poppler, /opt/homebrew/bin)를 못 찾아 판정·보고서·백업이 통째로 멈췄다.
#    claude 처럼 도구마다 절대경로를 찾는 대신 여기 한 곳에서 환경을 맞춘다 — 다음에 들어올 외부 도구도 같이 풀린다.
_PATH = os.environ.get("PATH", "/usr/bin:/bin")
_WEEKDAY = {"월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 0}


def declared() -> list[dict]:
    """모든 스킬의 SKILL.md 에서 스케줄 선언을 모은다. 이게 유일한 정본."""
    out = []
    for md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        fm = yaml.safe_load(text.split("---", 2)[1])
        skill = fm.get("name") or md.parent.name
        for row in fm.get("schedule") or []:
            raw = str(row.get("when", ""))
            # 🔴 `cmd` 에 플래그가 붙을 수 있다 ("apply --no-slack").
            #    ① 라벨은 **첫 낱말만** 쓴다. 안 그러면 플리스트 파일명에 공백과
            #       대시가 들어가고, 플래그를 바꾸는 순간 launchd 는 다른 잡으로 본다
            #       (옛 잡이 남아 둘이 같이 돈다).
            #    ② argv 는 **낱말마다 하나씩** 넘긴다. 통째로 넘기면 argparse 가
            #       "apply --no-slack" 이라는 이름의 하위명령을 찾다가 죽는다.
            argv = shlex.split(str(row["cmd"]))
            if not argv:
                raise ValueError(f"{md}: schedule cmd must not be empty")
            job = {"skill": skill, "cmd": row["cmd"], "argv": argv, "when": raw,
                   "why": row.get("why", ""),
                   "label": LABEL.format(skill=skill, cmd=argv[0])}
            e = _EVERY.match(raw)
            m = _WHEN.match(raw)
            if e:
                job.update(interval=int(e.group(1)) * 60, weekday=None, hour=None, minute=None)
            elif m:
                day, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
                job.update(interval=None, weekday=None if day == "매일" else _WEEKDAY[day],
                           hour=hh, minute=mm)
            else:
                raise ValueError(
                    f"{md}: when 을 못 읽었다: {raw!r} (예: '매일 07:00' 또는 '10분마다')")
            out.append(job)
    dup = sorted({j["label"] for j in out if sum(k["label"] == j["label"] for k in out) > 1})
    if dup:
        # 🔴 라벨이 같으면 뒤 행의 플리스트가 앞 행을 **조용히 덮어쓴다** — 앞 잡이 사라진다.
        #    (2026-09-22 실측: "observe" 와 "observe --op health" 가 둘 다 com.kv.mail.observe 였다)
        raise ValueError(f"스케줄 라벨이 겹친다 — 첫 낱말이 같은 cmd 는 하위명령을 따로 둔다: {', '.join(dup)}")
    return out


def plist(job: dict) -> str:
    if job.get("interval"):
        when = f"  <key>StartInterval</key><integer>{job['interval']}</integer>"
    else:
        cal = (f"<key>Hour</key><integer>{job['hour']}</integer>\n"
               f"    <key>Minute</key><integer>{job['minute']}</integer>")
        if job["weekday"] is not None:
            cal += f"\n    <key>Weekday</key><integer>{job['weekday']}</integer>"
        when = f"  <key>StartCalendarInterval</key><dict>\n    {cal}\n  </dict>"
    log = paths.data_root().resolve() / "_logs" / f"{job['label']}.log"
    argv = "".join(f"<string>{html.escape(a)}</string>" for a in job["argv"])
    env = {"PATH": _PATH, "KV_CONFIG": str(paths.config_root().resolve()), "KV_DATA": str(paths.data_root().resolve())}
    environment = "".join(f"<key>{k}</key><string>{html.escape(v)}</string>" for k, v in env.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- 생성물이다. 손으로 고치지 마라. 정본은 스킬의 SKILL.md schedule 선언이다. -->
<plist version="1.0"><dict>
  <key>Label</key><string>{html.escape(job['label'])}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{html.escape(str(ROOT))}/.venv/bin/python</string>
    <string>-m</string><string>skills.{html.escape(job['skill'])}.run</string>
    {argv}
  </array>
  <key>WorkingDirectory</key><string>{html.escape(str(ROOT))}</string>
{when}
  <key>EnvironmentVariables</key><dict>{environment}</dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{html.escape(str(log))}</string>
  <key>StandardErrorPath</key><string>{html.escape(str(log))}</string>
</dict></plist>
"""


def cron_line(job: dict) -> str:
    """crontab 한 줄. 라벨은 끝의 주석이다 — loaded() 가 그걸 읽는다."""
    if job.get("interval"):
        n = job["interval"] // 60
        if 60 % n:
            raise ValueError(f"{job['label']}: cron 은 60 을 나누는 분 간격만 된다 ({n}분)")
        when = f"*/{n} * * * *"
    else:
        wd = "*" if job["weekday"] is None else job["weekday"]          # 우리 요일표(일=0)가 cron 과 같다
        when = f"{job['minute']} {job['hour']} * * {wd}"
    log = paths.data_root().resolve() / "_logs" / f"{job['label']}.log"
    env = {"PATH": _PATH, "KV_CONFIG": str(paths.config_root().resolve()), "KV_DATA": str(paths.data_root().resolve())}
    assignments = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    argv = ['.venv/bin/python', '-m', f"skills.{job['skill']}.run", *job['argv']]
    command = (f"cd {shlex.quote(str(ROOT))} && {assignments} "
               f"{shlex.join(argv)}"
               f" >> {shlex.quote(str(log))} 2>&1")
    # cron interprets percent signs before the shell, even inside shell quotes.
    if "\n" in command or "\r" in command:
        raise ValueError("cron paths and arguments must not contain newlines")
    command = command.replace('%', r'\%')
    return f"{when} {command}  # {job['label']}"


def _crontab() -> list[str]:
    """지금 crontab. 없으면 stdout 이 비어 빈 목록이다(rc 를 보지 않는다 — A3)."""
    return subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()


def loaded() -> set[str]:
    """실제로 설치된 우리 라벨 — 맥은 launchd, 그 밖은 crontab."""
    if MAC:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        return {ln.split("\t")[-1] for ln in r.stdout.splitlines() if "com.kv." in ln}
    return {ln.rsplit("# ", 1)[-1].strip() for ln in _crontab() if "# com.kv." in ln}


def cmd_show() -> int:
    for j in declared():
        print(f"  {j['when']:10} {j['skill']}.{j['cmd']:8}  {j['why']}")
    return 0


def cmd_check() -> int:
    """🔴 선언 ↔ 설치 대조. 어긋나면 시끄럽게."""
    want = {j["label"]: j for j in declared()}
    have = loaded()
    missing = sorted(set(want) - have)
    extra = sorted(l for l in have - set(want) if l.startswith("com.kv.") and "." in l[7:])

    for label in sorted(want):
        mark = "✅" if label in have else "🔴"
        print(f"  {mark} {want[label]['when']:10} {label}")
    if missing:
        print(f"\n  🔴 선언했는데 설치가 안 됐다 ({len(missing)}개) — 이게 '선언은 있는데 발동 안 하는' 상태다")
        for m in missing:
            print(f"       {m}")
    if extra:
        print(f"\n  ⚠️  설치돼 있는데 선언에 없다 ({len(extra)}개) — 옛 시스템 것일 수 있다")
        for e in extra:
            print(f"       {e}")
    return 1 if missing else 0


def cmd_install(apply: bool) -> int:
    jobs = declared()
    if not MAC:
        return _install_cron(jobs, apply)
    for j in jobs:
        p = AGENTS / f"{j['label']}.plist"
        print(f"  {'쓴다' if apply else '(dry)'} {p.name}  ← {j['when']}")
        if apply:
            AGENTS.mkdir(parents=True, exist_ok=True)
            paths.data("_logs")
            p.write_text(plist(j), encoding="utf-8")
            subprocess.run(["launchctl", "bootout", f"gui/{__import__('os').getuid()}/{j['label']}"],
                           capture_output=True)
            subprocess.run(["launchctl", "bootstrap", f"gui/{__import__('os').getuid()}", str(p)],
                           capture_output=True)
    gone = sorted(l for l in loaded() - {j["label"] for j in jobs} if l.startswith("com.kv.") and "." in l[7:])
    for label in gone:                                              # 선언에서 빠진 우리 잡은 내린다 — 선언이 곧 현실이어야 한다
        print(f"  {'내린다' if apply else '(dry) 내린다'} {label}")
        if apply:
            subprocess.run(["launchctl", "bootout", f"gui/{__import__('os').getuid()}/{label}"], capture_output=True)
            (AGENTS / f"{label}.plist").unlink(missing_ok=True)
    if apply:
        missing = {j["label"] for j in jobs} - loaded()
        if missing:
            raise RuntimeError(f"launchd 에 안 들어갔다: {sorted(missing)}")
    if not apply:
        print("\n  --apply 를 붙이면 실제로 설치한다. 설치하면 그 시각부터 무인으로 돈다.")
    return 0


def _install_cron(jobs: list[dict], apply: bool) -> int:
    """crontab 의 우리 블록만 바꿔 넣는다. 사람이 적어 둔 다른 줄은 그대로."""
    old = _crontab()
    keep = old[:old.index(_CRON_BEGIN)] + old[old.index(_CRON_END) + 1:] if _CRON_BEGIN in old else old
    block = [_CRON_BEGIN, *(cron_line(j) for j in jobs), _CRON_END]
    for ln in block[1:-1]:
        print(f"  {'쓴다' if apply else '(dry)'} {ln}")
    if apply:
        paths.data("_logs")
        subprocess.run(["crontab", "-"], input="\n".join([*keep, *block]) + "\n", text=True)
        missing = {j["label"] for j in jobs} - loaded()                # 🔴 쓴 것은 되읽는다 — rc 가 아니라 crontab -l 로 판정
        if missing:
            raise RuntimeError(f"crontab 에 안 들어갔다: {sorted(missing)}")
    else:
        print("\n  --apply 를 붙이면 crontab 에 넣는다. 설치하면 그 시각부터 무인으로 돈다.")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "show"
    if cmd == "show":
        return cmd_show()
    if cmd == "check":
        return cmd_check()
    if cmd == "install":
        return cmd_install("--apply" in argv)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
