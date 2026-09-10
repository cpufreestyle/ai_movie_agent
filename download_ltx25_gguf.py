#!/usr/bin/env python
# LTX-2.5 GGUF 权重下载/探测（16GB 显存专用路径）
#
# 背景：LTX-2.5 官方最小组合（int8-convrot transformer + Gemma4 int8 编码器）合计 34GB+，
# 16GB 显卡唯一可行路径是社区 GGUF：transformer 与文本编码器都用 GGUF 量化。
# 本脚本走 hf-mirror 镜像 + 本地代理，支持断点续传与断连重试。
#
# 用法：
#   python download_ltx25_gguf.py --list                 # 探测两个仓库有哪些 gguf 文件及大小
#   python download_ltx25_gguf.py --list <repo_id>       # 只探测指定仓库
#   python download_ltx25_gguf.py --get --quant Q3_K_M   # 下载 transformer(指定档) + 文本编码器
#   python download_ltx25_gguf.py --get --only transformer --quant Q2_K
import os, sys, time, argparse, requests

# 走本地代理（Clash mixed-port 7897）；已手工设置过代理时不覆盖
if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

MIRROR = "https://hf-mirror.com"

# models 目录统一走 comfy_paths（COMFYUI_MODELS_DIR / COMFYUI_ROOT 可覆盖，跨平台）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from comfy_paths import models_dir as _models_dir
    _MODELS = _models_dir()
except Exception:      # 单独拷走使用时退回字面兜底
    _MODELS = os.environ.get("COMFYUI_MODELS_DIR") or r"D:\ComfyUI\models"
DIFF = os.path.join(_MODELS, "diffusion_models")
TE = os.path.join(_MODELS, "text_encoders")

# realrebelai 的 GGUF 把 61-key transformer config 写进了 GGUF KV 字段，
# stock "Unet Loader (GGUF)" 可直接加载；自制/部分社区 GGUF 会缺 config 导致按 LTX-2.3 形状加载而 shape mismatch。
TRANSFORMER_REPO = "realrebelai/LTX-2.5_GGUFs"
TE_REPO = "elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF"
TE_FILE = "gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf"

CHUNK = 8 * 1024 * 1024
MAX_RETRY = 60


def log(m):
    sys.stdout.write(m + "\n")
    sys.stdout.flush()


def list_gguf(repo):
    """列出仓库里所有 .gguf 文件，并 HEAD 取大小与可访问性（401 即 gated）。"""
    try:
        r = requests.get(f"{MIRROR}/api/models/{repo}", timeout=30)
    except Exception as e:
        log(f"[ERR ] api {repo}: {type(e).__name__}: {str(e)[:100]}")
        return []
    if r.status_code != 200:
        log(f"[FAIL] api {repo} -> HTTP {r.status_code}")
        return []
    names = [s.get("rfilename") for s in r.json().get("siblings", [])
             if s.get("rfilename", "").lower().endswith(".gguf")]
    if not names:
        log("  (仓库内无 .gguf 文件)")
    ok = []
    for n in names:
        url = f"{MIRROR}/{repo}/resolve/main/{requests.utils.quote(n)}"
        try:
            h = requests.head(url, allow_redirects=True, timeout=30)
            size = int(h.headers.get("Content-Length", 0) or 0)
            flag = "OK " if h.status_code == 200 else "DENY"
            log(f"  [{flag} {h.status_code}] {n}  {size / 1e9:.2f} GB")
            if h.status_code == 200:
                ok.append((n, size))
        except Exception as e:
            log(f"  [ERR ] {n}: {type(e).__name__}: {str(e)[:80]}")
    return ok


def fetch(repo, src, dstdir, dstname, total=None):
    """单源下载，内部做 Range 续传 + 断连重试。"""
    os.makedirs(dstdir, exist_ok=True)
    url = f"{MIRROR}/{repo}/resolve/main/{requests.utils.quote(src)}"
    if total is None:
        try:
            h = requests.head(url, allow_redirects=True, timeout=30)
        except Exception as e:
            log(f"[FAIL] HEAD {repo}/{src}: {type(e).__name__}: {str(e)[:80]}")
            return False
        if h.status_code != 200:
            log(f"[FAIL] HEAD {repo}/{src} -> HTTP {h.status_code}")
            return False
        total = int(h.headers.get("Content-Length", 0) or 0)
        url = h.url
    log(f"[SRC ] {repo}/{src}  ({total / 1e9:.2f} GB)")
    dst = os.path.join(dstdir, dstname)
    for attempt in range(1, MAX_RETRY + 1):
        offset = os.path.getsize(dst) if os.path.exists(dst) else 0
        if total and offset >= total:
            log(f"[SKIP] {dstname} 已完整 ({offset} bytes)")
            return True
        mode = "ab" if offset else "wb"
        hdr = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            log(f"[GET ] {dstname} attempt {attempt}: {offset}/{total} bytes")
            r = requests.get(url, headers=hdr, stream=True, timeout=60, allow_redirects=True)
            if r.status_code not in (200, 206):
                log(f"[FAIL] GET -> {r.status_code}")
                return False
            if r.status_code == 200 and offset:
                mode, offset = "wb", 0
            written = offset
            t0 = time.time()
            with open(dst, mode) as f:
                for ch in r.iter_content(CHUNK):
                    if not ch:
                        continue
                    f.write(ch)
                    written += len(ch)
                    if written % (512 * 1024 * 1024) < CHUNK:
                        el = time.time() - t0
                        spd = written / 1e6 / el if el else 0
                        pct = written / total * 100 if total else 0
                        log(f"  {dstname}: {written / 1e9:.2f}/{total / 1e9:.2f} GB ({pct:.1f}%) {spd:.1f} MB/s")
            if written >= total:
                log(f"[DONE] {dstname}: {written} bytes -> {dst}")
                return True
            log(f"[PART] {dstname}: {written}/{total}, retry")
        except Exception as e:
            log(f"[ERR ] {dstname} attempt {attempt}: {type(e).__name__}: {str(e)[:110]}")
        time.sleep(5)
    log(f"[GAVEUP] {dstname}")
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", metavar="REPO", nargs="?", const="ALL")
    ap.add_argument("--quant", default="Q3_K_M", help="transformer GGUF 量化档: Q2_K / Q3_K_M / Q4_K_S / Q4_K_M")
    ap.add_argument("--get", action="store_true", help="下载 transformer 与文本编码器")
    ap.add_argument("--only", choices=["transformer", "te"])
    a = ap.parse_args()

    if a.list:
        repos = [TRANSFORMER_REPO, TE_REPO] if a.list == "ALL" else [a.list]
        for rp in repos:
            log(f"=== {rp} ===")
            list_gguf(rp)
        sys.exit(0)

    if a.get:
        if a.only in (None, "transformer"):
            tname = f"LTX-2.5-Distilled-{a.quant}.gguf"
            ok = fetch(TRANSFORMER_REPO, tname, DIFF, tname)
            log(f"RESULT transformer {a.quant}: {'OK' if ok else 'FAILED'}")
        if a.only in (None, "te"):
            ok = fetch(TE_REPO, TE_FILE, TE, TE_FILE)
            log(f"RESULT text encoder: {'OK' if ok else 'FAILED'}")
        log("=== 下载任务结束 ===")
