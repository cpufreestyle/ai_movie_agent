#!/usr/bin/env python
# 后台下载 MiniMax H3（comfyui_mmH3 引擎）必需权重（无需 HF token，走 hf-mirror.com 镜像）
# 支持断点续传 + 连接中断自动重试；与 download_ltx_models.py 同模式。
#
# 对应 config.yaml 的 engine.comfyui_mmH3：
#   unet        -> diffusion_models/
#   text_encoder -> text_encoders/
#   video_vae   -> vae/
#   audio_vae   -> vae/
#   lora        -> loras/
#
# 用法（在「有 NVIDIA 显卡、已装好 ComfyUI」的机器上跑）：
#   python download_mmh3_models.py --models-dir D:\ComfyUI\models
#   # 或指定环境变量：  set COMFYUI_MODELS_DIR=D:\ComfyUI\models
#   # 权重会落到 <models-dir>/{diffusion_models,text_encoders,vae,loras}
import os, sys, time, argparse, requests

# 走本地代理（Clash mixed-port 7897）；已手工设置过代理时不覆盖
if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

ap = argparse.ArgumentParser(description="下载 MiniMax H3 权重（免 token / 续传 / 重试）")
ap.add_argument("--models-dir", default=os.environ.get("COMFYUI_MODELS_DIR") or "ComfyUI/models",
                help="ComfyUI 的 models 根目录（默认 COMFYUI_MODELS_DIR 或 ./ComfyUI/models）")
args = ap.parse_args()

MIRROR = "https://hf-mirror.com"
MODELS_ROOT = os.path.abspath(args.models_dir)
DIFF = os.path.join(MODELS_ROOT, "diffusion_models")
TE = os.path.join(MODELS_ROOT, "text_encoders")
VAE = os.path.join(MODELS_ROOT, "vae")
LORA = os.path.join(MODELS_ROOT, "loras")
for d in (DIFF, TE, VAE, LORA):
    os.makedirs(d, exist_ok=True)

# ([(repo_id, src_path), ...备源], dst_dir, dst_filename)
# 首源 Merserk/MiniMax-H3-INT4-ConvRot 与 config 的 int4_convrot 命名一致；
# 其余社区/官方重打包仓作备源，脚本会 HEAD 自检、自动跳过不存在的源。
JOBS = [
    ([("Merserk/MiniMax-H3-INT4-ConvRot", "minimax_h3_fl2va_pruned_int4_convrot.safetensors"),
      ("Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot", "minimax_h3_fl2va_pruned_int4_convrot.safetensors"),
      ("Comfy-Org/MiniMax-H3", "minimax_h3_fl2va_pruned_int4_convrot.safetensors")],
     DIFF, "minimax_h3_fl2va_pruned_int4_convrot.safetensors"),

    ([("Merserk/MiniMax-H3-INT4-ConvRot", "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"),
      ("Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot", "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"),
      ("Comfy-Org/MiniMax-H3", "qwen3vl_32b_minimax_h3_int4_convrot.safetensors")],
     TE, "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"),

    ([("Merserk/MiniMax-H3-INT4-ConvRot", "minimax_h3_video_vae_fp16.safetensors"),
      ("Comfy-Org/MiniMax-H3", "minimax_h3_video_vae_fp16.safetensors")],
     VAE, "minimax_h3_video_vae_fp16.safetensors"),

    ([("Merserk/MiniMax-H3-INT4-ConvRot", "minimax_h3_audio_vae_fp32.safetensors"),
      ("Comfy-Org/MiniMax-H3", "minimax_h3_audio_vae_fp32.safetensors")],
     VAE, "minimax_h3_audio_vae_fp32.safetensors"),

    ([("Merserk/MiniMax-H3-INT4-ConvRot", "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"),
      ("Comfy-Org/MiniMax-H3", "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors")],
     LORA, "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"),
]

CHUNK = 8 * 1024 * 1024
MAX_RETRY = 60

def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

def download(sources, dstdir, dstname):
    """依次尝试各镜像源；单源内部做 Range 续传 + 断连重试。"""
    for repo, src in sources:
        resolve = f"{MIRROR}/{repo}/resolve/main/{requests.utils.quote(src)}"
        try:
            h = requests.head(resolve, allow_redirects=True, timeout=30)
        except Exception as e:
            log(f"[FAIL] HEAD {repo}/{src}: {type(e).__name__}: {str(e)[:80]}")
            continue
        if h.status_code != 200:
            log(f"[FAIL] HEAD {repo}/{src} -> {h.status_code}")
            continue
        total = int(h.headers.get("Content-Length", 0))
        log(f"[SRC ] {repo}/{src}  ({total/1e6:.1f} MB)")
        if _fetch(h.url, dstdir, dstname, total):
            return True
        log(f"[NEXT] {dstname}: 该源未完成，切换下一个源")
    log(f"[GAVEUP] {dstname}: 所有源均失败")
    return False

def _fetch(final_url, dstdir, dstname, total):
    dst = os.path.join(dstdir, dstname)
    for attempt in range(1, MAX_RETRY + 1):
        offset = os.path.getsize(dst) if os.path.exists(dst) else 0
        if offset >= total and total:
            log(f"[SKIP] {dstname} already complete ({offset} bytes)")
            return True
        mode = "ab" if offset else "wb"
        hdr = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            log(f"[GET ] {dstname} attempt {attempt}: {offset}/{total} bytes")
            r = requests.get(final_url, headers=hdr, stream=True, timeout=60, allow_redirects=True)
            if r.status_code not in (200, 206):
                log(f"[FAIL] GET -> {r.status_code}")
                return False
            if r.status_code == 200 and offset:
                mode = "wb"
            written = offset
            t0 = time.time()
            with open(dst, mode) as f:
                for chunk in r.iter_content(CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
                    if written % (256 * 1024 * 1024) < CHUNK:
                        el = time.time() - t0
                        spd = written / 1e6 / el if el else 0
                        pct = written / total * 100 if total else 0
                        log(f"  {dstname}: {written/1e9:.2f}/{total/1e9:.2f} GB ({pct:.1f}%) {spd:.1f} MB/s")
            if written >= total:
                log(f"[DONE] {dstname}: {written} bytes -> {dst}")
                return True
            log(f"[PART] {dstname}: connection ended at {written}/{total}, will retry")
        except Exception as e:
            log(f"[ERR ] {dstname} attempt {attempt}: {type(e).__name__}: {str(e)[:120]}")
        time.sleep(5)
    log(f"[GAVEUP] {dstname}: 该源重试 {MAX_RETRY} 次仍未完成")
    return False

if __name__ == "__main__":
    log(f"=== MiniMax H3 权重下载（续传+重试+多源） ===")
    log(f"models 根目录: {MODELS_ROOT}")
    for sources, d, name in JOBS:
        try:
            ok = download(sources, d, name)
            log(f"RESULT {name}: {'OK' if ok else 'FAILED'}")
        except Exception as e:
            log(f"[ERROR] {name}: {type(e).__name__}: {e}")
    log("=== 全部任务结束 ===")
