#!/usr/bin/env bash
# =============================================================================
# 在 DGX Spark (GB10 Grace Blackwell, Linux aarch64) 上部署 Sol-H3-Spark 视频服务
# -----------------------------------------------------------------------------
# 由 ai_movie_agent 的 HTTP 客户端（tools/sol_h3_client.py）远程调用出视频。
#
# 重要：NVIDIA 官方明确声明 "A clean installation has not yet been validated"，
# 且 DGX Spark 是 aarch64，CUDA 扩展（fastvideo-kernel、all2all_cpp）需源码编译。
# 本脚本按 models/minimax_h3/Sol-H3-Spark/docs/setup.md 编排；三环境（Stage1/Stage2/
# Qwen）的创建属于未验证步骤，可能需你按官方文档手动微调。脚本对可自动化部分用 set -e。
#
# 前置：
#   - Ubuntu 22.04/24.04 + CUDA 13 + cuDNN frontend 1.27.0
#   - git / conda(或 venv) / docker（Qwen 用 Docker 构建）
#   - HF 账号已接受 LTX-2.5 访问条款；HF_TOKEN 有该仓库读取权限
#
# 用法：
#   export HF_TOKEN=hf_xxx
#   export REPO_DIR=$HOME/Sol-H3-Spark SERVER_PORT=8000
#   bash deploy_sol_h3_spark.sh
# =============================================================================
set -euo pipefail

# ============ 可配置变量（按你的 DGX Spark 修改）============
export REPO_DIR="${REPO_DIR:-$HOME/Sol-H3-Spark}"          # Sol-H3-Spark 目录（含 infer.py）
export RUNTIME_ROOT="${RUNTIME_ROOT:-$HOME/sol_h3_runtime}"  # 运行时根（须预存在、可写）
export PROMPT_CACHE="${PROMPT_CACHE:-$RUNTIME_ROOT/fixed-prompt.pt}"
export QWEN_IMAGE="${SOL_H3_SPARK_QWEN_IMAGE:-sol-h3-spark-qwen}"
export HF_TOKEN="${HF_TOKEN:-}"                            # 已接受 LTX-2.5 条款的 HF token
export SERVER_PORT="${SERVER_PORT:-8000}"
export SERVER_HOST="${SERVER_HOST:-0.0.0.0}"
# 三个环境的解释器（按官方 setup.md 创建后填入绝对路径）
export STAGE1_PY="${STAGE1_PY:-$REPO_DIR/.venv_stage1/bin/python}"
export STAGE2_PY="${STAGE2_PY:-$REPO_DIR/.venv_stage2/bin/python}"
export QWEN_PY="${QWEN_PY:-$REPO_DIR/qwen_python.sh}"       # 或 docker 封装脚本
export SERVER_PY="${SERVER_PY:-python}"                     # 能调 infer.py 的解释器

mkdir -p "$RUNTIME_ROOT"

# ============ 0. 获取代码（若尚未存在）============
if [ ! -f "$REPO_DIR/infer.py" ]; then
  echo "[0] 克隆 NVlabs/Sana (sol-engine) 并取出 Sol-H3-Spark ..."
  TMP=$(mktemp -d)
  git clone --branch sol-engine --depth 1 https://github.com/NVlabs/Sana.git "$TMP/sana"
  SRC="$TMP/sana/models/minimax_h3/Sol-H3-Spark"
  mkdir -p "$REPO_DIR"
  cp -r "$SRC"/. "$REPO_DIR"/
  rm -rf "$TMP"
  echo "    代码已就位: $REPO_DIR"
fi
cd "$REPO_DIR"

# ============ 1. 创建三个 Python 环境（未验证，按官方文档微调）============
# 官方未给出 Stage1/Stage2 的精确 conda/venv 命令；以下为骨架提示。
#   - Stage1: patched FastVideo + fastvideo-kernel 0.3.5 + cuDNN frontend 1.27.0 / CUDA 13
#   - Stage2: fixed LTX src (ltx-core/ltx-pipelines/ltx-kernels) + 编译 all2all_cpp
#   - Qwen  : docker build -f Dockerfile.qwen -t "$QWEN_IMAGE" .
# 请按 docs/setup.md 把对应 python 路径填入 STAGE1_PY / STAGE2_PY / QWEN_PY。
if [ ! -x "$STAGE1_PY" ] || [ ! -x "$STAGE2_PY" ]; then
  echo "[1] 三环境尚未就绪。请按官方 setup.md 创建："
  echo "    Stage1 -> $STAGE1_PY"
  echo "    Stage2 -> $STAGE2_PY"
  echo "    Qwen   -> $QWEN_PY (docker build -f Dockerfile.qwen -t $QWEN_IMAGE .)"
  echo "    创建后重跑本脚本。"
  exit 1
fi

# ============ 2. prepare.py：写 paths*.json + 打 FastVideo 补丁 ============
echo "[2] prepare.py --fetch-sources (生成 paths.json / fl2va / ref2va) ..."
python prepare.py --fetch-sources \
  --python-stage1 "$STAGE1_PY" \
  --python-stage2 "$STAGE2_PY" \
  --python-qwen "$QWEN_PY" \
  --prompt-cache "$PROMPT_CACHE" \
  --output paths.json
python prepare.py --fetch-sources --task fl2va \
  --python-stage1 "$STAGE1_PY" --python-stage2 "$STAGE2_PY" --python-qwen "$QWEN_PY" \
  --prompt-cache "$PROMPT_CACHE" --output paths-fl2va.json
python prepare.py --fetch-sources --task ref2va \
  --python-stage1 "$STAGE1_PY" --python-stage2 "$STAGE2_PY" --python-qwen "$QWEN_PY" \
  --prompt-cache "$PROMPT_CACHE" --output paths-ref2va.json

# ============ 3. cache_builder：通用上下文缓存 ============
echo "[3] runtime.cache_builder ..."
"$STAGE2_PY" -m pip install --no-deps \
  --target dependencies/offline-context -r requirements/offline-context.txt
PYTHONPATH="$PWD/dependencies/offline-context:$PWD" \
  "$STAGE2_PY" -m runtime.cache_builder --paths paths.json --output "$PROMPT_CACHE"

# ============ 4. download_checkpoints：权重（需 HF 授权）============
echo "[4] download_checkpoints.py (t2va/fl2va/ref2va) ..."
[ -n "$HF_TOKEN" ] && export HF_TOKEN   # huggingface_hub 会自动读取该环境变量
python download_checkpoints.py --plan
python -m pip install -r requirements/download.txt
for t in t2va fl2va ref2va; do
  python download_checkpoints.py --task "$t" --output-dir checkpoints --include-offline
done

# ============ 5. 启动 HTTP 服务（常驻）============
echo "[5] 启动 sol_h3_server.py ($SERVER_HOST:$SERVER_PORT) ..."
nohup "$SERVER_PY" "$REPO_DIR/sol_h3_server.py" \
  --repo "$REPO_DIR" --host "$SERVER_HOST" --port "$SERVER_PORT" \
  --python "$SERVER_PY" \
  > "$RUNTIME_ROOT/sol_h3_server.log" 2>&1 &
echo "    已后台启动，日志: $RUNTIME_ROOT/sol_h3_server.log"
sleep 2
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$SERVER_PORT/health" | grep -q 200; then
  echo "    自检通过: curl http://127.0.0.1:$SERVER_PORT/health -> 200"
else
  echo "    自检未通过，请查日志: $RUNTIME_ROOT/sol_h3_server.log"
fi
echo "完成。把 ai_movie_agent 的 config.yaml 里 engine.sol_h3.api 指向"
echo "  http://<本机内网IP>:$SERVER_PORT  并把 engine.backend 设为 sol_h3。"
