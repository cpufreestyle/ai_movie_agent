#!/usr/bin/env python
"""Wan2.2 多镜头成片：按《看见未来之前》科幻比赛脚本生成 5 镜并拼接。

镜1 为 T2V 建立场景；镜2-5 以上一镜的尾帧作为 start_image 做 I2V 续写，
保证角色/场景/光影在镜头间连贯，最后用 ffmpeg xfade 转场拼成一支成片。

用法:
  python run_wan22_multishot.py              # 依次生成全部镜头并拼接
  python run_wan22_multishot.py --shot 3     # 只生成第 3 镜（断点续跑）
  python run_wan22_multishot.py --concat     # 只做拼接（镜头已生成完）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import requests

URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8200")
S = requests.Session()
S.trust_env = False  # 避免把 127.0.0.1 也走代理(127.0.0.1:7897)导致 502

OUT_DIR = "D:/ComfyUI/output"          # ComfyUI 输出目录
ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "outputs", "shots")
os.makedirs(WORK, exist_ok=True)

# ---- 生成参数 ----
# v4 = 480p 重制。v3(720p) 的实测问题：cu130 修好噪声后，画面仍是"平滑低频色场"，
#      模型在 720p 下收敛不足、出不了细节与物体。降到 480p 让模型更容易收敛，
#      并把 shift 同步改回 480p 官方值 5.0（8.0 是 720p 的值，混用会导致对比度/饱和度坍缩）。
VERSION = "v5"
WIDTH, HEIGHT = 832, 480
LENGTH = 49              # 480p 用 49 帧（对齐 run_wan22_test.py 已跑通的配置）
FPS = 16
STEPS = 30              # 官方 50 步；16GB 上为稳定与速度折中到 30
CFG = 6.0               # 官方 5.0；实测偏淡，提到 6.0 加强色彩与对比约束
SHIFT = 5.0              # 480p 官方值（720p 才是 8.0）
CRF = 28.0
BASE_SEED = 20260902

# Wan2.2 官方负向提示词。v1 的坑：negative 留空导致画面整体发灰、饱和度只剩 1/3。
NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，"
    "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，"
    "画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，"
    "杂乱的背景，三条腿，背景人很多，倒着走"
)
T = LENGTH / FPS                        # 单镜时长 5.0625s
XFADE = 0.5                             # 转场时长

# ---- 分镜：一场连续的戏（雨夜赛博都市，主角穿行、记忆闪现、走向发光门）----
SHOTS = [
    # 1 建立 · 全景
    "A wide shot of a rain-slicked neon street in a cyberpunk metropolis at night, "
    "towering holographic billboards, crowds with augmented-reality overlays, "
    "a lone figure in a long coat walking in the distance, "
    "cinematic, film grain, 35mm, dramatic lighting, slow camera movement",
    # 2 跟随 · 中景
    "The lone figure in a long coat walking down the neon alley, holographic advertisements "
    "flickering overhead, rain falling through volumetric light, fragments of memory glowing "
    "in the air, cinematic, film grain, 35mm, dramatic lighting, slow tracking shot",
    # 3 反应 · 特写
    "The figure stops and looks up, close-up of the face, eyes reflecting streams of flowing "
    "data and fragmented memories, neon reflections on wet skin, mysterious and emotional, "
    "cinematic, film grain, 35mm, dramatic lighting, shallow depth of field",
    # 4 发现 · 主观/推进
    "The figure's view looking down the street, a distant glowing gate of light appearing "
    "through the rain and fog, holographic signs and flying vehicles in the sky, "
    "cinematic, film grain, 35mm, dramatic lighting, slow camera push forward",
    # 5 结尾 · 拉远
    "The lone figure walking toward the glowing gate down the rain-slicked street, city "
    "skyline fading into fog behind, camera slowly pulling back, hope and mystery, "
    "cinematic, film grain, 35mm, dramatic lighting",
]


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FFMPEG = ffmpeg_exe()

# ---- 模型预检：D: 盘存在"目录视图间歇翻转"，模型文件可能暂时不可见 ----
# 在翻转窗口启动生成会加载到空权重，渲染几十分钟后产出纯灰噪点视频，
# 因此开跑前必须确认三个模型文件都可见且非空。
MODELS_DIR = os.path.join(os.path.dirname(OUT_DIR), "models")
MODEL_FILES = [
    os.path.join(MODELS_DIR, "diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors"),
    os.path.join(MODELS_DIR, "text_encoders", "umt5xxl-encoder-q5_k_m.gguf"),
    os.path.join(MODELS_DIR, "vae", "Wan2.2_VAE.safetensors"),
]


def check_models() -> bool:
    """全部模型文件可见且 >1MB 才算就绪；翻转窗口内会间歇性 False。"""
    missing = [p for p in MODEL_FILES
               if not (os.path.exists(p) and os.path.getsize(p) > 1_000_000)]
    for p in missing:
        print(f"[model] 不可见或为空: {p}")
    return not missing


def canary_check() -> bool:
    """4 步 I2V 金丝雀：检测 ComfyUI 是否加载了坏权重（输出纯灰场）。

    灰片根因 = 实例在 D: 盘翻转窗口加载模型 → 权重损坏并固化，
    直到进程重启。金丝雀用同实例跑一次 4 步 I2V 小生成，统计帧像素
    标准差：健康画面 std≈0.15+，坏权重灰场 std<0.04。
    返回 True=实例健康可跑，False=需重启 ComfyUI。
    """
    import requests
    import imageio_ffmpeg

    url = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
    start_img = os.path.join(ROOT, "outputs", "start_boat2.png")
    if not os.path.exists(start_img):
        print("[canary] 无起始图可测，跳过金丝雀（视为通过）")
        return True

    s = requests.Session()
    s.trust_env = False
    with open(start_img, "rb") as f:
        up = s.post(f"{url}/upload/image",
                    files={"image": ("start_boat2.png", f, "image/png")},
                    data={"overwrite": "true", "subfolder": "", "type": "input"},
                    timeout=60)
    up.raise_for_status()
    img_name = up.json()["name"]

    wf = {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": "Wan2.2-TI2V-5B-Q8_0.gguf"}},
        "2": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": "umt5xxl-encoder-q5_k_m.gguf", "type": "wan"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "Wan2.2_VAE.safetensors"}},
        "4": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["3", 0], "width": 320, "height": 192,
                         "length": 9, "batch_size": 1,
                         "start_image": ["11", 0]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": "a red sailboat on a calm lake at sunrise"}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": "worst quality"}},
        "7": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["1", 0], "shift": 5.0}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["4", 0], "seed": 42,
                         "steps": 4, "cfg": 6.0, "sampler_name": "euler",
                         "scheduler": "beta", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "canary_check"}},
        "11": {"class_type": "LoadImage", "inputs": {"image": img_name}},
    }
    pid = s.post(f"{url}/prompt", json={"prompt": wf}, timeout=60).json()["prompt_id"]
    for _ in range(200):
        time.sleep(2)
        h = s.get(f"{url}/history/{pid}", timeout=30).json()
        if pid not in h:
            continue
        st = h[pid].get("status", {})
        if st.get("status_str") != "success":
            print(f"[canary] 工作流失败: {str(st)[:200]}")
            return False
        img = h[pid]["outputs"]["10"]["images"][0]
        src = os.path.join(OUT_DIR, img["filename"])
        # 等延迟可见恢复后用 PIL 统计像素标准差
        import time as _t
        deadline = _t.time() + 60
        while _t.time() < deadline and not os.path.exists(src):
            _t.sleep(1)
        try:
            from PIL import Image
            import numpy as np
            img_obj = Image.open(src).convert("L")
            std = float(np.asarray(img_obj, dtype=np.float64).std())
            print(f"[canary] 帧像素 std={std:.4f} "
                  f"({'健康' if std >= 0.04 else '灰场(坏权重)'})")
            return std >= 0.04
        except Exception as e:
            print(f"[canary] 帧分析失败: {e}")
            return False
    print("[canary] 超时")
    return False


def build_workflow(idx: int, start_image: str | None = None) -> dict:
    """构造单镜工作流；start_image 非空时走 I2V（接续上一镜尾帧）。"""
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors",
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": "umt5xxl-encoder-q5_k_m.gguf", "type": "wan"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "Wan2.2_VAE.safetensors"}},
        "4": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["3", 0], "width": WIDTH, "height": HEIGHT,
                         "length": LENGTH, "batch_size": 1}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": SHOTS[idx - 1]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": NEGATIVE}},
        "7": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["1", 0], "shift": SHIFT}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["4", 0], "seed": BASE_SEED + idx,
                         "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
                         "scheduler": "beta", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveWEBM",
               "inputs": {"images": ["9", 0],
                          "filename_prefix": f"wan22{VERSION}_shot{idx}",
                          "codec": "vp9", "fps": float(FPS), "crf": CRF}},
    }
    if start_image:
        wf["11"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        wf["4"]["inputs"]["start_image"] = ["11", 0]
    return wf


def extract_last_frame(src: str, dst: str) -> bool:
    """用 ffmpeg 抽视频最后一帧，作为下一镜的 I2V 起始图。"""
    cmd = [FFMPEG, "-y", "-sseof", "-0.05", "-i", src,
           "-update", "1", "-frames:v", "1", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        print(f"  [frame] {os.path.basename(dst)}")
        return True
    # 兜底：按帧号抽最后一帧
    cmd = [FFMPEG, "-y", "-i", src, "-vf",
           f"select=eq(n\\,{LENGTH - 1})", "-update", "1", "-frames:v", "1", dst]
    subprocess.run(cmd, capture_output=True, text=True)
    ok = os.path.exists(dst) and os.path.getsize(dst) > 0
    print(f"  [frame] {os.path.basename(dst)} fallback={not ok}")
    if not ok:
        print("  [err]", (r.stderr or "")[-500:])
    return ok


def upload_image(path: str) -> str:
    """上传本地图片到 ComfyUI input 目录，返回文件名。"""
    with open(path, "rb") as f:
        files = {"image": (os.path.basename(path), f, "image/png")}
        data = {"overwrite": "true", "subfolder": "", "type": "input"}
        r = S.post(f"{URL}/upload/image", files=files, data=data, timeout=180)
    r.raise_for_status()
    name = r.json()["name"]
    print(f"  [upload] {name}")
    return name


def find_shot_file(prefix: str) -> str | None:
    """按 prefix 找已生成的 webm：优先项目 WORK 副本，其次 ComfyUI output 目录。
    兼容 ComfyUI 的 _0000N_ 计数漂移，多命中取最新。"""
    import glob as _glob
    for d in (WORK, OUT_DIR):
        hits = sorted(_glob.glob(os.path.join(d, f"{prefix}_*.webm")),
                      key=os.path.getmtime)
        if hits:
            return hits[-1]
    return None


def run_shot(idx: int) -> str | None:
    prefix = f"wan22{VERSION}_shot{idx}"
    webm = find_shot_file(prefix)
    if webm:
        print(f"[shot {idx}] 已存在，跳过 -> {webm}")
        return webm

    # Wan2.2 TI2V-5B 是 I2V 模型：无 start_image 的纯 T2V 会退化成灰场
    # （2026-09-05 金丝雀对照实证），因此每一镜都必须有起始帧。
    start = None
    first_frame = os.path.join(ROOT, "outputs", "ltx23_film_f01.png")
    if idx == 1:
        if os.path.exists(first_frame):
            print(f"[shot 1] 使用概念帧作为起始图: {first_frame}")
            start = upload_image(first_frame)
        else:
            print("[fatal] 镜 1 缺少起始帧（纯 T2V 会产出灰片）。"
                  f"请放置起始图于: {first_frame}")
            return None
    if idx > 1:
        prev = find_shot_file(f"wan22{VERSION}_shot{idx - 1}")
        if not prev:
            print(f"[fatal] 缺少上一镜 wan22{VERSION}_shot{idx - 1}")
            return None
        last = os.path.join(WORK, f"{VERSION}_shot{idx - 1}_last.png")
        if not os.path.exists(last):
            extract_last_frame(prev, last)
        start = upload_image(last)

    wf = build_workflow(idx, start)
    r = S.post(f"{URL}/prompt", json={"prompt": wf, "client_id": f"shot{idx}"}, timeout=180)
    r.raise_for_status()
    pid = r.json()["prompt_id"]
    mode = "I2V" if start else "T2V"
    print(f"[shot {idx}/{len(SHOTS)}] {mode} submit pid={pid} {WIDTH}x{HEIGHT}x{LENGTH}")

    for i in range(1200):  # 单镜最多 60 分钟
        time.sleep(3)
        h = S.get(f"{URL}/history/{pid}", timeout=30).json()
        if pid in h:
            rec = h[pid]
            out = rec.get("outputs", {})
            if "10" in out:
                info = out["10"]["images"][0]
                fn = info["filename"]
                # 主副本落项目 WORK 目录。D:\ComfyUI\output 存在"新文件短暂不可读"的
                # 文件系统视图问题（同 seed 灰片排查的教训），故统一走 /view API
                # 从 ComfyUI 进程句柄拉取字节，与本地目录状态解耦。
                webm = os.path.join(WORK, fn)
                r2 = S.get(f"{URL}/view",
                           params={"filename": fn, "subfolder": info.get("subfolder", ""),
                                   "type": "output"}, timeout=600)
                if r2.status_code != 200 or not r2.content:
                    print(f"[shot {idx}] /view 拉取失败: HTTP {r2.status_code}")
                    return None
                with open(webm, "wb") as f:
                    f.write(r2.content)
                print(f"[shot {idx}] done -> {webm} "
                      f"({round(os.path.getsize(webm) / 2 ** 20, 2)}MB)")
                extract_last_frame(webm, os.path.join(WORK, f"{VERSION}_shot{idx}_last.png"))
                return webm
            st = rec.get("status", {})
            print(f"[shot {idx}] 失败: {json.dumps(st)[:800]}")
            return None
        if i % 20 == 0:
            q = S.get(f"{URL}/queue", timeout=30).json()
            print(f"  [wait] {i * 3}s running={len(q.get('queue_running', []))}")
    print(f"[shot {idx}] timeout, 查看 D:/ComfyUI/comfy_run.log")
    return None


def concat() -> str | None:
    srcs = [find_shot_file(f"wan22{VERSION}_shot{i}")
            for i in range(1, len(SHOTS) + 1)]
    miss = [f"wan22{VERSION}_shot{i}" for i, p in enumerate(srcs, 1) if not p]
    if miss:
        print("[fatal] 缺少镜头:", miss)
        return None

    out = os.path.join(ROOT, "outputs", f"scifi_multishot_{VERSION}.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    parts, labels = [], []
    for i, p in enumerate(srcs):
        parts += ["-i", p]
        labels.append(f"[{i}:v]fps={FPS},format=yuv420p,setsar=1,scale={WIDTH}:{HEIGHT}[v{i}];")
    chain = "".join(labels)
    chain += f"[v0][v1]xfade=transition=fade:duration={XFADE}:offset={T - XFADE:.4f}[x1];"
    for k in range(2, len(srcs)):
        off = k * T - k * XFADE
        chain += f"[x{k - 1}][v{k}]xfade=transition=fade:duration={XFADE}:offset={off:.4f}[x{k}];"
    chain = chain.rstrip(";")

    cmd = [FFMPEG, "-y", *parts, "-filter_complex", chain,
           "-map", f"[x{len(srcs) - 1}]", "-c:v", "libx264", "-crf", "18",
           "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS), out]
    print("[concat] 拼接", len(srcs), "镜 ->", out)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        total = len(srcs) * T - (len(srcs) - 1) * XFADE
        print(f"[OK] 成片 {out}  {round(os.path.getsize(out) / 2 ** 20, 2)}MB  "
              f"{WIDTH}x{HEIGHT} 约{total:.1f}s")
        return out
    print("[err] ffmpeg 失败:", (r.stderr or "")[-1500:])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=int, default=0, help="只生成第 N 镜；0=全部")
    ap.add_argument("--concat", action="store_true", help="只做拼接")
    a = ap.parse_args()

    if a.concat:
        concat()
        return
    if not check_models():
        print("[fatal] 模型文件不可见（D: 盘视图翻转窗口），避免渲染 30 分钟出灰片，已中止。"
              "稍后重试，或先修复 D: 盘存储。")
        sys.exit(1)
    print("[canary] 验证 ComfyUI 实例权重健康度...")
    if not canary_check():
        print("[fatal] ComfyUI 实例加载了坏权重（灰场输出）。"
              "请重启 ComfyUI 后重跑；若重启后仍灰，检查 D: 盘模型文件是否被清理工具移除。")
        sys.exit(1)
    if a.shot:
        run_shot(a.shot)
        return

    ok = []
    for i in range(1, len(SHOTS) + 1):
        p = run_shot(i)
        if not p:
            print(f"[abort] 第 {i} 镜失败，已生成: {ok}")
            return
        ok.append(p)
        if i < len(SHOTS):
            time.sleep(2)
    concat()


if __name__ == "__main__":
    main()
