#!/usr/bin/env python
# Wan2.2-TI2V-5B GGUF 真·图生视频(I2V) 验证 —— 必须喂起始图。
# 关键：Wan2.2-TI2V 是图生视频模型，不传 start_image 的纯 T2V 用法会退化成平滑色调场(看不清)。
#
# 用法:
#   python run_wan22_test.py                          # 纯 T2V(退化/对比, 勿用)
#   python run_wan22_test.py outputs/start_boat2.png  # 真 I2V(清晰)
import json
import time
import sys
import os
import requests

URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8200")
S = requests.Session()
S.trust_env = False  # 127.0.0.1 不走系统代理，避免 7897->502

PROMPT = ("A small red sailboat with a white sail drifting on a calm lake at sunrise, "
          "golden sunlight reflecting on the water, soft cinematic light, gentle ripples, "
          "photorealistic, high detail, smooth motion")
# 负向提示词必须填：空 negative 会让 Wan2.2 画面褪色/发灰(饱和仅~34)
NEGATIVE = ("blurry, low resolution, low quality, deformed, watermark, text, subtitle, logo, "
            "overexposed, underexposed, low contrast, washed out, desaturated, dull colors, "
            "grayish, static, no detail, noise, compression artifacts, distorted, warped")

# 720p 真 I2V 配置：shift=8 是 720p 官方值(480p 才用 5)；负向词必填
W, H, LENGTH, STEPS, SHIFT, CFG = 1280, 704, 49, 30, 8.0, 6.0

wf = {
    "1": {"class_type": "UnetLoaderGGUF",
          "inputs": {"unet_name": "Wan2.2-TI2V-5B-Q8_0.gguf"}},
    "2": {"class_type": "CLIPLoaderGGUF",
          "inputs": {"clip_name": "umt5xxl-encoder-q5_k_m.gguf", "type": "wan"}},
    "3": {"class_type": "VAELoader",
          "inputs": {"vae_name": "Wan2.2_VAE.safetensors"}},
    "4": {"class_type": "Wan22ImageToVideoLatent",
          "inputs": {"vae": ["3", 0], "width": W, "height": H,
                     "length": LENGTH, "batch_size": 1}},
    "5": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["2", 0], "text": PROMPT}},
    "6": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["2", 0], "text": NEGATIVE}},
    "7": {"class_type": "ModelSamplingSD3",
          "inputs": {"model": ["1", 0], "shift": SHIFT}},
    "8": {"class_type": "KSampler",
          "inputs": {"model": ["7", 0], "positive": ["5", 0], "negative": ["6", 0],
                     "latent_image": ["4", 0], "seed": 12345, "steps": STEPS,
                     "cfg": CFG, "sampler_name": "euler", "scheduler": "beta",
                     "denoise": 1.0}},
    "9": {"class_type": "VAEDecode",
          "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveWEBM",
           "inputs": {"images": ["9", 0], "filename_prefix": "wan22_i2v",
                      "codec": "vp9", "fps": 24.0, "crf": 28.0}},
}


def upload_image(path: str) -> str:
    with open(path, "rb") as f:
        files = {"image": (os.path.basename(path), f, "image/png")}
        data = {"overwrite": "true", "subfolder": "", "type": "input"}
        r = S.post(f"{URL}/upload/image", files=files, data=data, timeout=180)
    r.raise_for_status()
    return r.json()["name"]


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else None
    if start:
        if not os.path.exists(start):
            print(f"[ERR] 起始图不存在: {start}")
            return
        name = upload_image(start)
        wf["11"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf["4"]["inputs"]["start_image"] = ["11", 0]
        print(f"[i2v] 起始图: {start} -> {name}  ({W}x{H}, steps={STEPS}, shift={SHIFT})")
    else:
        print("[warn] 未传起始图：TI2V 纯 T2V 会退化成色调场(看不清)，仅用于对比。")

    r = S.post(f"{URL}/prompt", json={"prompt": wf, "client_id": "wan22test"},
               timeout=120)
    r.raise_for_status()
    pid = r.json()["prompt_id"]
    print(f"[submit] prompt_id={pid}")
    for i in range(800):
        time.sleep(3)
        h = S.get(f"{URL}/history/{pid}", timeout=30).json()
        if pid in h:
            out = h[pid].get("outputs", {})
            if "10" in out:
                for im in out["10"].get("images", []):
                    print("  ", im)
                print(f"[OK] 完成；视频在 ComfyUI/output (wan22_i2v_*.webm)")
                return
            print("[warn] 节点10无输出:", json.dumps(out)[:500])
            return
        if i % 10 == 0:
            st = S.get(f"{URL}/queue", timeout=30).json()
            print(f"[wait] {i*3}s running={len(st.get('queue_running', []))} "
                  f"pending={len(st.get('queue_pending', []))}")
    print("[timeout] 40 分钟内未完成, 查看 ComfyUI 日志")


if __name__ == "__main__":
    main()
