@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행: 필요한 프로그램을 설치합니다...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)
start "" http://localhost:3800
".venv\Scripts\python.exe" -m animalpick.server
pause