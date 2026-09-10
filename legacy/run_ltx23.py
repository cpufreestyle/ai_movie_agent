#!/usr/bin/env python
"""用 LTX-2.3 (GGUF, 16GB 可跑) 生成科幻比赛视频。

权重放在 E: 盘（D: 盘放不下），通过 D:/ComfyUI/extra_model_paths.yaml 映射。
流程基于 ComfyUI 官方 LTX-2.3 蓝图的核心节点，手工改成单阶段（去掉 spatial upscale
与第二段 refiner，16GB 显存下更稳）。

用法:
  python run_ltx23.py                      # 默认 768x512, 97帧, 8步(distilled)
  python run_ltx23.py --w 960 --h 544 --len 97 --steps 8
"""
from __future__ import annotations

import argparse
import json
import os
import time

import requests

PORT = int(os.environ.get("COMFY_PORT", 8200))
URL = f"http://127.0.0.1:{PORT}"
S = requests.Session()
S.trust_env = False  # 127.0.0.1 不走系统代理，避免 7897->502
OUT_DIR = "D:/ComfyUI/output"

UNET = "ltx-2.3-22b-distilled-1.1-Q3_K_M.gguf"
VIDEO_VAE = "ltx-2.3-video-vae.safetensors"
AUDIO_VAE = "ltx-2.3-dev-audio-vae.safetensors"
TEXT_ENC = "gemma_3_12B_it_fp4_mixed.safetensors"
CONNECTORS = "ltx-2.3-embeddings-connectors.safetensors"

PROMPT = (
    "A wide shot of a rain-slicked neon street in a cyberpunk metropolis at night, "
    "towering holographic billboards, crowds with augmented-reality overlays, "
    "a lone figure in a long coat walking in the distance, cinematic, film grain, "
    "35mm, dramatic lighting, slow camera movement"
)
NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly, blurry, low quality"


def build_wf(prompt, width, height, length, steps, seed, fps, cfg):
    return {
        # --- 模型加载 ---
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "3": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": AUDIO_VAE}},
        "4": {"class_type": "LTXAVTextEncoderLoader",
              "inputs": {"text_encoder": TEXT_ENC, "ckpt_name": CONNECTORS,
                         "device": "default"}},
        # --- 文本编码 ---
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["4", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["4", 0], "text": NEGATIVE}},
        # --- latent：视频 + 音频拼接 ---
        "7": {"class_type": "EmptyLTXVLatentVideo",
              "inputs": {"width": width, "height": height, "length": length,
                         "batch_size": 1}},
        "8": {"class_type": "LTXVEmptyLatentAudio",
              "inputs": {"audio_vae": ["3", 0], "frames_number": length,
                         "frame_rate": fps, "batch_size": 1}},
        "9": {"class_type": "LTXVConcatAVLatent",
              "inputs": {"video_latent": ["7", 0], "audio_latent": ["8", 0]}},
        # --- 条件与采样 ---
        "10": {"class_type": "LTXVConditioning",
               "inputs": {"positive": ["5", 0], "negative": ["6", 0],
                          "frame_rate": fps}},
        "11": {"class_type": "CFGGuider",
               "inputs": {"model": ["1", 0], "positive": ["10", 0],
                          "negative": ["10", 1], "cfg": cfg}},
        "12": {"class_type": "KSamplerSelect",
               "inputs": {"sampler_name": "euler_cfg_pp"}},
        "13": {"class_type": "LTXVScheduler",
               "inputs": {"steps": steps, "max_shift": 2.05, "base_shift": 0.95,
                          "stretch": True, "terminal": 0.1}},
        "14": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "15": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["14", 0], "guider": ["11", 0],
                          "sampler": ["12", 0], "sigmas": ["13", 0],
                          "latent_image": ["9", 0]}},
        # --- 分离解码 ---
        "16": {"class_type": "LTXVSeparateAVLatent",
               "inputs": {"av_latent": ["15", 0]}},
        "17": {"class_type": "VAEDecodeTiled",
               "inputs": {"samples": ["16", 0], "vae": ["2", 0],
                          "tile_size": 512, "overlap": 64,
                          "temporal_size": 32, "temporal_overlap": 4}},
        "18": {"class_type": "LTXVAudioVAEDecode",
               "inputs": {"samples": ["16", 1], "audio_vae": ["3", 0]}},
        # --- 合成与保存 ---
        "19": {"class_type": "CreateVideo",
               "inputs": {"images": ["17", 0], "audio": ["18", 0], "fps": fps,
                          "bit_depth": "auto", "color_space": "sRGB"}},
        "20": {"class_type": "SaveVideo",
               "inputs": {"video": ["19", 0], "filename_prefix": "ltx23/contest",
                          "format": "auto", "codec": "auto"}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8200)
    ap.add_argument("--w", type=int, default=768)
    ap.add_argument("--h", type=int, default=512)
    ap.add_argument("--len", type=int, default=97)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--prompt", type=str, default=PROMPT)
    a = ap.parse_args()
    global URL
    URL = f"http://127.0.0.1:{a.port}"

    try:
        S.get(f"{URL}/", timeout=8).raise_for_status()
    except Exception as e:
        print(f"[fatal] ComfyUI 不可达: {type(e).__name__}")
        return

    wf = build_wf(a.prompt, a.w, a.h, a.len, a.steps, a.seed, a.fps, a.cfg)
    r = S.post(f"{URL}/prompt", json={"prompt": wf, "client_id": "ltx23"}, timeout=180)
    body = r.text[:800]
    if r.status_code != 200:
        print("[submit failed]", r.status_code, body)
        return
    j = r.json()
    if "prompt_id" not in j:
        print("[validation error]", json.dumps(j, ensure_ascii=False)[:1500])
        return
    pid = j["prompt_id"]
    print(f"[submit] {pid}  {a.w}x{a.h} x{a.len}@{a.fps}fps steps={a.steps} cfg={a.cfg}")

    for i in range(1500):
        time.sleep(3)
        h = S.get(f"{URL}/history/{pid}", timeout=30).json()
        if pid in h:
            rec = h[pid]
            out = rec.get("outputs", {})
            if "20" in out:
                for item in out["20"].get("images", []) or []:
                    print("[done]", item)
                print(f"[OK] -> {OUT_DIR}/ltx23/")
                return
            st = rec.get("status", {})
            print("[failed]", json.dumps(st, ensure_ascii=False)[:1200])
            return
        if i % 20 == 0:
            q = S.get(f"{URL}/queue", timeout=30).json()
            print(f"  [wait] {i * 3}s running={len(q.get('queue_running', []))}")
    print("[timeout] 查看 D:/ComfyUI/comfy_run.log")


if __name__ == "__main__":
    main()
