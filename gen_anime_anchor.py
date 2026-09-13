#!/usr/bin/env python
"""生成「2D 动漫风」锚定资产（ComfyUI + 动漫 checkpoint，替代 outputs/anchor 的写实图）。

为什么不用 outputs/anchor 的写实图：H3 的画风由**参考图/首帧**决定、文本说了不算。
写实锚定图会把成片锁成真人脸；故无真人脸要求下必须换成动漫参考图。

产物（outputs/anchor_anime/）：
  mira_front / mira_side / mira_back  主角三视图（中性背景）
  mira_face                           脸部特写（供 shot4/12/14 首帧锚定）
  scene_*                             场景空镜（与 outputs/anchor 同名，便于替换 anchor_map）

用法:
  python gen_anime_anchor.py                       # 全部生成
  python gen_anime_anchor.py --only mira_front,mira_face
  python gen_anime_anchor.py --ckpt <文件名> --res 768x768
"""
from __future__ import annotations
import argparse
import os
import shutil

import run_series as rs

ROOT = rs.ROOT
OUT = os.path.join(ROOT, "outputs", "anchor_anime")

DEFAULT_CKPT = "Counterfeit-V3.0_fix_fp16.safetensors"

# Counterfeit 系模型吃「质量标签 + 自然语言」混合；质量标签在前权重更高
QUALITY = ("masterpiece, best quality, official art, unity 8k wallpaper, "
           "ultra detailed, anime coloring, cel shading, flat color, clean line art")
NEGATIVE = ("photo, photograph, photorealistic, realistic, 3d render, octane render, "
            "realistic skin texture, live action, lowres, bad anatomy, bad hands, "
            "blurry, jpeg artifacts, watermark, text, extra limbs, deformed face")

MIRA = ("1girl, short dark bob hair, dark worn trench coat, "
        "subtle cybernetic ports on her knuckles, determined expression")

# (文件名, 提示词, 是否用方形分辨率)
SHOTS = [
    ("mira_front",
     f"{QUALITY}, {MIRA}, front view, full body, neutral standing pose facing camera, "
     f"plain soft neutral studio backdrop, even lighting", True),
    ("mira_side",
     f"{QUALITY}, {MIRA}, side profile view, full body, standing in profile, "
     f"plain soft neutral studio backdrop, even lighting", True),
    ("mira_back",
     f"{QUALITY}, {MIRA}, back view, full body, seen from behind, "
     f"plain soft neutral studio backdrop, even lighting", True),
    ("mira_face",
     f"{QUALITY}, {MIRA}, close-up portrait of face, skeptical expression, "
     f"looking slightly off camera, rain droplets on cheek, "
     f"neon pink and cyan reflections, blurred rainy cyberpunk city bokeh at night", True),
    ("scene_neon_street",
     f"{QUALITY}, wide establishing shot of a rain-soaked neon megacity street at night, "
     f"magenta and cyan signs reflecting on wet asphalt, no people", False),
    ("scene_shop_exterior",
     f"{QUALITY}, exterior of a small corner memory shop at night, neon sign in the rain, "
     f"rows of glowing small memory vials in the window, no people", False),
    ("scene_shop_interior",
     f"{QUALITY}, interior of a dim memory shop, shelves of glowing memory vials, "
     f"a counter with brass scales, cold blue light, no people", False),
    ("scene_reading_chair",
     f"{QUALITY}, a memory reading chair in a dim room, cold blue scanning light, "
     f"no people", False),
    ("scene_meadow",
     f"{QUALITY}, flashback of a sunlit meadow, tall grass moving in warm wind, "
     f"golden hour, no people", False),
    ("scene_chip",
     f"{QUALITY}, close-up of a small blank black memory chip resting in an open palm, "
     f"cold blue rim light", False),
]


def build_workflow(ckpt: str, prompt: str, negative: str, w: int, h: int,
                   seed: int, steps: int, cfg: float) -> dict:
    """SD1.5 txt2img 标准 API 工作流。"""
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": "dpmpp_2m",
                         "scheduler": "karras", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "anime_anchor"}},
    }


def gen_shot_keyframes(client, ep: int, ckpt: str, w: int, h: int,
                       steps: int, cfg: float, seed: int) -> None:
    """按 series_shots.json 的分镜，逐镜生成一张动漫关键帧。

    逐镜首帧锚定是「无真人脸」最强的信号：实测 shot4 用动漫脸部图作首帧 →
    真实人脸检出率 100%→0%。场景锚定图里没有人物，动漫信号弱（全片仍 32%），
    故改为逐镜出图。
    """
    import json
    data = json.load(open(rs.SHOTS_FILE, encoding="utf-8"))
    prompts = data.get(f"ep{ep}") or []
    d = os.path.join(OUT, "shots")
    os.makedirs(d, exist_ok=True)
    amap = {}
    print(f"[cfg] ep{ep} 共 {len(prompts)} 镜 -> {d}", flush=True)
    for i, base in enumerate(prompts):
        idx = i + 1
        name = f"ep{ep}_shot{idx:02d}"
        dst = os.path.join(d, f"{name}.png")
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            amap[str(idx)] = os.path.relpath(dst, ROOT).replace("\\", "/")
            continue
        prompt = f"{QUALITY}, {base}, {rs.load_json(rs.BIBLE_FILE).get('style_anchor','')}"
        wf = build_workflow(ckpt, prompt, NEGATIVE, w, h, seed + i * 137, steps, cfg)
        try:
            paths = client.run_workflow(wf, d, timeout=600)
        except Exception as e:
            print(f"  [err] {name}: {e}")
            continue
        if not paths:
            print(f"  [err] {name}: 无产出")
            continue
        shutil.move(paths[0], dst)
        amap[str(idx)] = os.path.relpath(dst, ROOT).replace("\\", "/")
        print(f"  [ok] {name} ({os.path.getsize(dst) / 1024:.0f}KB)", flush=True)
    if amap:
        mp = os.path.join(OUT, f"ep{ep}_anchor_map_shots.json")
        json.dump(amap, open(mp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[ok] 锚定映射 -> {mp}（{len(amap)} 条）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="",
                    help="按 series_shots.json 逐镜生成动漫关键帧，如 ep1")
    ap.add_argument("--res-shots", default="1024x576", help="逐镜关键帧分辨率")
    ap.add_argument("--ckpt", default=DEFAULT_CKPT)
    ap.add_argument("--res", default="768x768", help="方形分辨率（三视图/特写）")
    ap.add_argument("--res-scene", default="768x448", help="宽屏分辨率（场景图）")
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--cfg", type=float, default=7.0)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--only", default="", help="只跑指定图，逗号分隔")
    a = ap.parse_args()

    sw, sh = (int(x) for x in a.res.lower().split("x"))
    vw, vh = (int(x) for x in a.res_scene.lower().split("x"))
    os.makedirs(OUT, exist_ok=True)

    from tools.comfyui_client import ComfyUIClient
    client = ComfyUIClient("http://127.0.0.1:8188", timeout=600)
    if not client.is_ready():
        print("[err] ComfyUI 未就绪（8188）")
        raise SystemExit(1)

    if a.shots:
        ep = int(a.shots.lower().replace("ep", ""))
        gw, gh = (int(x) for x in a.res_shots.lower().split("x"))
        gen_shot_keyframes(client, ep, a.ckpt, gw, gh, a.steps, a.cfg, a.seed)
        return

    want = [s.strip() for s in a.only.split(",") if s.strip()]
    todo = [(n, p, sq) for n, p, sq in SHOTS if not want or n in want]
    print(f"[cfg] ckpt={a.ckpt} 方形 {sw}x{sh}  场景 {vw}x{vh}  共 {len(todo)} 张 -> {OUT}",
          flush=True)

    for i, (name, prompt, square) in enumerate(todo):
        w, h = (sw, sh) if square else (vw, vh)
        dst = os.path.join(OUT, f"{name}.png")
        wf = build_workflow(a.ckpt, prompt, NEGATIVE, w, h,
                            a.seed + i * 137, a.steps, a.cfg)
        try:
            paths = client.run_workflow(wf, OUT, timeout=600)
        except Exception as e:
            print(f"  [err] {name}: {e}")
            continue
        if not paths:
            print(f"  [err] {name}: 无产出")
            continue
        shutil.move(paths[0], dst)
        print(f"  [ok] {name} <- {os.path.basename(paths[0])} "
              f"({os.path.getsize(dst) / 1024:.0f}KB)", flush=True)


if __name__ == "__main__":
    main()
