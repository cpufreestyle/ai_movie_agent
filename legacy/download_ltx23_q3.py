"""Download LTX-2.3 22B distilled-1.1 Q3_K_M GGUF (dual-source, resume, retry)."""
import os
import sys
import time

import requests

if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

MIRROR = "https://hf-mirror.com"
DIFF = r"D:\ComfyUI\models\diffusion_models"
DST = os.path.join(DIFF, "ltx-2.3-22b-distilled-1.1-Q3_K_M.gguf")
CHUNK = 8 * 1024 * 1024
MAX_RETRY = 60

SOURCES = [
    ("unsloth/LTX-2.3-GGUF", "distilled-1.1/ltx-2.3-22b-distilled-1.1-Q3_K_M.gguf"),
    ("QuantStack/LTX-2.3-GGUF", "LTX-2.3-distilled-1.1/LTX-2.3-22B-distilled-1.1-Q3_K_M.gguf"),
]


def log(m):
    sys.stdout.write(m + "\n")
    sys.stdout.flush()


def _fetch(url, total):
    for attempt in range(1, MAX_RETRY + 1):
        offset = os.path.getsize(DST) if os.path.exists(DST) else 0
        if total and offset >= total:
            log(f"[SKIP] already complete ({offset})")
            return True
        mode = "ab" if offset else "wb"
        hdr = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            log(f"[GET ] attempt {attempt}: {offset}/{total}")
            r = requests.get(url, headers=hdr, stream=True, timeout=60,
                             allow_redirects=True)
            if r.status_code not in (200, 206):
                log(f"[FAIL] GET -> {r.status_code}")
                return False
            if r.status_code == 200 and offset:
                mode = "wb"
            written = offset
            with open(DST, mode) as f:
                for chunk in r.iter_content(CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
            if written >= total:
                log(f"[DONE] {written} bytes -> {DST}")
                return True
            log(f"[PART] ended at {written}/{total}, retry")
        except Exception as e:
            log(f"[ERR ] attempt {attempt}: {type(e).__name__}: {str(e)[:120]}")
        time.sleep(5)
    return False


def main():
    os.makedirs(DIFF, exist_ok=True)
    for repo, src in SOURCES:
        url = f"{MIRROR}/{repo}/resolve/main/{requests.utils.quote(src)}"
        try:
            h = requests.head(url, allow_redirects=True, timeout=30)
        except Exception as e:
            log(f"[FAIL] HEAD {repo}: {type(e).__name__}")
            continue
        if h.status_code != 200:
            log(f"[FAIL] HEAD -> {h.status_code}")
            continue
        total = int(h.headers.get("Content-Length", 0))
        log(f"[SRC ] {repo} ({total/1e9:.2f} GB)")
        if _fetch(h.url, total):
            print("DOWNLOAD_OK")
            return
    print("DOWNLOAD_FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
