"""Resilient downloader for all 3 missing Wan2.2 files (hf-mirror, resume, 20 rounds)."""
import sys
import time

sys.path.insert(0, r"D:\ai sheare\repo\ai_movie_agent")
import download_hf_mirror as dl  # noqa: E402

FILES = [
    ("QuantStack/Wan2.2-TI2V-5B-GGUF", "VAE/Wan2.2_VAE.safetensors",
     r"D:\ComfyUI\models\vae\Wan2.2_VAE.safetensors"),
    ("QuantStack/Wan2.2-TI2V-5B-GGUF", "umt5xxl-encoder-q5_k_m.gguf",
     r"D:\ComfyUI\models\text_encoders\umt5xxl-encoder-q5_k_m.gguf"),
]

all_ok = False
for round_n in range(1, 21):
    print(f"=== round {round_n} start {time.strftime('%H:%M:%S')} ===", flush=True)
    pending = [(r, f, p) for r, f, p in FILES
               if not (__import__("os").path.exists(p)
                       and __import__("os").path.getsize(p) > 1_000_000)]
    if not pending:
        print("=== ALL FILES PRESENT ===", flush=True)
        all_ok = True
        break
    failed = False
    for repo, fname, dest in pending:
        try:
            ok = dl.download(repo, fname, dest, 4)
            if not ok:
                failed = True
        except Exception as e:
            print(f"[EXC] {fname}: {type(e).__name__}: {str(e)[:120]}", flush=True)
            failed = True
    if not failed:
        print("=== ALL FILES DOWNLOADED ===", flush=True)
        all_ok = True
        break
    time.sleep(60)

print("=== FINAL:", "ALL_OK" if all_ok else "INCOMPLETE", "===",
      flush=True)
sys.exit(0 if all_ok else 1)
