#!/usr/bin/env python
"""H3 真人低分片 -> LTX-2.5 超分（ESRGAN 4x-UltraSharp）视频管线。

阶段：
  1) ComfyUI：VHS_LoadVideo(低分 H3 片) -> ImageUpscaleWithModel(4x-UltraSharp) -> ImageSharpen -> VHS_VideoCombine(静音)
  2) ffmpeg：把原片对应片段的原生立体声音轨混回超分视频

OOM 提示：整集 700+ 帧一次性 4x 会爆 16GB；用 --start/--limit 分段处理短片段。

用法：
  python upscale_video.py <src.mp4> <dst.mp4> [--model 4x-UltraSharp.pth] [--sharpen 0.2]
                              [--start 690] [--limit 40] [--crf 17] [--fps 24]
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.comfyui_client import ComfyUIClient
import imageio_ffmpeg

COMFY_INPUT = r"D:\ComfyUI\input"
COMFY_API = "http://127.0.0.1:8188"


def build_workflow(video_name, model, sharpen, fps, limit, start, crf):
    wf = {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": video_name,
            "force_rate": float(fps),
            "custom_width": 0, "custom_height": 0,
            "frame_load_cap": int(limit) if limit else 0,
            "skip_first_frames": int(start) if start else 0,
            "select_every_nth": 1,
        }},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {
            "image": ["1", 0], "upscale_model": ["2", 0]}},
    }
    cur = "3"
    if sharpen and sharpen > 0:
        nid = "4"
        wf[nid] = {"class_type": "ImageSharpen", "inputs": {
            "image": [cur, 0], "sharpen_radius": 1,
            "sigma": 1.0, "alpha": float(sharpen)}}
        cur = nid
    wf["5"] = {"class_type": "VHS_VideoCombine", "inputs": {
        "images": [cur, 0], "audio": None, "meta_batch": None, "vae": None,
        "frame_rate": float(fps), "loop_count": 0,
        "filename_prefix": "upscale/pipe", "format": "video/h264-mp4",
        "pix_fmt": "yuv420p", "crf": int(crf),
        "save_metadata": True, "trim_to_audio": False,
        "pingpong": False, "save_output": True}}
    return wf


def frame_count(path):
    cap = cv2.VideoCapture(path)
    n, ok, f = 0, True, None
    ok, f = cap.read()
    while ok:
        n += 1
        ok, f = cap.read()
    cap.release()
    return n


def run_one(client, video_name, out_dir, fps, start, limit, model, sharpen, crf):
    wf = build_workflow(video_name, model, sharpen, fps, limit, start, crf)
    paths = client.run_workflow(wf, out_dir, timeout=1800)
    videos = [p for p in paths if p.lower().endswith((".mp4", ".webm", ".mov"))]
    if not videos:
        raise RuntimeError(f"分段 start={start} limit={limit} 未产出视频")
    return max(videos, key=os.path.getmtime)


def concat_videos(ff, parts, out):
    lst = out + ".list.txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", lst,
           "-c", "copy", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    try:
        os.remove(lst)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--model", default="4x-UltraSharp.pth")
    ap.add_argument("--sharpen", type=float, default=0.2)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--fps", type=int, default=0)
    ap.add_argument("--api", default=COMFY_API)
    ap.add_argument("--full", action="store_true", help="整片分段超分(自动按 --chunk 切)")
    ap.add_argument("--chunk", type=int, default=60, help="--full 时每段帧数")
    ap.add_argument("--force", action="store_true", help="--full 时忽略已存在的分段，全部重跑")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.src)
    fps = a.fps or (cap.get(cv2.CAP_PROP_FPS) or 24)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = frame_count(a.src)
    cap.release()
    print(f"[cfg] {a.src} 源 {w}x{h} @{fps}fps 总帧 {total}", flush=True)

    client = ComfyUIClient(a.api, timeout=1800)
    if not client.is_ready():
        print("[err] ComfyUI 未就绪(8188)")
        raise SystemExit(1)

    # 源片拷到 ComfyUI input 目录，VHS_LoadVideo 按文件名读取（绝对路径在 Win 下易踩坑）
    base = os.path.basename(a.src)
    dst_in = os.path.join(COMFY_INPUT, base)
    if os.path.abspath(a.src) != os.path.abspath(dst_in):
        shutil.copy(a.src, dst_in)
    print(f"[cfg] 源片已置于 ComfyUI input: {dst_in}", flush=True)

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out_dir = os.path.dirname(os.path.abspath(a.dst))
    os.makedirs(out_dir, exist_ok=True)
    final = a.dst

    if a.full:
        chunk = a.chunk
        stem = os.path.splitext(base)[0]
        segs = []
        start, idx = 0, 0
        while start < total:
            lim = min(chunk, total - start)
            seg = os.path.join(out_dir, f".{stem}_seg{idx:03d}.mp4")
            if (not a.force) and os.path.exists(seg):
                print(f"[info] 分段 {idx} 已存在, 跳过 (start={start})", flush=True)
                segs.append(seg)
                start += lim
                idx += 1
                continue
            print(f"[info] 分段 {idx} start={start} limit={lim} ...", flush=True)
            silent = run_one(client, base, out_dir, fps, start, lim,
                             a.model, a.sharpen, a.crf)
            shutil.move(silent, seg)
            segs.append(seg)
            print(f"[info] 分段 {idx} 完成 -> {seg}", flush=True)
            start += lim
            idx += 1
        print(f"[info] 拼接 {len(segs)} 段静音超分片...", flush=True)
        silent_full = os.path.join(out_dir, f".{stem}_silent.mp4")
        concat_videos(ff, segs, silent_full)
        # 整片只混回一次原生立体声，避免多段音频接缝
        cmd = [ff, "-y", "-i", silent_full, "-i", a.src,
               "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "copy",
               "-shortest", final]
        print(f"[info] 混音: {' '.join(cmd)}", flush=True)
        subprocess.run(cmd, check=False)
        for s in segs:
            try: os.remove(s)
            except OSError: pass
        try: os.remove(silent_full)
        except OSError: pass
        print("done", final)
        return

    # 单段模式（原逻辑）
    wf = build_workflow(base, a.model, a.sharpen, fps, a.limit, a.start, a.crf)
    print("[info] 提交超分工作流...", flush=True)
    paths = client.run_workflow(wf, out_dir, timeout=1800)
    videos = [p for p in paths if p.lower().endswith((".mp4", ".webm", ".mov"))]
    if not videos:
        print("[err] 未产出超分视频")
        raise SystemExit(1)
    silent = max(videos, key=os.path.getmtime)
    print(f"[info] 超分静音片: {silent}", flush=True)

    # 混回原片对应片段的原生立体声音轨
    seg_args = []
    if a.start or a.limit:
        seg_args = ["-ss", f"{a.start / fps:.3f}", "-t", f"{(a.limit or 1e9) / fps:.3f}"]
    cmd = [ff, "-y", "-i", silent] + seg_args + ["-i", a.src,
            "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "copy",
            "-shortest", final]
    print(f"[info] 混音: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=False)
    print("done", final)


if __name__ == "__main__":
    main()
