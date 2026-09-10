#!/usr/bin/env python
"""批量：ep1 全 18 镜白模驱动出片（Blender blockout -> H3 Hybrid）。

流程：① 按 ep1_blocking_map.json 用 bpy 渲染白模参考视频；
      ② 用对应锚定图 + 白模跑 run_blocking（Hybrid）；
      ③ 拼接成片 outputs/blocking/ep1_blocking_film.mp4。

用法：
  python run_blocking_batch.py --ep 1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import run_series as rs

ROOT = rs.ROOT
PY = sys.executable
BASE_SEED = 20260907


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--res", default="1024x576")
    ap.add_argument("--start", type=int, default=1,
                    help="从第几镜开始（断点续跑，例如崩了之后 --start 6）")
    a = ap.parse_args()

    shots = rs.load_json(rs.SHOTS_FILE)["ep%d" % a.ep]
    anchor_map = rs.load_json(os.path.join(ROOT, "outputs/anchor/ep%d_anchor_map.json" % a.ep))
    blocking = rs.load_json(os.path.join(ROOT, "outputs/blocking/ep%d_blocking_map.json" % a.ep))

    out_dir = os.path.join(ROOT, "outputs/blocking/ep%d_hybrid" % a.ep)
    os.makedirs(out_dir, exist_ok=True)
    parts = []
    w, h = (int(x) for x in a.res.lower().split("x"))

    eng = rs.build_engine(w, h, a.frames, a.fps, "mmh3")
    if not eng.is_ready():
        raise SystemExit("ComfyUI 未就绪")

    for idx, p in enumerate(shots, 1):
        if idx < a.start:
            continue
        b = blocking.get(str(idx)) or {}
        cam = b.get("cam", "static")
        walk = b.get("walk", "0,0:0,0")
        blk = os.path.join(out_dir, "%02d_block.mp4" % idx)
        # ① 白模参考视频
        if not os.path.exists(blk):
            print(f"[white] ep{a.ep} shot{idx} cam={cam} walk={walk}", flush=True)
            subprocess.run([PY, "gen_blocking.py", "--out", blk,
                            "--frames", str(a.frames), "--fps", str(a.fps),
                            "--res", a.res, "--cam", cam, f"--walk={walk}"], check=True)
        # ② 锚定图
        img = anchor_map.get(str(idx)) or anchor_map.get(idx) or ""
        img_path = img if os.path.isabs(img) else os.path.join(ROOT, img)
        cmd = [PY, "run_blocking.py",
               "--prompt", p,
               "--ref", blk,
               "--out", os.path.join(out_dir, "%02d.mp4" % idx),
               "--seed", str(BASE_SEED + a.ep * 1000 + idx)]
        if img and os.path.exists(img_path):
            cmd += ["--image", img_path]
        print(f"[hybrid] ep{a.ep} shot{idx} img={'Y' if img else 'N'}", flush=True)
        subprocess.run(cmd, check=True)
        parts.append(os.path.join(out_dir, "%02d.mp4" % idx))

    # ③ 拼接（用目录下全部 ??.mp4，支持 --start 断点后续跑补全成片）
    import glob as _gg
    parts = sorted(_gg.glob(os.path.join(out_dir, "??.mp4")))
    film = os.path.join(ROOT, "outputs/blocking/ep%d_blocking_film.mp4" % a.ep)
    with open(os.path.join(out_dir, "_list.txt"), "w") as f:
        for pp in parts:
            f.write("file '%s'\n" % os.path.abspath(pp))
    subprocess.run([rs.FFMPEG, "-y", "-f", "concat", "-safe", "0",
                    "-i", os.path.join(out_dir, "_list.txt"),
                    "-c", "copy", film], check=True)
    print("FILM ->", film, flush=True)


if __name__ == "__main__":
    main()
