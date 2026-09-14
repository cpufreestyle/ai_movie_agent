#!/usr/bin/env python
"""ModelScope Range 续传下载（本机 HF 被封、modelscope.cn 直连可达）。

用法:
    MS_PROXY=none python download_ms.py <repo> <repo内路径> <dest> [threads]
例:
    MS_PROXY=none python download_ms.py Comfy-Org/MiniMax-H3 \
        model_patches/minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors \
        E:/ComfyUI_models/model_patches/minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors 6
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# 本机 HF 被封，但 modelscope.cn 直连可达；默认不挂代理，避免代理 TLS 问题。
_PROXY = os.environ.get("MS_PROXY", "none")
if _PROXY and _PROXY.lower() not in ("none", "off", "-"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = _PROXY

CH = 8 * 1024 * 1024
BASE = "https://modelscope.cn/models/{repo}/resolve/master/{fp}"


def log(m):
    sys.stdout.write(m + "\n")
    sys.stdout.flush()


def get_size(repo, fp):
    url = BASE.format(repo=repo, fp=fp)
    h = requests.get(url, headers={"Range": "bytes=0-0"},
                     allow_redirects=True, stream=True, timeout=60)
    h.raise_for_status()
    cr = h.headers.get("Content-Range")
    total = int(cr.rsplit("/", 1)[1]) if cr and "/" in cr else int(h.headers.get("Content-Length", 0) or 0)
    h.close()
    return total


def dl(url, path, off, n):
    # 每次自行 resolve+重定向（LFS 签名 URL 可能刷新），Range 跟随重定向保留
    hdr = {"Range": f"bytes={off}-{off + n - 1}"}
    r = requests.get(url, headers=hdr, stream=True, timeout=300, allow_redirects=True)
    r.raise_for_status()
    with open(path, "r+b") as f:
        f.seek(off)
        for ch in r.iter_content(CH):
            if ch:
                f.write(ch)


def download(repo, fp, dest, nth=6):
    total = get_size(repo, fp)
    if total == 0:
        log("[ERR] 无法获取文件大小")
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not os.path.exists(dest):
        open(dest, "wb").close()
    size = os.path.getsize(dest)
    aligned = (size // CH) * CH
    if aligned < size:
        os.truncate(dest, aligned)
        size = aligned
    log(f"[resume] {size/1e9:.2f}/{total/1e9:.2f} GB")
    if size >= total:
        log("[done] 已完整")
        return True
    url = BASE.format(repo=repo, fp=fp)
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
        futs = [ex.submit(dl, url, dest, o, n) for o, n in segs]
        for fu in as_completed(futs):
            fu.result()
    final = os.path.getsize(dest)
    el = time.time() - t0
    spd = (final - size) / 1e6 / el if el else 0
    ok = final >= total
    log(f"[final] {final/1e9:.2f}/{total/1e9:.2f} GB, {spd:.1f} MB/s -> "
        f"{'COMPLETE' if ok else 'INCOMPLETE'}")
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: download_ms.py <repo> <file_path> <dest> [threads]")
        raise SystemExit(2)
    repo, fp, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    nth = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    raise SystemExit(0 if download(repo, fp, dest, nth) else 1)
