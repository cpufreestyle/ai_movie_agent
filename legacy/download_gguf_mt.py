#!/usr/bin/env python
# 多线程 Range 续传 LTX-2.5 GGUF（加速 16GB 显存路径的权重下载）。
# 用法: python download_gguf_mt.py
# 从已有文件大小续传 [start, TOTAL]，按 chunk 对齐截断可能不完整的尾块，
# 再用 NTH 个线程分片并行下载剩余部分，各自 seek 写对应偏移。
import os
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

MIRROR = "https://hf-mirror.com"
REPO = "realrebelai/LTX-2.5_GGUFs"
QUANT = "Q2_K"
FILE = f"LTX-2.5-Distilled-{QUANT}.gguf"
URL = f"{MIRROR}/{REPO}/resolve/main/{requests.utils.quote(FILE)}"
PATH = r"D:\ComfyUI\models\diffusion_models\{FILE}".format(FILE=FILE)
# 动态获取真实文件大小（不同量化档大小不同）
_h = requests.head(URL, allow_redirects=True, timeout=30)
TOTAL = int(_h.headers.get("Content-Length", 0) or 0)
URL = _h.url
if TOTAL == 0:
    log("[ERR ] 无法获取文件大小，退出")
    sys.exit(1)
NTH = 6
CH = 8 * 1024 * 1024  # 与原脚本一致，便于对齐截断尾块


def log(m):
    sys.stdout.write(m + "\n")
    sys.stdout.flush()


def dl(off, n):
    hdr = {"Range": f"bytes={off}-{off + n - 1}"}
    r = requests.get(URL, headers=hdr, stream=True, timeout=120, allow_redirects=True)
    r.raise_for_status()
    with open(PATH, "r+b") as f:
        f.seek(off)
        got = 0
        for ch in r.iter_content(CH):
            if not ch:
                continue
            f.write(ch)
            got += len(ch)
    return off, got


def dl_retry(off, n, retries=6):
    last = None
    for i in range(retries):
        try:
            return dl(off, n)
        except Exception as e:
            last = e
            log(f"  [seg {off/1e9:.2f}GB] {type(e).__name__}: {str(e)[:90]} -> retry {i+1}")
            time.sleep(3)
    raise RuntimeError(f"seg {off} failed: {last}")


if __name__ == "__main__":
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    if not os.path.exists(PATH):
        open(PATH, "wb").close()
    size = os.path.getsize(PATH)
    # 对齐到 chunk 边界，截断可能不完整的尾块（最多重下 8MB）
    aligned = (size // CH) * CH
    if aligned < size:
        os.truncate(PATH, aligned)
        log(f"[trunc] {size/1e9:.2f} -> {aligned/1e9:.2f} GB (丢弃不完整尾块)")
        size = aligned
    log(f"[resume] from {size/1e9:.2f} GB / {TOTAL/1e9:.2f} GB")
    if size >= TOTAL:
        log("[done] 文件已完整")
        sys.exit(0)

    remain = TOTAL - size
    seg = (remain + NTH - 1) // NTH
    segs = []
    o = size
    while o < TOTAL:
        n = min(seg, TOTAL - o)
        segs.append((o, n))
        o += n
    log(f"[spawn] {len(segs)} threads for 剩余 {remain/1e9:.2f} GB")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=NTH) as ex:
        futs = [ex.submit(dl_retry, o, n) for o, n in segs]
        for fu in as_completed(futs):
            off, got = fu.result()
            log(f"  [seg {off/1e9:.2f}GB] done {got/1e6:.1f} MB")
    final = os.path.getsize(PATH)
    el = time.time() - t0
    spd = (final - size) / 1e6 / el if el else 0
    log(f"[final] {final/1e9:.2f} GB / {TOTAL/1e9:.2f} GB, 多线程均速 {spd:.1f} MB/s -> "
        f"{'COMPLETE' if final >= TOTAL else 'INCOMPLETE'}")
