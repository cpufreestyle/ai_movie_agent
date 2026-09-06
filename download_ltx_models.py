#!/usr/bin/env python
# 后台下载 LTX-2.5 必需权重（无需 HF token，走 hf-mirror.com 镜像）
# 支持断点续传 + 连接中断自动重试
import os, sys, time, requests

# 走本地代理（Clash mixed-port 7897）；已手工设置过代理时不覆盖
if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

MIRROR = "https://hf-mirror.com"
DIFF = r"D:\ComfyUI\models\diffusion_models"
TE = r"D:\ComfyUI\models\text_encoders"
VAE = r"D:\ComfyUI\models\vae"
os.makedirs(DIFF, exist_ok=True)
os.makedirs(TE, exist_ok=True)
os.makedirs(VAE, exist_ok=True)

# ([(repo_id, src_path), ...备源], dst_dir, dst_filename)
JOBS = [
    ([("BennyDaBall/LTX-2.5-22b-distilled-nvfp4-comfy",
       "ltx-2.5-22b-distilled-transformer-nvfp4-comfy.safetensors")],
     DIFF, "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors"),
    ([("DeepNeuralNerd/Gemma-4-12B-it-uncensored-heretic-DeepNeuralNerd-LTX_2.5_ComfyUI",
       "Gemma-4-12B-it-uncensored-heretic - DeepNeuralNerd -LTX 2.5-ComfyUI-int8convrot.safetensors")],
     TE, "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"),
    # LTX-2.5 的音视频 VAE：官方 Lightricks/LTX-2.5 是 gated(401)，改用镜像仓库。
    # 缺 VAE 时 LTXVEmptyLatentAudio 会报
    #   'PixelspaceConversionVAE' object has no attribute 'latent_frequency_bins'
    ([("lxxxy6/LTX-2.5", "ltx-2.5-video-vae-bf16.safetensors"),
      ("yuvraj108c/LTX-2.5", "ltx-2.5-video-vae-bf16.safetensors"),
      ("comfyicu/LTX-2.5", "vae/ltx-2.5-video-vae-bf16.safetensors")],
     VAE, "ltx-2.5-video-vae-bf16.safetensors"),
    ([("lxxxy6/LTX-2.5", "ltx-2.5-audio-vae-bf16.safetensors"),
      ("yuvraj108c/LTX-2.5", "ltx-2.5-audio-vae-bf16.safetensors"),
      ("comfyicu/LTX-2.5", "vae/ltx-2.5-audio-vae-bf16.safetensors")],
     VAE, "ltx-2.5-audio-vae-bf16.safetensors"),
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
    log("=== LTX-2.5 权重下载（续传+重试+多源） ===")
    for sources, d, name in JOBS:
        try:
            ok = download(sources, d, name)
            log(f"RESULT {name}: {'OK' if ok else 'FAILED'}")
        except Exception as e:
            log(f"[ERROR] {name}: {type(e).__name__}: {e}")
    log("=== 全部任务结束 ===")
