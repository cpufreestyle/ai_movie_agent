@echo off
cd /d "%~dp0"
set "PY=d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" supervise.py --status
pause
