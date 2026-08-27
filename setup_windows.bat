@echo off
setlocal
cd /d "%~dp0"

set "MINIMAX_PYTHON=F:\MinimaxH3-v260808\walkingwithai\python.exe"
if not exist "%MINIMAX_PYTHON%" (
  echo [Error] MinimaxH3 Python was not found:
  echo %MINIMAX_PYTHON%
  echo Edit MINIMAX_PYTHON in this file, then run it again.
  pause
  exit /b 1
)

if not exist ".venv-win\Scripts\python.exe" (
  "%MINIMAX_PYTHON%" -m venv --system-site-packages .venv-win
)

".venv-win\Scripts\python.exe" -m pip install flask pyyaml openai
echo.
echo Setup complete.
echo Start MinimaxH3 first, then run start_webui_windows.bat.
pause
