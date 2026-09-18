@echo off
cd /d "%~dp0"
set "PY=d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe"
if not exist "%PY%" set "PY=python"
echo [start_all] 用 %PY% 启动服务守护（ComfyUI 8188 + WebUI 8000，崩溃自动重启）
"%PY%" supervise.py
