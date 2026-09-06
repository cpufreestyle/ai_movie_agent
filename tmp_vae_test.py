#!/usr/bin/env python
"""临时诊断：VAE 自洽测试。高饱和图 -> VAEEncode -> VAEDecode，看重建后是否褪色。

若重建图严重褪色/亮度被压缩 => VAE 权重有问题（与 UNet / 文本编码器 / 采样参数无关）。
"""
import os
import time

import numpy as np
import requests
from PIL import Image, ImageDraw

URL = "http://127.0.0.1:8188"
S = requests.Session()
S.trust_env = True
OUT = "D:/ComfyUI/output"
W, H = 1280, 704

# 1) 造一张高饱和测试图：横向彩色渐变 + 纯色块 + 黑白灰阶
img = Image.new("RGB", (W, H))
d = ImageDraw.Draw(img)
for x in range(W):  # 高饱和 HSV 全色相渐变
    hue = int(x / W * 255)
    d.line([(x, 0), (x, H // 2)], fill=tuple(
        int(c) for c in Image.new("RGB", (1, 1)).convert("RGB").getpixel((0, 0))) if False else
        tuple(int(v) for v in np.array(Image.new("HSV", (1, 1), (hue, 255, 255)).convert("RGB")).reshape(3)))
colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
          (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 0, 255)]
bw = W // len(colors)
for i, c in enumerate(colors):
    d.rectangle([i * bw, H // 2, (i + 1) * bw - 1, H * 3 // 4], fill=c)
for i in range(8):  # 灰阶 0..255
    d.rectangle([i * bw, H * 3 // 4, (i + 1) * bw - 1, H - 1],
                fill=(i * 36, i * 36, i * 36))
src = os.path.abspath("_vae_src.png")
img.save(src)

hsv = np.array(img.convert("HSV")).astype(float)
rgb = np.array(img).astype(float)
y = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
print(f"原图 : sat={hsv[:, :, 1].mean():.1f}  Y min/median/max="
      f"{y.min():.0f}/{np.median(y):.0f}/{y.max():.0f}")

# 2) 上传
with open(src, "rb") as f:
    r = S.post(f"{URL}/upload/image",
               files={"image": ("_vae_src.png", f, "image/png")},
               data={"overwrite": "true", "subfolder": "", "type": "input"}, timeout=180)
r.raise_for_status()
name = r.json()["name"]

# 3) Encode -> Decode 往返
WF = {
    "1": {"class_type": "VAELoader",
          "inputs": {"vae_name": "Wan2.2_VAE.safetensors"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": name}},
    "3": {"class_type": "VAEEncode",
          "inputs": {"pixels": ["2", 0], "vae": ["1", 0]}},
    "4": {"class_type": "VAEDecode",
          "inputs": {"samples": ["3", 0], "vae": ["1", 0]}},
    "5": {"class_type": "SaveImage",
          "inputs": {"images": ["4", 0], "filename_prefix": "vae_roundtrip"}},
}
r = S.post(f"{URL}/prompt", json={"prompt": WF, "client_id": "vaetest"}, timeout=120)
r.raise_for_status()
pid = r.json()["prompt_id"]

for _ in range(45):          # 纯编解码往返，通常几秒完成，最多等 45s
    time.sleep(1)
    h = S.get(f"{URL}/history/{pid}", timeout=30).json()
    if pid in h:
        out = h[pid].get("outputs", {})
        if "5" in out:
            p = os.path.join(OUT, out["5"]["images"][0]["filename"])
            rec = np.array(Image.open(p).convert("RGB")).astype(float)
            rhsv = np.array(Image.open(p).convert("HSV")).astype(float)
            ry = 0.299 * rec[:, :, 0] + 0.587 * rec[:, :, 1] + 0.114 * rec[:, :, 2]
            print(f"重建 : sat={rhsv[:, :, 1].mean():.1f}  Y min/median/max="
                  f"{ry.min():.0f}/{np.median(ry):.0f}/{ry.max():.0f}")
            print(f"色彩误差 MAE={np.abs(rec - rgb).mean():.1f}  "
                  f"亮度范围压缩比={(ry.max() - ry.min()) / (y.max() - y.min()):.2f}")
            print("判定: 重建饱和/亮度接近原图 => VAE 正常，问题在 UNet/量化或采样；"
                  "严重褪色 => VAE 权重有问题，换官方 wan_2.1_vae")
        else:
            print("failed:", str(out)[:500])
        break
else:
    print("timeout")

os.remove(src)
