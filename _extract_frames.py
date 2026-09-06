#!/usr/bin/env python
"""从成片里每镜抽 1 帧（按时间均匀），供视觉核对实际画面内容。"""
import os, subprocess, imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
ROOT = os.path.dirname(os.path.abspath(__file__))

VIDEOS = [
    ("outputs/ltx23_film.mp4", "outputs/_frames/ep2", 18),
    ("outputs/ltx25_film.mp4", "outputs/_frames/ep3", 18),
]

def duration(path):
    r = subprocess.run([FF, "-i", path], capture_output=True, text=True, timeout=60)
    for line in (r.stderr + r.stdout).splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h)*3600 + int(m)*60 + float(s)
    return 0.0

for vpath, outdir, n in VIDEOS:
    os.makedirs(os.path.join(ROOT, outdir), exist_ok=True)
    d = duration(os.path.join(ROOT, vpath))
    print(f"== {vpath}  duration={d:.2f}s  frames={n}")
    for k in range(n):
        t = (k + 0.5) * d / n
        out = os.path.join(ROOT, outdir, f"shot_{k+1:02d}.png")
        cmd = [FF, "-y", "-ss", f"{t:.3f}", "-i", os.path.join(ROOT, vpath),
               "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "3", out]
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(f"  shot {k+1:02d} t={t:.2f}s -> {out}")
print("DONE")
