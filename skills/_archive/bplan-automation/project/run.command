#!/bin/bash

# 🔴 [2026-08-16] 이 스크립트는 **은퇴**했다. 실행하지 마라.
#
#    이 파일은 `main.py drop` 을 돌린다 — 현행 mail-harness 와 **같은 노션 Bplan DB ·
#    같은 Gmail 인박스**를 만진다. `_archive/README.md` 가 "되살리면 접수건 44건이 중복
#    생성된다"고 적어둔 코드이고, `skills/mail-harness/SKILL.md` 의 HARD-GATE #1 이
#    컷오버 전 `apply·sync·drop` 실행을 **예외 없이** 금지한다.
#
#    지금까지 안 돈 이유는 `.env` 가 없어서였다 — **설계가 아니라 우연이다.**
#    (`load_dotenv` 는 CWD 에서 위로 훑는다. 리포 상위 어디든 `.env` 가 하나 놓이면 돈다.)
#    더블클릭 한 번이 프로덕션 노션에 쓰기를 시작하므로 가드를 코드로 세운다.
#
#    현행 경로: skills/mail-harness/project/jobs/bplan_drop_job.py (컷오버 후)
echo "🔴 이 스크립트는 은퇴했습니다 — 실행하지 않습니다."
echo "   되살리면 현행 mail-harness 와 같은 노션 DB·인박스에 중복 초안이 생깁니다."
echo "   (skills/mail-harness/SKILL.md HARD-GATE #1)"
echo ""
echo "   상태 확인:  python -m kv_core.orrery"
exit 1

# ===== Bplan 드랍메일 실행 (Mac) =====
# 이 파일을 더블클릭하면 가상환경 준비 + 드랍메일 초안 생성까지 자동으로 됩니다.
# (동기화를 실행하려면 터미널에서: .venv/bin/python main.py sync)
cd "$(dirname "$0")" || exit 1

# Mac용 가상환경이 없으면(또는 다른 OS용이면) 새로 만들고 패키지를 설치합니다.
if [ ! -x ".venv/bin/python" ]; then
    echo "[setup] Mac용 가상환경을 만드는 중입니다... (최초 1회, 1~2분 소요)"
    rm -rf .venv
    python3 -m venv .venv || { echo "[error] python3 가 필요합니다. https://www.python.org 에서 설치하세요."; read -r; exit 1; }
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || { echo "[error] 패키지 설치 실패"; read -r; exit 1; }
fi

echo "[run] 드랍메일 초안 생성을 실행합니다."
.venv/bin/python main.py drop

# 작업이 끝나면 창이 바로 닫히지 않게 대기
echo ""
echo "[stop] 작업이 종료되었습니다. 엔터를 누르면 창이 닫힙니다."
read -r
