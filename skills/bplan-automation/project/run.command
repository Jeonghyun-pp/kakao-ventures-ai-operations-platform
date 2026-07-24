#!/bin/bash
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
