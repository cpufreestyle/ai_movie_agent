#!/usr/bin/env bash
# 尽力探测本机 GPU 厂商，给出 docker compose 的 profile 建议（仅供参考，非强制）。
# 用法:  bash detect_gpu.sh
set +e

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "GPU: NVIDIA 检测到"
  echo "  -> 建议:  docker compose --profile gpu up -d"
  echo "  -> 权重:  python download_mmh3_models.py --gpu nvidia   (默认)"
  exit 0
fi

if (command -v rocminfo >/dev/null 2>&1 && rocminfo >/dev/null 2>&1) \
   || ls /dev/kfd >/dev/null 2>&1; then
  echo "GPU: AMD(ROCm) 检测到"
  echo "  -> 建议:  docker compose -f docker-compose.yml -f docker-compose.amd.yml --profile gpu up -d"
  echo "  -> 权重:  python download_mmh3_models.py --gpu amd   (bf16/INT8 变体)"
  echo "  -> 注意:  NVFP4/int4_convrot 为 NVIDIA 专属，AMD 须用 bf16/GGUF/INT8 权重"
  exit 0
fi

echo "GPU: 未检测到独显 / 无 ROCm"
echo "  -> 文案链路(A-F)可照常跑；视频阶段请把 COMFYUI_API 指向远程有显卡的 ComfyUI"
echo "  -> 例如 .env:  COMFYUI_API=http://<远程显卡机IP>:8188"
