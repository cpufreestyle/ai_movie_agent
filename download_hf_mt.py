#!/usr/bin/env python
"""通用多线程 Range 续传下载（HF / hf-mirror）。

用法:
    python download_hf_mt.py <repo> <filename> <dest_path> [threads]
    python download_hf_mt.py --mirror https://hf-mirror.com <repo> <filename> <dest>
    HF_MIRROR=https://hf-mirror.com python download_hf_mt.py <repo> <filename> <dest>
    python download_hf_mt.py --models-dir D:/ComfyUI/models      # 批量下内置清单

例:
    python download_hf_mt.py QuantStack/Wan2.2-TI2V-5B-GGUF Wan2.2-TI2V-5B-Q8_0.gguf D:/ComfyUI/models/diffusion_models/Wan2.2-TI2V-5B-Q8_0.gguf

注：download_hf_mirror.py 是本脚本的 hf-mirror 预设封装（保留旧文件名以兼容既有 import）。
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# 代理：默认走本机 127.0.0.1:7897（环境里可能预置了其它不可用代理，会导致 HF 下载在
# TLS 握手阶段 UNEXPECTED_EOF）。用 HF_PROXY 覆盖；设为 none/off/- 则不使用代理。
_PROXY = os.environ.get("HF_PROXY", "http://127.0.0.1:7897")
if _PROXY and _PROXY.lower() not in ("none", "off", "-"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = _PROXY

# 下载源：--mirror > HF_MIRROR > 官方站
MIRROR = (os.environ.get("HF_MIRROR") or "https://huggingface.co").rstrip("/")
CH = 8 * 1024 * 1024

# gated 模型下载需要 HF token（从 HF_TOKEN 环境变量读取）。
# 仅从环境变量读取，不写盘、不进日志；不存在时为空（公开模型照常下载）。
# 注意：HF 上并不存在 Wan2.2-T2V-5B GGUF（5B 只发 FP16）；token 主要用于其他 gated 仓库。
_HF_TOKEN = os.environ.get("HF_TOKEN")
AUTH = {"Authorization": f"Bearer {_HF_TOKEN}"} if _HF_TOKEN else {}


def log(m):
    sys.stdout.write(m + "\n")
    sys.stdout.flush()


def get_url_and_size(repo, fname):
    url = f"{MIRROR}/{repo}/resolve/main/{requests.utils.quote(fname)}"
    if _HF_TOKEN:
        url += f"?token={_HF_TOKEN}"   # gated 模型重定向到 CDN 时会丢掉 Authorization 头，必须走 ?token= 查询参数
    # 用 GET(Range) 而非 HEAD：本机代理对 HF 的 HEAD 常 TLS 中断，GET 更稳
    hdr = dict(AUTH)
    hdr["Range"] = "bytes=0-0"
    h = requests.get(url, headers=hdr, allow_redirects=True, stream=True, timeout=60)
    h.raise_for_status()
    total = 0
    cr = h.headers.get("Content-Range")
    if cr and "/" in cr:
        total = int(cr.rsplit("/", 1)[1])
    else:
        total = int(h.headers.get("Content-Length", 0) or 0)
    h.close()
    return h.url, total


def dl(url, path, off, n):
    hdr = {"Range": f"bytes={off}-{off + n - 1}"}
    if AUTH:
        hdr.update(AUTH)
    r = requests.get(url, headers=hdr, stream=True, timeout=180, allow_redirects=True)
    r.raise_for_status()
    with open(path, "r+b") as f:
        f.seek(off)
        for ch in r.iter_content(CH):
            if ch:
                f.write(ch)
    return n


def dl_retry(url, path, off, n, retries=8):
    last = None
    for i in range(retries):
        try:
            return dl(url, path, off, n)
        except Exception as e:
            last = e
            log(f"  [seg {off/1e9:.2f}GB] {type(e).__name__}: {str(e)[:90]} -> retry {i+1}")
            time.sleep(3)
    raise RuntimeError(f"seg {off} failed: {last}")


def download(repo, fname, path, nth=4):
    url, total = get_url_and_size(repo, fname)
    if total == 0:
        log(f"[ERR ] 无法获取文件大小: {fname}")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        open(path, "wb").close()
    size = os.path.getsize(path)
    aligned = (size // CH) * CH
    if aligned < size:
        os.truncate(path, aligned)
        log(f"[trunc] {size/1e9:.2f} -> {aligned/1e9:.2f} GB (丢弃不完整尾块)")
        size = aligned
    log(f"[{fname}] resume {size/1e9:.2f} / {total/1e9:.2f} GB")
    if size >= total:
        log(f"[done] {fname} 已完整")
        return True

    remain = total - size
    seg = (remain + nth - 1) // nth
    segs, o = [], size
    while o < total:
        n = min(seg, total - o)
        segs.append((o, n))
        o += n
    log(f"[spawn] {len(segs)} threads, 剩余 {remain/1e9:.2f} GB")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=nth) as ex:
        futs = [ex.submit(dl_retry, url, path, o, n) for o, n in segs]
        for fu in as_completed(futs):
            log(f"  [seg] done {fu.result()/1e6:.1f} MB")
    final = os.path.getsize(path)
    el = time.time() - t0
    spd = (final - size) / 1e6 / el if el else 0
    ok = final >= total
    log(f"[final] {fname}: {final/1e9:.2f}/{total/1e9:.2f} GB, {spd:.1f} MB/s -> "
        f"{'COMPLETE' if ok else 'INCOMPLETE'}")
    return ok


# Wan2.2-TI2V-5B (16GB 显存友好) 所需资产：(repo, 仓库内文件名, models 下的相对路径)
TASKS = [
    ("QuantStack/Wan2.2-TI2V-5B-GGUF", "Wan2.2-TI2V-5B-Q8_0.gguf",
     "diffusion_models/Wan2.2-TI2V-5B-Q8_0.gguf"),
    ("QuantStack/Wan2.2-TI2V-5B-GGUF", "VAE/Wan2.2_VAE.safetensors",
     "vae/Wan2.2_VAE.safetensors"),
]
DEFAULT_MODELS_DIR = os.environ.get("COMFYUI_MODELS_DIR") or "D:/ComfyUI/models"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="多线程 Range 续传下载（HF / hf-mirror）")
    ap.add_argument("repo", nargs="?", help="仓库，如 QuantStack/Wan2.2-TI2V-5B-GGUF")
    ap.add_argument("filename", nargs="?", help="仓库内文件名")
    ap.add_argument("dest", nargs="?", help="本地保存路径")
    ap.add_argument("threads", nargs="?", type=int, default=4, help="并发线程数，默认 4")
    ap.add_argument("--mirror", default="", help="下载源，默认 https://huggingface.co")
    ap.add_argument("--models-dir", default=DEFAULT_MODELS_DIR,
                    help=f"批量模式的 ComfyUI/models 目录（默认 {DEFAULT_MODELS_DIR}）")
    a = ap.parse_args(argv)

    global MIRROR
    if a.mirror:
        MIRROR = a.mirror.rstrip("/")

    # 给了 repo/filename/dest 就下单个文件
    if a.repo and a.filename and a.dest:
        return 0 if download(a.repo, a.filename, a.dest, a.threads) else 1

    # 否则批量下内置清单
    log(f"[mirror] {MIRROR}\n[models] {a.models_dir}")
    for repo, fname, rel in TASKS:
        dest = os.path.join(a.models_dir, rel)
        done = False
        for attempt in range(12):
            try:
                if download(repo, fname, dest, a.threads):
                    done = True
                    break
            except Exception as e:
                log(f"[RETRY] {fname} attempt {attempt+1}: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(5)
        if not done:
            log(f"[FAIL] {fname} 多次重试仍失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
