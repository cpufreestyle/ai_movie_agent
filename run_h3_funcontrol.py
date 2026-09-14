#!/usr/bin/env python
"""H3 Fun Control 真机验证：白模 depth 序列作逐帧稠密条件，看 H3 是否跟随走位。

路线：MiniMaxH3FunControlLoader/ApplyT8Advanced 把 control_video(depth 帧) 注入 H3 DiT，
      等价于「白模负责走位，H3 负责渲染」。

用法:
  python run_h3_funcontrol.py --control outputs/blocking/_i2v/control_depth.mp4 \
      --prompt-file outputs/blocking/_i2v/prompt.txt --out outputs/blocking/_i2v/h3_fc.mp4 --ab

A/B: --ab 时额外跑一版「无 control」(同 seed)，比较走位方向(dx)。
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid

COMFY = "http://127.0.0.1:8188"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机不走代理
W = 768
H = 448
LENGTH = 56  # 17*3+5
CONTROLNET = "minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors"
BASE_WF = "E:/ComfyUI_models/mmh3_turbo_api.json"


def post_json(path, data=None, raw=None, timeout=600):
    req = urllib.request.Request(COMFY + path,
                                 data=json.dumps(data).encode() if data is not None else raw,
                                 headers={"Content-Type": "application/json"})
    return json.loads(OP.open(req, timeout=timeout).read())


def upload_video(path):
    import requests
    with open(path, "rb") as f:
        r = requests.post(COMFY + "/upload/image",
                          files={"image": (os.path.basename(path), f, "video/mp4")},
                          proxies={}, timeout=120)
    return r.json().get("name")


def refresh():
    try:
        post_json("/refresh")
    except Exception as e:
        print("[warn] refresh failed:", e)


def build_workflow(control_video_name, use_control, seed, strength=0.8,
                   control_kind="depth", end_percent=0.85):
    wf = json.load(open(BASE_WF))["prompt"]
    # 基础生成参数对齐控制视频
    wf["6"]["inputs"]["width"] = W
    wf["6"]["inputs"]["height"] = H
    wf["6"]["inputs"]["length"] = LENGTH
    wf["9"]["inputs"]["noise_seed"] = seed

    if use_control:
        # Fun Control 加载器
        wf["30"] = {"class_type": "MiniMaxH3FunControlLoaderT8Advanced",
                    "_meta": {"title": "FunControlLoader"},
                    "inputs": {"control_net_name": CONTROLNET}}
        # 控制视频加载（VHS）
        wf["31"] = {"class_type": "VHS_LoadVideo",
                    "_meta": {"title": "LoadControlVideo"},
                    "inputs": {"video": control_video_name,
                               "force_rate": 24.0,
                               "custom_width": W, "custom_height": H,
                               "frame_load_cap": LENGTH, "skip_first_frames": 0,
                               "select_every_nth": 1}}
        # Fun Control Apply：注入 DiT
        wf["32"] = {"class_type": "MiniMaxH3FunControlApplyT8Advanced",
                    "_meta": {"title": "FunControlApply"},
                    "inputs": {"model": ["2", 0], "positive": ["6", 0],
                               "control_net": ["30", 0], "vae": ["4", 0],
                               "control_video": ["31", 0],
                               "width": W, "height": H, "length": LENGTH,
                               "control_kind": control_kind, "fit_mode": "exact",
                               "strength": strength, "start_percent": 0.0,
                               "end_percent": end_percent}}
        # 把打了 control patch 的 model / positive 喂给采样（经 BasicGuider）
        wf["8"]["inputs"]["model"] = ["32", 0]
        wf["8"]["inputs"]["conditioning"] = ["32", 1]
    else:
        # 基线：原模型/条件
        wf["8"]["inputs"]["model"] = ["7", 0]
        wf["8"]["inputs"]["conditioning"] = ["6", 0]
    return wf


def run_one(control_name, use_control, seed, out_path, strength=0.8,
            control_kind="depth", end_percent=0.85):
    wf = build_workflow(control_name, use_control, seed, strength, control_kind, end_percent)
    cid = str(uuid.uuid4())
    resp = post_json("/prompt", {"prompt": wf, "client_id": cid})
    pid = resp.get("prompt_id") or cid  # ←必须用 ComfyUI 返回的 prompt_id，而非本地 uuid
    # 轮询历史
    for _ in range(600):
        try:
            hist = post_json("/history?max_items=500")
        except Exception:
            hist = {}
        if pid in hist:
            info = hist[pid]
            if info.get("status", {}).get("status") == "error" or "exception" in info:
                raise RuntimeError("ComfyUI 任务失败: " + str(info.get("exception") or info.get("status"))[:600])
            outs = info.get("outputs", {})
            # 找视频输出（VHS_VideoCombine 输出 'gifs'）
            for nid, o in outs.items():
                if "gifs" in o:
                    g = o["gifs"][0]
                    fn = g["filename"]
                    sub = urllib.parse.quote(g.get("subfolder", ""))
                    typ = g.get("type", "output")
                    url = f"{COMFY}/view?filename={urllib.parse.quote(fn)}&subfolder={sub}&type={typ}"
                    data = OP.open(urllib.request.Request(url), timeout=120).read()
                    with open(out_path, "wb") as f:
                        f.write(data)
                    print(f"[OK] {out_path} ({os.path.getsize(out_path)//1024}KB)")
                    return out_path
            if outs:
                raise RuntimeError("任务完成但无视频输出: " + str(outs)[:300])
        time.sleep(3)
    raise TimeoutError("ComfyUI 超时未出片")


def motion_dx(video):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(video)
    prev = None
    dxs = []
    while True:
        r, f = cap.read()
        if not r:
            break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(float)
        if prev is not None:
            fl = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            dxs.append(fl[..., 0].mean())
        prev = g
    cap.release()
    return float(np.mean(dxs)) if dxs else 0.0


def motion_trend(video):
    """前景运动质心的水平漂移（像素/帧，正=右）。比全图平均光流更敏感，
    过滤静止背景，专抓主体(角色)的屏幕位移。"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(video)
    prev = None
    cxs = []
    while True:
        r, f = cap.read()
        if not r:
            break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            fl = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            mag = np.hypot(fl[..., 0], fl[..., 1])
            th = mag.mean() + 1.5 * mag.std()
            ys, xs = np.where(mag > th)
            if len(xs) > 50:
                cxs.append(float(xs.mean()))
        prev = g
    cap.release()
    if len(cxs) < 4:
        return 0.0
    x = np.arange(len(cxs))
    slope = np.polyfit(x, np.array(cxs), 1)[0]
    return float(slope)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", default="", help="现成的白模 depth 控制视频（或用 --walk 自动生成）")
    ap.add_argument("--walk", default="",
                    help="白模走位 'x1,y1:x2,y2'，自动调 gen_blocking --depth 生成控制视频（端到端）")
    ap.add_argument("--cam", default="static", help="--walk 时的相机运动")
    ap.add_argument("--no-track", action="store_true", help="--walk 时不跟拍（横向走位才体现）")
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--prompt", default="A young woman in a neon city walks from left to right, cinematic, detailed")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ab", action="store_true", help="额外跑无 control 基线做同 seed 对照")
    ap.add_argument("--baseline-only", action="store_true",
                    help="只跑无 control 基线（避免与 control 版连续跑导致显存累积）")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--strength", type=float, default=0.8, help="Fun Control 强度（可 >1）")
    ap.add_argument("--control-kind", default="depth",
                    help="控制类型标签: depth/pose/edge/canny/HED/MLSD")
    ap.add_argument("--end-percent", type=float, default=0.85, help="控制生效到多少采样进度")
    a = ap.parse_args()

    # 注：--prompt / --prompt-file 当前未参与出片（基础工作流自带 prompt），保留参数仅为兼容旧命令
    refresh()
    ctrl = a.control
    if a.walk:
        ctrl = ctrl or "outputs/blocking/_i2v/control_depth_auto.mp4"
        cmd = [sys.executable, "gen_blocking.py", "--out", ctrl,
               "--frames", str(LENGTH), "--fps", "24", "--res", f"{W}x{H}",
               "--cam", a.cam, "--depth", "--walk=" + a.walk]
        if a.no_track:
            cmd.append("--no-track")
        print("[gen] 白模 depth 控制视频:", " ".join(cmd))
        subprocess.run(cmd, check=True)
    if not ctrl or not os.path.exists(ctrl):
        raise SystemExit("需要 --control <视频> 或 --walk <走位> 之一")
    cname = upload_video(ctrl)

    if a.baseline_only:
        run_one(cname, False, a.seed, a.out)
        dx = motion_dx(a.out)
        tr = motion_trend(a.out)
        print(f"[motion] 基线(nocontrol) dx={dx:+.4f} trend={tr:+.3f}px/帧  {'右' if tr>0 else '左'}")
        return

    print(f"[run] control=on seed={a.seed} strength={a.strength}")
    test_out = a.out
    run_one(cname, True, a.seed, test_out, a.strength, a.control_kind, a.end_percent)
    dx_test = motion_dx(test_out)
    tr_test = motion_trend(test_out)
    print(f"[motion] 测试(control) dx={dx_test:+.4f} trend={tr_test:+.3f}px/帧  {'右' if tr_test>0 else '左'}")

    if a.ab:
        base_out = os.path.splitext(a.out)[0] + "_nocontrol.mp4"
        run_one(cname, False, a.seed, base_out)
        dx_base = motion_dx(base_out)
        tr_base = motion_trend(base_out)
        print(f"[motion] 基线(nocontrol) dx={dx_base:+.4f} trend={tr_base:+.3f}px/帧  {'右' if tr_base>0 else '左'}")
        print(f"[结论] 白模意图右移; 测试 trend={tr_test:+.3f} vs 基线 trend={tr_base:+.3f}")


def use_control_desc(_):
    return "on"


if __name__ == "__main__":
    main()
