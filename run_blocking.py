#!/usr/bin/env python
"""用「白模参考视频」驱动单镜出片（Blender blockout -> MiniMax H3 Hybrid）。

白模先在 3D 里确定性地摆好相机运动与人物走位，再作为 ref_video 喂给 H3：
首帧(image)锁形象/场景，参考视频(ref)锁走位与镜头运动。

用法:
  python run_blocking.py --prompt "..." --ref outputs/blocking/shot1.mp4 \
      --image outputs/anchor/scene_neon_street.png --out outputs/blocking/shot1_hybrid.mp4
  python run_blocking.py --prompt-file ep1_shot1.txt ...   # 从文件读 prompt
"""
from __future__ import annotations

import argparse
import os

import run_series as rs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="", help="从文件读 prompt（与 --prompt 二选一）")
    ap.add_argument("--ref", required=True, help="白模参考视频（ref_video）")
    ap.add_argument("--image", default="", help="首帧锚定图（可选，建议给以锁形象）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--res", default="1024x576")
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20261908)
    ap.add_argument("--ref-images", default="",
                    help="额外参考图（逗号分隔，最多 9 张）。Hybrid 只靠 1 张首帧锁形象时，"
                         "身份信号会被参考视频的运动信号压过导致人物崩坏，补同角色多图可增强身份")
    ap.add_argument("--no-turbo", action="store_true",
                    help="关闭 Turbo LoRA（4 步太糙易崩），改用完整步数")
    ap.add_argument("--steps", type=int, default=30, help="--no-turbo 时的步数")
    ap.add_argument("--engine", default="mmh3")
    a = ap.parse_args()

    prompt = a.prompt
    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as f:
            prompt = f.read().strip()
    if not prompt:
        raise SystemExit("需要 --prompt 或 --prompt-file")
    if not os.path.exists(a.ref):
        raise SystemExit(f"参考视频不存在: {a.ref}")

    w, h = (int(x) for x in a.res.lower().split("x"))
    rs.set_workspace(a.engine)
    eng = rs.build_engine(w, h, a.frames, a.fps, a.engine)
    if not eng.is_ready():
        raise SystemExit("ComfyUI 未就绪（8188）")

    tag = "Hybrid(首帧+参考视频)" if a.image else "Ref2VA(仅参考视频)"
    print(f"[cfg] {a.res} {a.frames}帧@{a.fps}fps  {tag}  seed={a.seed}", flush=True)
    out = eng.generate(prompt, a.out, seed=a.seed,
                       image=a.image or None, ref_video=a.ref)
    print("DONE ->", out, flush=True)


if __name__ == "__main__":
    main()
