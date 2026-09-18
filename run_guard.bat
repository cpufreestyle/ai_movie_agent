@echo off
rem 外层 guard：supervisor 无论因何退出都重新拉起（配合 supervisor 内置自看门狗 => 双保险）。
cd /d "%~dp0"
set "PY=d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe"
if not exist "%PY%" set "PY=python"
title ai_movie_agent supervisor guard
:loop
echo [guard] %date% %time% 启动 supervisor
"%PY%" supervise.py
echo [guard] supervisor 退出 rc=%errorlevel%，5 秒后重启
timeout /t 5 /nobreak >nul
goto loop
