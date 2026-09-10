#!/usr/bin/env bash
# 一键安装 ComfyUI 性能/质量 插件（见 docs/comfyui_plugins.md）。
# 用法：
#   COMFYUI_ROOT=/path/to/ComfyUI bash setup_comfy_plugins.sh
#   COMFYUI_ROOT=/path/to/ComfyUI GPU_BACKEND=amd bash setup_comfy_plugins.sh
set -euo pipefail

ROOT="${COMFYUI_ROOT:-$HOME/ComfyUI}"
GPU_BACKEND="${GPU_BACKEND:-nvidia}"
echo "==> ComfyUI 根目录: $ROOT"
echo "==> 显卡后端: $GPU_BACKEND"

if [ ! -d "$ROOT" ]; then
  echo "!! 找不到 ComfyUI 目录，请用 COMFYUI_ROOT 指定（如 COMFYUI_ROOT=D:/ComfyUI）"
  exit 1
fi

# 1) Python 解释器：优先用 ComfyUI 自带 venv
PY="$ROOT/venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
echo "==> 使用 Python: $PY"

# 2) 性能后端：SageAttention（NVIDIA 装；AMD/ROCm 用 sageattention 的 rocm  wheel 或跳过）
if [ "$GPU_BACKEND" = "nvidia" ]; then
  echo "==> 安装 SageAttention（NVIDIA，采样提速 + 省显存）"
  "$PY" -m pip install --upgrade sageattention || \
    echo "!! SageAttention 安装失败，启动 ComfyUI 时请勿加 --sage-attention"
else
  echo "==> AMD/ROCm：尝试安装 SageAttention（需匹配 ROCm 的 wheel，失败可跳过）"
  "$PY" -m pip install --upgrade sageattention || \
    echo "!! AMD 下 SageAttention 可能不兼容，已跳过（默认后端仍可用）"
fi

# 3) 质量自定义节点（帧插值 / 综合修复）
CUSTOM="$ROOT/custom_nodes"
mkdir -p "$CUSTOM"
install_git() {
  local url="$1" name
  name="$(basename "$url")"
  if [ -d "$CUSTOM/$name" ]; then
    echo "==> 已存在: $name（跳过）"
  else
    echo "==> 克隆: $name"
    git -C "$CUSTOM" clone --depth 1 "$url" || echo "!! 克隆失败: $url"
  fi
}
install_git https://github.com/AndrewB22/ComfyUI_Frame_Interpolation   # RIFE 帧插值
install_git https://github.com/AUTOMATIC1111/ComfyUI-SUPIR             # SUPIR 综合修复

# 4) 提示：超分模型需手动下载到 models/upscale_models
echo
echo "==> 完成。还需手动下载超分模型到 $ROOT/models/upscale_models/："
echo "   - 4x-UltraSharp.pth   （常用锐利）"
echo "   - 4x_NMKD-Siax_200k.pth（动画平滑）"
echo "   然后在 config.yaml 的 engine.comfyui_mmH3.post / comfyui_ltx.post 打开 upscale_model。"
echo "==> 启动加性能开关： python launch_comfy.py --sage-attention"
