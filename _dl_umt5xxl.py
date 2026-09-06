"""Download umt5xxl text encoder GGUF from city96 (store as workflow-expected name)."""
import os
import sys
import time

import requests

if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

MIRROR = "https://hf-mirror.com"
REPO = "city96/umt5-xxl-encoder-gguf"
SRC = "umt5-xxl-encoder-Q5_K_M.gguf"
DST = r"D:\ComfyUI\models\text_encoders\umt5xxl-encoder-q5_k_m.gguf"
CHUNK = 8 * 1024 * 1024
ROUNDS = 20


def log(m):
    sys.stdout.write(m + "\n")
    sys.stdout.flush()


def main():
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    for round_n in range(1, ROUNDS + 1):
        url = f"{MIRROR}/{REPO}/resolve/main/{SRC}"
        try:
            h = requests.head(url, allow_redirects=True, timeout=30)
            if h.status_code != 200:
                log(f"[FAIL] HEAD -> {h.status_code}")
                time.sleep(45)
                continue
            total = int(h.headers.get("Content-Length", 0))
        except Exception as e:
            log(f"[ERR ] HEAD: {type(e).__name__}: {str(e)[:100]}")
            time.sleep(45)
            continue
        log(f"[SRC ] {SRC} ({total/1e9:.2f} GB)")
        offset = os.path.getsize(DST) if os.path.exists(DST) else 0
        if total and offset >= total:
            log("[SKIP] already complete")
            return True
        try:
            hdr = {"Range": f"bytes={offset}-"} if offset else {}
            r = requests.get(url, headers=hdr, stream=True, timeout=60,
                             allow_redirects=True)
            if r.status_code not in (200, 206):
                log(f"[FAIL] GET -> {r.status_code}")
                time.sleep(30)
                continue
            mode = "ab" if offset else "wb"
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
            log(f"[PART] ended at {written}/{total}")
        except Exception as e:
            log(f"[ERR ] {type(e).__name__}: {str(e)[:120]}")
        time.sleep(20)
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
