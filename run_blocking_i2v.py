#!/usr/bin/env python
"""白模负责走位/构图，H3 负责渲染：Blender 白模(含角色走位) -> H3 I2V 首帧 + 锚定图 ref_image。

这是「白模负责走位、H3 负责渲染」的**推荐落地路径**：
  - ref_video(灰模动画) 路径经 A/B 实测有害（不传运动、反而压制运镜、画质降、慢 5 倍），
    已弃用（见 docs/h3_blocking_guide.md §七）。
  - H3 唯一能可靠承接白模的杠杆是**首帧(I2V start)**：白模先在 3D 里确定性地摆好
    角色站位/走位起点与机位，渲染一张构图帧；该帧作 H3 的 I2V 首帧(锁构图/站位/机位)，
    再把角色锚定图(Mira)作 ref_image(Hybrid) 给 H3 提供身份，H3「渲染」成实拍。

用法:
  # 单镜：白模走位 -> H3（构图帧作首帧 + Mira 锚定 ref_image）
  python run_blocking_i2v.py --prompt "一个少女从画面左侧走向右侧" \
      --walk "-3,0:3,0" --no-track --out outputs/blocking/i2v_shot.mp4

  # A/B：同时出对照组(锚定图首帧, 当前生产默认) 与 测试组(白模首帧+锚定ref)，
  #      打印两组 motion 指标（dx/dy 同号=白模锁走位生效），便于量化对比
  python run_blocking_i2v.py --prompt "一个少女从画面左侧走向右侧" \
      --walk "-3,0:3,0" --no-track --ab
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import run_series as rs
from _exp_ref_video import motion_profile

ROOT = rs.ROOT
PY = sys.executable
W, H, FPS, FRAMES = 768, 448, 24, 56
SEED = 20260907 + 777


def gen_blocking(out_mp4, previs_png, walk, cam, second, no_track, frames, fps, res):
    """调用 gen_blocking.py 产出灰模动画(参考/调试) + 白模构图帧(I2V 首帧)。"""
    cmd = [PY, "gen_blocking.py", "--out", out_mp4, "--frames", str(frames),
           "--fps", str(fps), "--res", res, "--cam", cam, "--walk=%s" % walk]
    if second:
        cmd += ["--second", second]
    if no_track:
        cmd += ["--no-track"]
    if previs_png:
        cmd += ["--previs-out", previs_png]
    subprocess.run(cmd, check=True)


def run_one(eng, prompt, out, image, ref_images, tag, ref_video=None, seed=SEED):
    """跑一镜并打印 motion 指标，返回出片路径。"""
    t0 = time.time()
    try:
        eng.generate(prompt, out, seed=seed, image=image or None,
                     ref_video=ref_video, ref_images=ref_images or None)
    except Exception as e:                       # noqa: BLE001 - 探测节点真实反应
        print(f"[{tag}] 生成失败: {type(e).__name__}: {str(e)[:400]}", flush=True)
        return None
    dt = time.time() - t0
    if os.path.exists(out):
        mo = motion_profile(out)
        print(f"[motion] {tag:18s} dx={mo['dx']:+.4f} dy={mo['dy']:+.4f} "
              f"mag={mo['mag']:.4f}  ({dt:.0f}s, {os.path.getsize(out)//1024}KB)",
              flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--walk", default="", help="主角走位 'x1,y1:x2,y2'（起点->终点）")
    ap.add_argument("--cam", default="static",
                    choices=["push_in", "pull_out", "lateral", "orbit", "static"])
    ap.add_argument("--second", default="", help="第二角色位置 'x,y'")
    ap.add_argument("--no-track", action="store_true",
                    help="相机不跟拍（固定朝向），让横向走位在画面里体现出来")
    ap.add_argument("--anchor", default=os.path.join(ROOT, "outputs", "anchor",
                                                     "mira_anchor.png"),
                    help="角色锚定图（作 ref_image 给身份）")
    ap.add_argument("--ref-images", default="", help="额外参考图(逗号分隔, 最多到 9 张)")
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs", "blocking", "i2v_shot.mp4"))
    ap.add_argument("--res", default="%dx%d" % (W, H))
    ap.add_argument("--frames", type=int, default=FRAMES)
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--ref-video", action="store_true",
                    help="实验：同时把灰模动画作 ref_video（已知有害, 默认关）")
    ap.add_argument("--ab", action="store_true",
                    help="A/B: 对照组(锚定首帧) + 测试组(白模首帧+锚定ref)")
    a = ap.parse_args()

    prompt = a.prompt
    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as f:
            prompt = f.read().strip()
    if not prompt:
        raise SystemExit("需要 --prompt 或 --prompt-file")

    w, h = (int(x) for x in a.res.lower().split("x"))
    rs.set_workspace("mmh3")
    eng = rs.build_engine(w, h, a.frames, a.fps, "mmh3")
    if not eng.is_ready():
        raise SystemExit("ComfyUI 未就绪（8188）")

    anchor = a.anchor if os.path.isabs(a.anchor) else os.path.join(ROOT, a.anchor)
    extra_refs = [p for p in a.ref_images.split(",") if p and os.path.exists(p)]

    work = os.path.join(ROOT, "outputs", "blocking", "_i2v")
    os.makedirs(work, exist_ok=True)
    gray = os.path.join(work, "gray.mp4")
    previs = os.path.join(work, "blocking_previs.png")

    # ① 先生成白模：灰模动画(参考) + 白模构图帧(I2V 首帧)
    print(f"[blocking] 生成白模: walk={a.walk or '-'} cam={a.cam} "
          f"no_track={a.no_track}", flush=True)
    if not os.path.exists(previs):
        gen_blocking(gray, previs, a.walk, a.cam, a.second, a.no_track,
                     a.frames, a.fps, a.res)
    else:
        print(f"[blocking] 复用已存在构图帧 {previs}", flush=True)

    # ② 灰模动画的运动方向（参考基准：白模想表达的走位）
    if os.path.exists(gray):
        mg = motion_profile(gray)
        print(f"[motion] 灰模走位基准  dx={mg['dx']:+.4f} dy={mg['dy']:+.4f} "
              f"mag={mg['mag']:.4f}", flush=True)

    if a.ab:
        ctrl_out = os.path.join(work, "ab_control.mp4")
        test_out = os.path.join(work, "ab_test.mp4")
        print("\n=== A/B 对照（锚定首帧 = 当前生产默认；白模首帧+锚定ref = 优化路径）===",
              flush=True)
        run_one(eng, prompt, ctrl_out, anchor, None, "对照组(锚定首帧)")
        run_one(eng, prompt, test_out, previs, [anchor] + extra_refs,
                "测试组(白模首帧+锚定ref)")
        print("\n[ab] 对照两组 motion：dx/dy 与灰模走位基准同号=白模锁走位生效；"
              "请目视对比构图是否更贴合白模站位。", flush=True)
        print(f"[ab] 白模构图帧(可目视): {previs}", flush=True)
        print(f"[ab] 对照: {ctrl_out}", flush=True)
        print(f"[ab] 测试: {test_out}", flush=True)
    else:
        run_one(eng, prompt, a.out, previs, [anchor] + extra_refs,
                "白模走位->H3" + (" (+ref_video)" if a.ref_video else ""),
                ref_video=(gray if a.ref_video else None))


if __name__ == "__main__":
    main()
