#!/usr/bin/env python
# 按项目《看见未来之前》科幻比赛脚本，用 Wan2.2-TI2V-5B GGUF 出更高分辨率/更长的视频。
# 主题: 近未来赛博都市科幻, 探讨记忆与身份; 风格来自 config.yaml 的 style 字段。
# 用法: python run_wan22_scifi.py
import json
import time
import requests

URL = "http://127.0.0.1:8188"
S = requests.Session()
S.trust_env = True

# 赛博都市科幻英文提示词（含 config.yaml 的 style: cinematic, film grain, 35mm, dramatic lighting, slow camera movement）
PROMPT = (
    "A neon-lit cyberpunk metropolis at night in the near future, towering holographic "
    "billboards reflecting on rain-slick streets, a lone figure walking through the crowded "
    "district while fragments of memory flicker as augmented-reality overlays, "
    "cinematic, film grain, 35mm, dramatic lighting, slow camera movement"
)

# Wan2.2 TI2V-5B 高分辨率(720p) + 更长(length 81 ≈ 5s @16fps)
WF = {
    "1": {"class_type": "UnetLoaderGGUF",
          "inputs": {"unet_name": "Wan2.2-TI2V-5B-Q8_0.gguf"}},
    "2": {"class_type": "CLIPLoaderGGUF",
          "inputs": {"clip_name": "umt5xxl-encoder-q5_k_m.gguf", "type": "wan"}},
    "3": {"class_type": "VAELoader",
          "inputs": {"vae_name": "Wan2.2_VAE.safetensors"}},
    "4": {"class_type": "Wan22ImageToVideoLatent",
          "inputs": {"vae": ["3", 0], "width": 1280, "height": 704,
                     "length": 81, "batch_size": 1}},
    "5": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["2", 0], "text": PROMPT}},
    "6": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["2", 0], "text": ""}},
    "7": {"class_type": "ModelSamplingSD3",
          "inputs": {"model": ["1", 0], "shift": 5.0}},
    "8": {"class_type": "KSampler",
          "inputs": {"model": ["7", 0], "positive": ["5", 0], "negative": ["6", 0],
                     "latent_image": ["4", 0], "seed": 20260902, "steps": 40,
                     "cfg": 5.0, "sampler_name": "euler", "scheduler": "beta",
                     "denoise": 1.0}},
    "9": {"class_type": "VAEDecode",
          "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveWEBM",
           "inputs": {"images": ["9", 0], "filename_prefix": "wan22_scifi_contest",
                      "codec": "vp9", "fps": 16.0, "crf": 28.0}},
}


def main():
    # 先探活 ComfyUI
    try:
        S.get(f"{URL}/", timeout=5).raise_for_status()
    except Exception as e:
        print(f"[fatal] ComfyUI 不可达 ({e})，请先运行 python launch_comfy.py")
        return
    r = S.post(f"{URL}/prompt", json={"prompt": WF, "client_id": "wan22scifi"},
               timeout=120)
    r.raise_for_status()
    pid = r.json()["prompt_id"]
    print(f"[submit] prompt_id={pid}  resolution=1280x704  length=81 (~5s@16fps)")
    for i in range(900):  # 最多 45 分钟
        time.sleep(3)
        h = S.get(f"{URL}/history/{pid}", timeout=30).json()
        if pid in h:
            out = h[pid].get("outputs", {})
            if "10" in out:
                for im in out["10"].get("images", []):
                    print("[done]", im)
                print("[OK] 科幻比赛视频生成完成 -> D:/ComfyUI/output")
                return
            print("[warn] 节点10无输出:", json.dumps(out)[:600])
            return
        status = S.get(f"{URL}/queue", timeout=30).json()
        if i % 10 == 0:
            print(f"[wait] {i*3}s running={len(status.get('queue_running', []))} "
                  f"pending={len(status.get('queue_pending', []))}")
    print("[timeout] 未完成, 查看 D:/ComfyUI/comfy_run.log")


if __name__ == "__main__":
    main()
