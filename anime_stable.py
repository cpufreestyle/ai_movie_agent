#!/usr/bin/env python
"""动漫化：光流时序传播，解决逐帧重绘"缓慢变幻凌乱"。

两种模式：
  --mode prop（推荐，最稳）：逐帧"传播 + 轻刷新"
     · 首帧：对原帧跑 img2img(denoise=--denoise，如 0.70) 建立动漫；
     · 之后每帧：用源片光流把【上一帧的动漫输出】按运动搬运到当前位置，
       再以很低的 denoise(--refresh，如 0.25) 只做"轻微刷新"（不重新演绎内容）。
     init 已是动漫 → 风格连续；低 denoise → 内容不再逐帧乱变；每帧都刷新 → 不拖影不漂移。
  --mode keyframe（较快）：只对关键帧(每 --key 帧)重绘，中间帧光流搬运关键帧结果。

用法:
  python anime_stable.py <src.mp4> <dst.mp4> --mode prop --denoise 0.70 --refresh 0.25
  python anime_stable.py <src.mp4> <dst.mp4> --mode keyframe --key 8 --blend 0.85
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys

import cv2
import numpy as np
import imageio_ffmpeg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anime_redraw import (build_wf, QUALITY, NEGATIVE, CKPT, _ROOT,      # noqa: E402
                          ensure_single_redraw)
from tools.comfyui_client import ComfyUIClient                           # noqa: E402

# 3D 动漫（卡通/皮克斯风，刻意避免写实）提示词预设
STYLE3D_POS = (
    "3d anime, stylized cartoon 3d character, pixar cartoon style, cel shaded 3d, "
    "toon shading, big eyes, simplified stylized facial features, toy-like 3d, "
    "smooth clean 3d render, non photorealistic, clean 3d animation film still, "
    "same composition, same scene layout")
STYLE3D_NEG = (
    "photorealistic, real person, realistic skin texture, hyperrealistic, "
    "live action photo, 3d scan of real human, real human face, "
    "flat 2d anime, hand drawn, sketch, lineart, blurry, lowres, bad anatomy, "
    "bad hands, watermark, text, extra limbs, deformed face, "
    "different composition, different pose")


def build_flow_map(base_small, cur_small, w, h, scale, flow_max=0.0):
    """cur→base 反向光流，返回【绝对采样坐标】(mx,my) 供 cv2.remap。
    ⚠️ remap 的 map = 恒等网格 + 位移（绝对坐标），不能直接传位移量。
    flow_max>0 时把位移量限幅（小分辨率像素），抑制光流误匹配导致的过度扭曲。"""
    # 金字塔层级=4、窗口=21、迭代=5：比默认更准，减少搬运扭曲
    flow = cv2.calcOpticalFlowFarneback(cur_small, base_small, None,
                                        0.5, 4, 21, 3, 5, 1.2, 0)
    if flow_max > 0:
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        s = np.minimum(1.0, flow_max / np.maximum(mag, 1e-6))
        flow *= s[..., None]
    flow = cv2.resize(flow, (w, h), interpolation=cv2.INTER_LINEAR)
    flow *= 1.0 / scale
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    return gx + flow[..., 0], gy + flow[..., 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--mode", choices=["prop", "keyframe", "smooth"], default="keyframe",
                    help="smooth=源片锚定+运动补偿时序融合(推荐,稳且不花屏)；"
                         "keyframe=关键帧+光流(稳)；prop=逐帧传播(⚠️约10帧后花屏，勿用)")
    ap.add_argument("--ckpt", default=CKPT)
    ap.add_argument("--denoise", type=float, default=0.70,
                    help="首帧/关键帧重绘强度（0% 检出需 0.70）")
    ap.add_argument("--refresh", type=float, default=0.25,
                    help="prop 模式逐帧轻刷新强度（低=更稳，高=更清晰但可能漂）")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--cfg", type=float, default=7.0)
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--key", type=int, default=8)
    ap.add_argument("--blend", type=float, default=0.85)
    ap.add_argument("--hires", type=float, default=1.0,
                    help="hires-fix 放大倍数(>1 开启)：原分辨率出图后放大再低 denoise 精修")
    ap.add_argument("--hires-denoise", type=float, default=0.30,
                    help="hires 精修强度（低=更清晰少改内容）")
    ap.add_argument("--extra", default="", help="追加画风正向提示词")
    ap.add_argument("--style3d", action="store_true", help="改用 3D 动漫(CG/皮克斯)风格提示词")
    ap.add_argument("--reanchor", type=int, default=0,
                    help="prop 模式每隔 N 帧用源片重新 img2img 切断漂移(0=不重置)。长片必须>0")
    ap.add_argument("--negative", default="", help="覆盖负向提示词（SDXL 用 Animagine 风格）")
    ap.add_argument("--base-scale", type=float, default=1.0,
                    help="首/关键帧基准分辨率放大倍数（SDXL 建议 1.5，即 768→1152）")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--flow-scale", type=float, default=1.0,
                    help="光流计算分辨率系数（1.0=全分辨率，更准、扭曲更少）")
    ap.add_argument("--flow-max", type=float, default=32.0,
                    help="光流位移限幅（小分辨率像素，抑制误匹配扭曲；0=不限）")
    ap.add_argument("--cut-thresh", type=float, default=18.0,
                    help="场景切换检测阈值（下采样灰度逐帧差，越小越敏感；0=关）")
    a = ap.parse_args()
    ensure_single_redraw()

    client = ComfyUIClient("http://127.0.0.1:8188", timeout=900)
    if not client.is_ready():
        print("[err] ComfyUI 未就绪(8188)")
        raise SystemExit(1)

    cap = cv2.VideoCapture(a.src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if a.start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, a.start)
    need = a.limit if a.limit else (total - a.start)
    frames = []
    for _ in range(need):
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    n = len(frames)
    if n == 0:
        print("[err] 未读到帧")
        raise SystemExit(1)

    inp = os.path.join(_ROOT, "input")
    outd = os.path.join(_ROOT, "output")
    os.makedirs(inp, exist_ok=True)
    os.system(f'del /q "{inp}\\redraw_*.png" >nul 2>nul')
    os.system(f'del /q "{outd}\\redraw_*.png" >nul 2>nul')

    base_pos = STYLE3D_POS if a.style3d else QUALITY
    base_neg = STYLE3D_NEG if a.style3d else NEGATIVE
    prompt = (base_pos + ", " + a.extra) if a.extra else base_pos
    negative = a.negative or base_neg

    def one_pass(img, denoise, tag):
        """单次动漫 img2img。"""
        name = f"redraw_{tag}.png"
        cv2.imwrite(os.path.join(inp, name), img)
        wf = build_wf(a.ckpt, prompt, negative, a.seed, a.steps, a.cfg, denoise)
        wf["2"]["inputs"]["image"] = name
        try:
            paths = client.run_workflow(wf, inp, timeout=900)
        except Exception as e:
            print(f"  [err] {tag}: {e}", flush=True)
            return None
        if not paths:
            return None
        out = cv2.imread(paths[0])
        return out

    def redraw(img, denoise, tag):
        """动漫 img2img；--base-scale 放大基准分辨率；--hires>1 再做"放大→低 denoise 精修"。"""
        work = img
        if a.base_scale > 1.0:
            work = cv2.resize(img, None, fx=a.base_scale, fy=a.base_scale,
                              interpolation=cv2.INTER_CUBIC)
        out = one_pass(work, denoise, tag)
        if out is None:
            return img.copy()
        if a.hires > 1.0:
            up = cv2.resize(out, None, fx=a.hires, fy=a.hires,
                            interpolation=cv2.INTER_CUBIC)
            ref = one_pass(up, a.hires_denoise, f"h{tag}")
            if ref is not None:
                out = ref
        if out.shape[1] != w or out.shape[0] != h:
            out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
        return out

    tmp = a.dst + ".silent.mp4"
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    scale = a.flow_scale
    small = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), None,
                        fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             for f in frames]

    if a.mode == "keyframe":
        # 场景切换检测：切点强制关键帧，避免把上一镜画面光流搬运到新镜头（否则整段糊掉）
        _d = [0.0]
        for _i in range(1, n):
            _d.append(float(np.mean(np.abs(small[_i].astype(np.float32)
                                           - small[_i - 1].astype(np.float32)))))
        _med = float(np.median(_d[1:])) if n > 1 else 0.0
        _thr = max(a.cut_thresh, 4.0 * _med) if a.cut_thresh > 0 else 9e9
        cuts = [i for i in range(1, n) if _d[i] > _thr]
        key_idx = sorted(set(list(range(0, n, a.key)) + cuts + [n - 1]))
        print(f"[cfg] keyframe {a.src} {w}x{h} 共{n}帧 denoise={a.denoise} "
              f"key={a.key} blend={a.blend} 关键帧={len(key_idx)} "
              f"切点={len(cuts)}(阈值{_thr:.1f})", flush=True)
        redraws = {}
        for k in key_idx:
            redraws[k] = redraw(frames[k], a.denoise, f"k{k:05d}")
            print(f"[key] 关键帧 {k} 完成", flush=True)
        prev_out, base_k = None, key_idx[0]
        for i in range(n):
            if i in redraws:
                out, base_k = redraws[i], i
            else:
                mx, my = build_flow_map(small[base_k], small[i], w, h, scale,
                                        a.flow_max)
                out = cv2.remap(redraws[base_k], mx, my, cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)
            if a.blend < 1.0 and prev_out is not None:
                out = cv2.addWeighted(out, a.blend, prev_out, 1.0 - a.blend, 0)
            vw.write(out)
            prev_out = out
            if (i + 1) % 20 == 0:
                print(f"[info] {i + 1} 帧已输出", flush=True)
    elif a.mode == "prop":  # 逐帧传播 + 轻刷新
        print("[warn] prop 模式会累积 VAE/光流误差，约 10 帧后发散花屏；"
              "建议改用 --mode keyframe（--key 1 即朴素逐帧重绘）", flush=True)
        print(f"[cfg] prop {a.src} {w}x{h} 共{n}帧 denoise={a.denoise} "
              f"refresh={a.refresh} blend={a.blend}", flush=True)
        prev_out, prev_small = None, None
        for i in range(n):
            reanchor = (i == 0) or (a.reanchor and i % a.reanchor == 0)
            if reanchor:
                # 用源片重新 img2img，切断传播漂移（否则数十帧后发散成噪点）
                out = redraw(frames[i], a.denoise, f"{i:05d}")
            else:
                mx, my = build_flow_map(prev_small, small[i], w, h, scale,
                                        a.flow_max)
                warped = cv2.remap(prev_out, mx, my, cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)
                out = redraw(warped, a.refresh, f"{i:05d}")
            if a.blend < 1.0 and prev_out is not None:
                out = cv2.addWeighted(out, a.blend, prev_out, 1.0 - a.blend, 0)
            vw.write(out)
            prev_out, prev_small = out, small[i]
            if (i + 1) % 20 == 0:
                print(f"[info] {i + 1} 帧已输出", flush=True)
    else:  # smooth：运动补偿时序融合（源片锚定 + 上一帧输出的光流对齐融合）
        print(f"[cfg] smooth {a.src} {w}x{h} 共{n}帧 denoise={a.denoise} "
              f"blend={a.blend}", flush=True)
        prev_out, prev_small = None, None
        for i in range(n):
            # 每帧仍用【源片】出新鲜图 → 锚定，不累积漂移/花屏
            out = redraw(frames[i], a.denoise, f"{i:05d}")
            if prev_out is not None and a.blend < 1.0:
                mx, my = build_flow_map(prev_small, small[i], w, h, scale,
                                        a.flow_max)
                warped = cv2.remap(prev_out, mx, my, cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)
                # 与"上一帧输出按运动对齐后"融合，压住逐帧沸腾
                out = cv2.addWeighted(out, a.blend, warped, 1.0 - a.blend, 0)
            vw.write(out)
            prev_out, prev_small = out, small[i]
            if (i + 1) % 20 == 0:
                print(f"[info] {i + 1} 帧已输出", flush=True)

    vw.release()
    os.system(f'del /q "{inp}\\redraw_*.png" >nul 2>nul')
    os.system(f'del /q "{outd}\\redraw_*.png" >nul 2>nul')
    print(f"[info] 完成 {n} 帧 -> {tmp}", flush=True)

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-i", tmp, "-i", a.src,
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-shortest", a.dst], check=False)
    print("done", a.dst)


if __name__ == "__main__":
    main()
