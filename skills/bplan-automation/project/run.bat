@echo off
REM ===== Bplan 드랍메일 실행 (Windows) =====
REM 이 파일을 더블클릭하면 가상환경 준비 + 드랍메일 초안 생성까지 자동으로 됩니다.
REM (동기화를 실행하려면 터미널에서: .venv\Scripts\python main.py sync)
cd /d "%~dp0"

REM Windows용 가상환경이 없으면(또는 다른 OS용이면) 새로 만들고 패키지를 설치합니다.
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Windows용 가상환경을 만드는 중입니다... ^(최초 1회, 1~2분 소요^)
    if exist ".venv" rmdir /s /q .venv
    python -m venv .venv || ( echo [error] python 이 필요합니다. https://www.python.org 에서 설치하세요. & pause & exit /b 1 )
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt || ( echo [error] 패키지 설치 실패 & pause & exit /b 1 )
)

echo [run] 드랍메일 초안 생성을 실행합니다.
.venv\Scripts\python main.py drop

echo.
echo [stop] 작업이 종료되었습니다. 아무 키나 누르면 창이 닫힙니다.
pause
