@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" (
  echo [Error] Windows environment is missing.
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)

start "" "http://127.0.0.1:8000"
".venv-win\Scripts\python.exe" cli.py webui --host 127.0.0.1 --port 8000
