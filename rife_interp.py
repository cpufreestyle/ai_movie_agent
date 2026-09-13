#!/usr/bin/env python
"""RIFE 补帧：把成片插帧到更高帧率（时长与音频不变）。

H3 原生 24fps、帧数受 17n+5 网格限制，运动感偏弱；用 RIFE 2x 补帧到 48fps
可显著提升流畅度，且不改时长/音频（仅帧率翻倍）。

依赖：
  - ComfyUI 的 ComfyUI-Frame-Interpolation 插件（节点 "RIFE VFI"）
  - 模型 ComfyUI-Frame-Interpolation/ckpts/rife/rife49.pth

用法:
    python rife_interp.py <src.mp4> <out.mp4> [--multiplier 2] [--fps 48]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.comfyui_client import ComfyUIClient

API_DEFAULT = "http://127.0.0.1:8188"


def probe_fps(path: str, fallback: float = 24.0) -> float:
    """用 ffmpeg 读视频帧率（读不到则回退 fallback）。"""
    ff = "ffmpeg"
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    try:
        out = subprocess.run([ff, "-hide_banner", "-i", path],
                             capture_output=True, text=True, errors="ignore").stderr
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", out)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return fallback


def build_workflow(src: str, multiplier: int, out_fps: float,
                   ckpt: str, prefix: str) -> dict:
    return {
        # 1) 加载源视频：本地绝对路径直读（免上传），IMAGE 帧批次 + AUDIO
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {
            "video": os.path.abspath(src),
            "force_rate": 0, "custom_width": 0, "custom_height": 0,
            "frame_load_cap": 0, "skip_first_frames": 0, "select_every_nth": 1}},
        # 2) RIFE 插帧：multiplier 倍
        "2": {"class_type": "RIFE VFI", "inputs": {
            "ckpt_name": ckpt, "frames": ["1", 0],
            "clear_cache_after_n_frames": 10, "multiplier": int(multiplier),
            "fast_mode": True, "ensemble": True, "scale_factor": 1.0,
            "dtype": "float32", "torch_compile": False, "batch_size": 1}},
        # 3) 合成：图像用插值后的帧，音频沿用源（"1" 的第 2 路 AUDIO），帧率提高
        "3": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["2", 0], "audio": ["1", 2],
            "frame_rate": float(out_fps), "loop_count": 0,
            "filename_prefix": prefix, "format": "video/h264-mp4",
            "pingpong": False, "save_output": True}},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="RIFE 补帧（时长/音频不变）")
    ap.add_argument("src", help="源视频路径")
    ap.add_argument("out", help="输出视频路径")
    ap.add_argument("--multiplier", type=int, default=2, help="插帧倍数（默认 2）")
    ap.add_argument("--fps", type=float, default=0.0,
                    help="输出帧率；0=自动（源 fps × multiplier）")
    ap.add_argument("--ckpt", default="rife49.pth")
    ap.add_argument("--prefix", default="H3/rife")
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    if not os.path.exists(a.src):
        raise SystemExit(f"源文件不存在: {a.src}")
    src_fps = probe_fps(a.src)
    out_fps = a.fps if a.fps > 0 else round(src_fps * a.multiplier, 3)
    client = ComfyUIClient(a.api, timeout=a.timeout)
    if not client.is_ready():
        raise SystemExit(f"ComfyUI 未就绪: {a.api}（需加载 ComfyUI-Frame-Interpolation）")

    dest = os.path.dirname(os.path.abspath(a.out)) or "."
    os.makedirs(dest, exist_ok=True)
    wf = build_workflow(a.src, a.multiplier, out_fps, a.ckpt, a.prefix)
    print(f"[rife] {a.src}  源{src_fps}fps → {a.multiplier}x → {out_fps}fps")
    paths = client.run_workflow(wf, dest, timeout=a.timeout)
    videos = [p for p in paths
              if p.lower().endswith((".mp4", ".webm", ".mov"))]
    if not videos:
        raise SystemExit("RIFE 未产出视频（检查 ComfyUI 日志）")
    produced = max(videos, key=os.path.getmtime)
    if os.path.abspath(produced) != os.path.abspath(a.out):
        shutil.move(produced, a.out)
    print(f"[rife] 已生成: {a.out}  ({out_fps}fps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
