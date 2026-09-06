#!/usr/bin/env bash
# ============================================================
# LTX-2.5 部署到 AMD Ryzen AI MAX+ 395 (Strix Halo, 128GB UMA, Linux+ROCm)
# 精度: BF16 transformer (~44GB)。无需量化、无需 ComfyUI-GGUF。
# 在 AMD 机器上以 bash setup_amd.sh 运行。
# ============================================================
set -uo pipefail

COMFY_HOME="${COMFY_HOME:-$HOME/ComfyUI}"
MODELS="$COMFY_HOME/models"
HF_REPO="${HF_REPO:-Lightricks/LTX-2.5}"
TRANSFORMER_BF16="${TRANSFORMER_BF16:-ltx-2.5-22b-distilled-transformer-bf16.safetensors}"
# 可选 HF 镜像（国内加速）: export HF_ENDPOINT=https://hf-mirror.com
: "${HF_ENDPOINT:=https://huggingface.co}"

log(){ echo -e "\033[1;32m[setup]\033[0m $*"; }
err(){ echo -e "\033[1;31m[ERR]\033[0m $*" >&2; }

# ---------- 1. 检测 ROCm ----------
log "检测 ROCm ..."
if ! command -v rocm-smi >/dev/null 2>&1; then
  err "未检测到 rocm-smi。请先安装 ROCm 6.2+ (推荐 6.3) 并装 ROCm 版 PyTorch:"
  err "  pip install torch --index-url https://download.pytorch.org/whl/rocm6.2"
  exit 1
fi
rocm-smi --showmeminfo vram 2>/dev/null || true

# ---------- 2. ComfyUI 安装/检测 ----------
log "ComfyUI 路径: $COMFY_HOME"
if [ ! -d "$COMFY_HOME" ]; then
  log "克隆 ComfyUI ..."
  git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFY_HOME"
fi
cd "$COMFY_HOME"

if [ ! -d venv ]; then
  python3 -m venv venv
fi
# 装依赖（注意: ROCm 版 torch 应在建 venv 后单独用 rocm index 安装, 这里跳过 torch）
log "安装 ComfyUI 依赖 (跳过 torch) ..."
./venv/bin/python -m pip install -r requirements.txt --no-deps 2>/dev/null || \
  ./venv/bin/python -m pip install comfyui-frontend-package

# ---------- 3. LTX-2.5 节点 ----------
# ComfyUI v0.32+ 已原生内置 LTX 节点 (LTXVBaseSampler 等)。
# 若缺失可装官方自定义节点:
if [ ! -d custom_nodes/ComfyUI-LTXVideo ] && [ ! -d custom_nodes/LTX-2.5 ]; then
  log "安装 lxxxy6/LTX-2.5 自定义节点 ..."
  git clone https://github.com/lxxxy6/LTX-2.5 custom_nodes/LTX-2.5
fi

# ---------- 4. 下载 BF16 transformer (断点续传) ----------
mkdir -p "$MODELS/diffusion_models"
if [ -f "$MODELS/diffusion_models/$TRANSFORMER_BF16" ]; then
  log "BF16 transformer 已存在, 跳过下载"
else
  log "下载 BF16 transformer: $HF_REPO/$TRANSFORMER_BF16 (HF_ENDPOINT=$HF_ENDPOINT)"
  HF_ENDPOINT="$HF_ENDPOINT" ./venv/bin/python - <<PY
from huggingface_hub import hf_hub_download
import os
f = hf_hub_download(
    repo_id=os.environ["HF_REPO"],
    filename=os.environ["TRANSFORMER_BF16"],
    local_dir=os.path.join(os.environ["MODELS"], "diffusion_models"),
    endpoint=os.environ["HF_ENDPOINT"],
)
print("downloaded ->", f)
PY
fi

# ---------- 5. 文本编码器 / VAE (从本机 Windows 拷贝, 或加 --all 从 HF 下) ----------
log "文本编码器与 VAE 需放置到:"
echo "  $MODELS/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors  (13.17GB, 从本机 Win 拷贝)"
echo "  $MODELS/vae/ltx-2.5-video-vae-bf16.safetensors  (1.47GB, 从本机 Win 拷贝)"
echo "  $MODELS/vae/ltx-2.5-audio-vae-bf16.safetensors  (0.36GB, 从本机 Win 拷贝)"
echo "本机 scp 示例(在 Windows PowerShell 执行):"
echo "  scp D:\\ComfyUI\\models\\text_encoders\\gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors user@AMD_IP:~/ComfyUI/models/text_encoders/"
echo "  scp D:\\ComfyUI\\models\\vae\\ltx-2.5-video-vae-bf16.safetensors user@AMD_IP:~/ComfyUI/models/vae/"
echo "  scp D:\\ComfyUI\\models\\vae\\ltx-2.5-audio-vae-bf16.safetensors user@AMD_IP:~/ComfyUI/models/vae/"

# 若带 --all 且已设 HF_TOKEN, 也可从 HF 直下(文本编码器 gated, 需登录授权)
if [ "${1:-}" = "--all" ] && [ -n "${HF_TOKEN:-}" ]; then
  log "从 HF 直下文本编码器/VAE ..."
  HF_ENDPOINT="$HF_ENDPOINT" ./venv/bin/python - <<PY
from huggingface_hub import hf_hub_download
import os
for fn,sub in [("gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors","text_encoders"),
               ("ltx-2.5-video-vae-bf16.safetensors","vae"),
               ("ltx-2.5-audio-vae-bf16.safetensors","vae")]:
    hf_hub_download(repo_id=os.environ["HF_REPO"], filename=fn,
                    local_dir=os.path.join(os.environ["MODELS"], sub),
                    endpoint=os.environ["HF_ENDPOINT"])
PY
fi

# ---------- 6. 启动 ----------
log "一切就绪后, 在 AMD 机器启动 ComfyUI (ROCm, 跨注意力兼容):"
echo "  cd $COMFY_HOME && ./venv/bin/python main.py --use-pytorch-cross-attention"
echo "然后在本机/AMD 上跑:  python cli.py ltx --prompt \"A cat walking on grass\" --out outputs/ltx_clip.mp4 --frames 33"
