"""实验：白模 ref_video（参考视频）→ H3 Hybrid 链路验证。

验证三件事：
  1. 干跑：image(首帧) + ref_video 是否被判为 Hybrid，且 `ref_videos.ref_video_0`
     Autogrow 键名拼对（键名错会被节点静默丢弃并报 "requires at least one reference media"）。
  2. 参考视频是否满足 H3 官方 2~15s 策略（本项目灰模动画由 blender.anim_frames=auto 保证 ≥2s）。
  3. 真跑出一镜，确认时长 = num_frames/fps 且 Hybrid 不报错。

Blender MCP 未就绪时，用「替身参考视频」（从已有出片裁 56 帧）也能验证代码链路；
白模真实效果仍需 Blender MCP(9876) 在线时先跑 `python cli.py blender --mode block`。

用法:
    python _exp_ref_video.py                 # 替身参考视频 + 真跑
    python _exp_ref_video.py --dry           # 只干跑检查工作流接线，不占 GPU
    python _exp_ref_video.py <ref.mp4>       # 指定参考视频（如白模 blocking.mp4）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import run_series as rs

ROOT = rs.ROOT
# 与 config.yaml 一致：768x448 / 56 帧 @24fps = 2.33s（17n+5 网格内，且 > 参考视频 2s 下限）
W, H, FPS, FRAMES = 768, 448, 24, 56
SHOT = 2
MIN_REF_SECONDS, MAX_REF_SECONDS = 2.0, 15.0
OUT = os.path.join(ROOT, "outputs", "_exp_ref_video")
os.makedirs(OUT, exist_ok=True)


def _ffmpeg() -> str:
    """与 agent/editor.py 一致：PATH → imageio-ffmpeg（本机 ffmpeg 不在 PATH）。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg        # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:           # noqa: BLE001
        raise SystemExit(f"找不到 ffmpeg（白模灰模动画合成也依赖它）: {e}")


def _duration(path: str, ff: str) -> float:
    """用 ffmpeg -i 的 stderr 解析时长（不再额外依赖 ffprobe）。"""
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True, errors="replace")
    for line in (r.stderr or "").splitlines():
        if "Duration:" in line:
            try:
                h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:                      # noqa: BLE001 - 解析失败按 0 处理
                return 0.0
    return 0.0


def motion_profile(path: str) -> dict:
    """粗测全局运动：平均光流的 dx/dy 与幅值 → 判断出片运镜是否跟随参考视频。"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    prev, dxs, dys, mags = None, [], [], []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY)
        if prev is not None:
            fl = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            dxs.append(float(fl[..., 0].mean()))
            dys.append(float(fl[..., 1].mean()))
            mags.append(float(np.sqrt(fl[..., 0] ** 2 + fl[..., 1] ** 2).mean()))
        prev = g
    cap.release()
    n = max(len(mags), 1)
    return {"pairs": len(mags), "dx": sum(dxs) / n, "dy": sum(dys) / n,
            "mag": sum(mags) / n}


def build_standin(ff: str) -> str:
    """替身参考视频：从已有出片裁 FRAMES 帧、缩到出片分辨率，时长 = FRAMES/FPS。"""
    dst = os.path.join(OUT, "ref_standin.mp4")
    if os.path.exists(dst) and abs(_duration(dst, ff) - FRAMES / FPS) < 0.15:
        print(f"[ref] 复用替身参考视频 {dst}", flush=True)
        return dst
    src = os.path.join(ROOT, "outputs", "_tune_ep2.mp4")
    if not os.path.exists(src):
        pool = [os.path.join(ROOT, "outputs", n)
                for n in os.listdir(os.path.join(ROOT, "outputs")) if n.endswith(".mp4")]
        if not pool:
            raise SystemExit("outputs 下没有可当替身的 mp4")
        src = max(pool, key=os.path.getmtime)
    subprocess.run([ff, "-y", "-v", "error", "-i", src,
                    "-vf", (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                            f"crop={W}:{H},fps={FPS}"),
                    "-frames:v", str(FRAMES), "-an", dst], check=True)
    print(f"[ref] 生成替身参考视频 {dst}（源 {os.path.basename(src)}）", flush=True)
    return dst


def main() -> int:
    ff = _ffmpeg()
    use_ref = "--no-ref-video" not in sys.argv     # 对照组：不传 ref_video，用于 A/B
    if use_ref:
        ref = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
               and os.path.exists(sys.argv[1]) else build_standin(ff))
        dur = _duration(ref, ff)
        ok = MIN_REF_SECONDS <= dur <= MAX_REF_SECONDS
        print(f"[ref] {ref}\n      时长 {dur:.2f}s（H3 官方策略 {MIN_REF_SECONDS:g}~{MAX_REF_SECONDS:g}s，"
              f"{'OK' if ok else '不满足'})", flush=True)
        # --force：跳过 2~15s 时长闸门，用于探测节点对越界参考视频的真实反应
        if not ok and "--force" not in sys.argv:
            print("[ref] 参考视频时长不达标；灰模动画请用 blender.anim_frames: auto", flush=True)
            return 2
    else:
        ref = ""
        print("[ref] 对照组：不传 ref_video（仅首帧 + ref_images）", flush=True)

    prompt = rs.load_json(rs.SHOTS_FILE)["ep1"][SHOT - 1]
    img = rs.load_json(os.path.join(ROOT, "outputs", "anchor",
                                    "ep1_anchor_map.json")).get(str(SHOT)) or ""
    imgp = img if os.path.isabs(img) else os.path.join(ROOT, img)
    if not (img and os.path.exists(imgp)):
        imgp = os.path.join(ROOT, "outputs", "anchor", "mira_front.png")
    print(f"[cfg] {W}x{H} {FRAMES}帧@{FPS}fps  prompt={prompt[:70]}...\n"
          f"[cfg] first_frame={imgp}", flush=True)

    # 按 config 现状决定是否一并喂白模控制图（与真实管线保持一致）
    bcfg = (rs.load_config().get("blender", {}) or {})
    ctrl = []
    if bcfg.get("use_as_ref_images") and "--no-ref-images" not in sys.argv:
        ctrl = [p for p in (os.path.join(ROOT, "outputs", "blocking", n)
                            for n in ("depth.png", "normal.png", "line.png"))
                if os.path.exists(p)]
    print(f"[cfg] use_as_ref_images={bool(bcfg.get('use_as_ref_images'))}"
          f"{'（被 --no-ref-images 覆盖）' if '--no-ref-images' in sys.argv else ''} "
          f"ref_images={[os.path.basename(p) for p in ctrl]}", flush=True)

    eng = rs.build_engine(W, H, FRAMES, FPS, "mmh3")
    if not eng.is_ready():
        print("[eng] ComfyUI 未就绪（8188）", flush=True)
        return 1

    # ---- 干跑：只检查工作流接线，不占 GPU ----
    wf = eng._build_workflow(prompt, 1, imgp, ref or None, ctrl or None)
    for nid, node in wf.items():
        ins = node.get("inputs") or {}
        if "task_type" in ins:
            ref_keys = sorted(k for k in ins if k.startswith("ref_"))
            print(f"[dry] 条件节点 {nid} ({node.get('class_type')}) "
                  f"task_type={ins['task_type']} first_frame={'first_frame' in ins} "
                  f"ref_keys={ref_keys}", flush=True)
            expect = "Hybrid" if (use_ref or ctrl) else "I2VA"
            if ins["task_type"] != expect:
                print(f"[dry] !! 期望 {expect}，实际 {ins['task_type']}，接线有问题", flush=True)
                return 3
            if use_ref and "ref_videos.ref_video_0" not in ins:
                print("[dry] !! ref_videos 键名不对，节点会丢弃该输入", flush=True)
                return 3
            if ctrl and not [k for k in ins if k.startswith("ref_images.")]:
                print("[dry] !! ref_images 键名不对，节点会丢弃该输入", flush=True)
                return 3
    if "--dry" in sys.argv:
        print("[dry] 接线检查通过（未生成）", flush=True)
        return 0

    # ---- 真跑 ----
    tag = ("" if use_ref else "_norv") + ("" if ctrl else "_nori")
    out = os.path.join(OUT, f"hybrid_shot{SHOT}{tag}.mp4")
    seed = 20260907 + 1000 + SHOT
    t0 = time.time()
    try:
        eng.generate(prompt, out, seed=seed, image=imgp, ref_video=ref or None,
                     ref_images=ctrl or None)
    except Exception as e:                     # noqa: BLE001 - 探测节点对越界参考视频的反应
        print(f"[err] {type(e).__name__}: {str(e)[:600]}", flush=True)
        return 4
    dt = time.time() - t0
    print(f"[ok] {out}  {os.path.getsize(out) // 1024}KB  用时 {dt:.0f}s "
          f"（{FRAMES}帧@{FPS}fps = 应约 {FRAMES / FPS:.2f}s）", flush=True)

    # ---- 客观指标：出片运镜是否跟随参考视频（有 A/B 对照才可下结论） ----
    mo = motion_profile(out)
    print(f"[motion] out  dx={mo['dx']:+.4f} dy={mo['dy']:+.4f} mag={mo['mag']:.4f}", flush=True)
    if use_ref:
        mr = motion_profile(ref)
        print(f"[motion] ref  dx={mr['dx']:+.4f} dy={mr['dy']:+.4f} mag={mr['mag']:.4f}", flush=True)
        same = (mr["dx"] * mo["dx"] >= 0) and (mr["dy"] * mo["dy"] >= 0)
        print(f"[motion] 与参考视频方向{'一致' if same else '不一致'}（dx/dy 同号=同向）", flush=True)
    # 所有组的横向汇总（A/B 只有横向对比才有意义）
    print("[ab] 各组 motion 汇总（rv=带 ref_video, ri=带 ref_images）：", flush=True)
    for name in (f"hybrid_shot{SHOT}.mp4", f"hybrid_shot{SHOT}_norv.mp4",
                 f"hybrid_shot{SHOT}_noref.mp4", f"hybrid_shot{SHOT}_nori.mp4",
                 f"hybrid_shot{SHOT}_norv_nori.mp4"):
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            m = motion_profile(p)
            print(f"[ab]   {name:32s} dx={m['dx']:+.4f} dy={m['dy']:+.4f} "
                  f"mag={m['mag']:.4f}", flush=True)

    from agent import record
    record.save(OUT, f"hybrid_shot{SHOT}", record.collect(
        eng, prompt=prompt, seed=seed, attempt=0, image=imgp,
        ref_images=ctrl or None, ref_video=ref))
    print(f"[record] {os.path.join(OUT, 'gen_params.json')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
