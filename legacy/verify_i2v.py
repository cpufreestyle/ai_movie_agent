#!/usr/bin/env python
# 轮询 8200 直到 wan22_i2v 任务完成，抽 3 帧并算平均饱和度(验证"发灰"是否消除)。
import requests, time, os, re, sys, subprocess
from PIL import Image

URL = "http://127.0.0.1:8200"
S = requests.Session(); S.trust_env = False
COMFY_OUT = "D:/ComfyUI/output"

# 从日志取 prompt_id
pid = None
try:
    log = open("outputs/i2v_run.log", encoding="utf-8", errors="ignore").read()
    m = re.search(r"prompt_id=([0-9a-f-]+)", log)
    if m:
        pid = m.group(1)
except Exception as e:
    print("[warn] 无法读 pid:", e)

print(f"[poll] pid={pid}")
done = None
for i in range(240):
    try:
        q = S.get(f"{URL}/queue", timeout=15).json()
        running = len(q.get("queue_running", []))
    except Exception:
        running = -1
    if pid:
        try:
            h = S.get(f"{URL}/history/{pid}", timeout=15).json()
            if pid in h and "10" in h[pid].get("outputs", {}):
                done = h[pid]["outputs"]["10"]["images"][0]
                break
        except Exception:
            pass
    if i % 6 == 0:
        print(f"[wait] {i*5}s running={running}")
    time.sleep(5)

if not done:
    print("[timeout] 未在规定时间内完成")
    sys.exit(0)

fname = done.get("filename")
sub = done.get("subfolder", "")
path = os.path.join(COMFY_OUT, sub, fname) if sub else os.path.join(COMFY_OUT, fname)
print(f"[done] 视频: {path}  ({os.path.getsize(path)/1024/1024:.1f} MB)")

# 抽帧 + 饱和度
try:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as e:
    print("[warn] 无 imageio_ffmpeg, 跳过抽帧:", e); sys.exit(0)

os.makedirs("outputs", exist_ok=True)
times = [0.2, 1.0, 2.0]
for idx, t in enumerate(times, 1):
    outp = f"outputs/i2v2_f{idx:02d}.png"
    cmd = [ff, "-y", "-ss", str(t), "-i", path, "-vframes", "1", outp]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    if os.path.exists(outp):
        im = Image.open(outp).convert("RGB").resize((256, 256))
        hsv = im.convert("HSV")
        sat = sum(hsv.getdata()[i][1] for i in range(len(hsv.getdata()))) / len(hsv.getdata())
        print(f"  {outp}: 平均饱和度 S={sat:.1f}/255  ({'正常' if sat>70 else '仍偏灰' if sat<50 else '中等'})")
    else:
        print(f"  {outp} 抽取失败")
print("[verify] 完成；用 open_result_view 看 outputs/i2v2_f*.png 或播放 webm")
