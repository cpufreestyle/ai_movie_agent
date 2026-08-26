#!/usr/bin/env bash
# AI 电影 Agent — WSL 一键初始化
# 用法: 在 WSL 中 cd 到本目录后执行  bash setup_wsl.sh
set -e
cd "$(dirname "$0")"
AGENT_DIR="$(pwd)"
echo "==> AI 电影 Agent 初始化 (WSL: $(uname -a))"

# 1. 系统依赖
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg git python3-venv python3-pip

# 2. 克隆 SkyReels-V2（视频引擎，Diffusion Forcing 无限时长）
if [ ! -d skyreels_v2 ]; then
  git clone https://github.com/SkyworkAI/SkyReels-V2.git skyreels_v2
else
  echo "    skyreels_v2 已存在，跳过克隆"
fi

# 3. 虚拟环境
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip

# 4. 安装依赖：Agent 自身 + SkyReels 推理栈（含 torch 等，较大）
pip install -r requirements.txt
pip install -r skyreels_v2/requirements.txt

# 5. 安装 biliup（B 站自动投稿，H 阶段）。优先 cargo，否则下载预编译二进制
if ! command -v biliup >/dev/null 2>&1; then
  if command -v cargo >/dev/null 2>&1; then
    cargo install biliup-rs
  else
    echo "    未检测到 cargo，请手动安装 biliup-rs："
    echo "    https://github.com/biliup/biliup-rs/releases"
    echo "    下载 linux 二进制放到 ~/.local/bin 并 chmod +x"
  fi
fi

echo ""
echo "==> 初始化完成。"
echo "    首次运行 'python cli.py run' 会自动从 HuggingFace 下载模型权重"
echo "    (1.3B-540P 约 5~10GB；如需代理: export HTTPS_PROXY=... HTTP_PROXY=...)"
echo "    启动持续创作:"
echo "        source .venv/bin/activate && python cli.py run --continuous"
