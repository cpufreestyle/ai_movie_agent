"""Resilient VAE download: outer retry loop rides out network flaps."""
import os
import sys
import time

sys.path.insert(0, r"D:\ai sheare\repo\ai_movie_agent")
import download_hf_mirror as dl  # noqa: E402

for attempt in range(1, 16):
    print(f"=== round {attempt} start {time.strftime('%H:%M:%S')} ===", flush=True)
    try:
        ok = dl.download("QuantStack/Wan2.2-TI2V-5B-GGUF",
                         "VAE/Wan2.2_VAE.safetensors",
                         r"D:\ComfyUI\models\vae\Wan2.2_VAE.safetensors", 4)
        if ok:
            print("=== SUCCESS ===", flush=True)
            break
    except Exception as e:
        print("unexpected:", type(e).__name__, str(e)[:120], flush=True)
    time.sleep(45)
else:
    print("=== ALL ROUNDS EXHAUSTED ===", flush=True)
