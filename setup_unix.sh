#!/usr/bin/env bash
# AI 电影 Agent — Linux / macOS 一键初始化
# 用法:  bash setup_unix.sh
set -e
cd "$(dirname "$0")"

PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

echo ""
echo "==> 初始化完成。"
echo "    1) 安装并启动 Ollama，拉取模型：  ollama pull qwen2.5:3b"
echo "    2) 在有 NVIDIA 显卡的机器上启动 ComfyUI + LTX-2.5 / MiniMax H3 节点"
echo "       （macOS / 无显卡：修改 config.yaml 的 engine.comfyui_mmH3.api 为远程 ComfyUI 地址）"
echo "    3) 运行 ./start_webui.sh 打开 http://localhost:8000"
