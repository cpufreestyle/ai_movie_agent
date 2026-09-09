@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (
  echo [Error] 未找到 python。请先安装 Python 3.10+ 并勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)

if not exist ".venv-win\Scripts\python.exe" (
  python -m venv .venv-win
)
call .venv-win\Scripts\activate.bat
python -m pip install -U pip
pip install -r requirements.txt

echo.
echo 初始化完成。下一步：
echo   1) 安装并启动 Ollama，拉取模型：  ollama pull qwen2.5:3b
echo   2) 在有 NVIDIA 显卡的机器上启动 ComfyUI，并装好 LTX-2.5 / MiniMax H3 节点
echo      （若无显卡，把 config.yaml 的 engine.comfyui_mmH3.api / comfyui_ltx.api 指向远程 ComfyUI）
echo   3) 运行 start_webui_windows.bat 打开 http://127.0.0.1:8000
echo.
pause
