"""Outer retry wrapper: keep retrying the Q3_K_M download across network flaps."""
import sys
import time

sys.path.insert(0, r"D:\ai sheare\repo\ai_movie_agent")
import download_ltx23_q3 as dl  # noqa: E402

for attempt in range(1, 21):
    print(f"=== round {attempt} start {time.strftime('%H:%M:%S')} ===", flush=True)
    try:
        dl.main()
        print("=== SUCCESS ===", flush=True)
        break
    except SystemExit:
        print("round failed, sleeping 60s...", flush=True)
        time.sleep(60)
    except Exception as e:
        print("unexpected:", type(e).__name__, str(e)[:120], flush=True)
        time.sleep(60)
else:
    print("=== ALL ROUNDS EXHAUSTED ===", flush=True)
