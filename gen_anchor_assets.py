#!/usr/bin/env python
"""生成主角 Mira 的「三视图 + 场景图」锚定资产（与成片同源：MiniMax H3）。

为什么必须同源：后续出片把对应锚定图作为 I2V 首帧（image=），人物/场景由同一
模型分布生成，天然一致；不必再走「先随便出片、再后处理换脸补一致性」的补丁路线。

产物（outputs/anchor/）：
  mira_front.png / mira_side.png / mira_back.png   主角三视图（中性背景，便于参考）
  scene_*.png                                      各场景空镜（光线/空间/道具固定）
  mira_anchor.png                                  从正面图裁出的人脸（换脸/身份锚定）
  index.html                                       一页锚定图册

用法:
  python gen_anchor_assets.py                        # 全部生成
  python gen_anchor_assets.py --only mira_front      # 只跑某几张（逗号分隔）
  python gen_anchor_assets.py --resolution 768x448   # 显存不足时降分辨率
"""
from __future__ import annotations
import argparse
import os
import subprocess

import run_series as rs

ROOT = rs.ROOT
OUT = os.path.join(ROOT, "outputs", "anchor")
TMP = os.path.join(OUT, "_tmp")

# ---- Mira 固定形象（与 outputs/series_bible.json 的 fixed_appearance 一致）----
MIRA = ("a woman in her twenties, short dark hair, dark worn trench coat, "
        "subtle cybernetic ports on her knuckles, pale determined face")
STYLE = "cinematic, film grain, 35mm, dramatic lighting"
NEUTRAL = "plain soft neutral studio backdrop, even lighting"

SHOTS = [
    # --- 主角三视图：中性背景突出人物，供换脸锚定 / I2V 形象参考 ---
    ("mira_front",
     f"front view portrait of {MIRA}, neutral standing pose facing camera, "
     f"{NEUTRAL}, {STYLE}"),
    ("mira_side",
     f"side profile view of the same woman, {MIRA}, standing in profile facing right, "
     f"{NEUTRAL}, {STYLE}"),
    ("mira_back",
     f"back view of the same woman, {MIRA}, seen from behind, "
     f"{NEUTRAL}, {STYLE}"),
    # --- 场景图：第1集主要场景，锁住光线/空间/道具 ---
    ("scene_neon_street",
     f"wide establishing shot of a rain-soaked neon megacity street at night, "
     f"magenta and cyan signs reflecting on wet asphalt, no people, {STYLE}"),
    ("scene_shop_exterior",
     f"exterior of a small corner memory shop at night, neon sign in the rain, "
     f"rows of glowing small memory vials in the window, no people, {STYLE}"),
    ("scene_shop_interior",
     f"interior of a dim memory shop, shelves of glowing memory vials, a counter, "
     f"cold blue light, no people, {STYLE}"),
    ("scene_reading_chair",
     f"a memory reading chair in a dim room, cold blue scanning light, "
     f"no people, {STYLE}"),
    ("scene_meadow",
     f"flashback of a sunlit meadow, tall grass moving in warm wind, golden hour, "
     f"no people, {STYLE}"),
    ("scene_chip",
     f"close-up of a small blank black memory chip resting in an open palm, "
     f"cold blue rim light, {STYLE}"),
    ("scene_door",
     f"the shop door at night seen from inside, rain outside, warm light spilling out, "
     f"no people, {STYLE}"),
]


def extract_frame(mp4: str, png: str, nth: int = 2) -> None:
    """从短片里抽第 nth 帧（0-index）作静帧锚定图（避开可能偏暗的首帧）。"""
    subprocess.run(
        [rs.FFMPEG, "-y", "-i", mp4, "-vf", f"select=eq(n\\,{nth})",
         "-vframes", "1", png],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_face_anchor(front_png: str, dst: str) -> bool:
    """从正面图裁出人脸，作为换脸/身份锚定（与成片同源，优于旧锚定图）。"""
    try:
        import cv2
        from insightface.app import FaceAnalysis
        img = cv2.imread(front_png)
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        faces = app.get(img)
        if not faces:
            print("  [warn] 正面图未检出人脸，跳过锚定提取")
            return False
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        x1, y1, x2, y2 = f.bbox
        h, w = img.shape[:2]
        padx = (x2 - x1) * 0.6
        pady = (y2 - y1) * 0.6
        ax1 = max(0, int(x1 - padx)); ay1 = max(0, int(y1 - pady))
        ax2 = min(w, int(x2 + padx)); ay2 = min(h, int(y2 + pady))
        cv2.imwrite(dst, img[ay1:ay2, ax1:ax2])
        return True
    except Exception as e:
        print("  [warn] 锚定提取失败:", e)
        return False


def build_index() -> None:
    """生成一页锚定图册 index.html。"""
    items = []
    for name, prompt in SHOTS:
        if not os.path.exists(os.path.join(OUT, f"{name}.png")):
            continue
        items.append(f'<figure><img src="{name}.png" loading="lazy">'
                     f'<figcaption><b>{name}</b><br><span>{prompt}</span></figcaption></figure>')
    if os.path.exists(os.path.join(OUT, "mira_anchor.png")):
        items.append('<figure><img src="mira_anchor.png" loading="lazy">'
                     '<figcaption><b>mira_anchor</b><br>'
                     '<span>正面图裁出的人脸（换脸/身份锚定）</span></figcaption></figure>')
    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>锚定资产 · Mira 三视图 + 场景图</title>
<style>
 body{background:#111;color:#eee;font-family:system-ui,sans-serif;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px} p.sub{color:#999;margin:0 0 24px;font-size:13px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px}
 figure{margin:0;background:#1a1a1a;border-radius:10px;overflow:hidden}
 img{width:100%;display:block;background:#000}
 figcaption{padding:10px 12px;font-size:12px;line-height:1.5}
 figcaption span{color:#999}
</style></head><body>
<h1>锚定资产 · Mira 三视图 + 场景图</h1>
<p class="sub">同源：MiniMax H3 · 供后续 I2V 首帧锚定与身份/换脸锚定使用</p>
<div class="grid">__ITEMS__</div></body></html>"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html.replace("__ITEMS__", "".join(items)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolution", default="1024x576",
                    help="锚定图分辨率（32 整除；显存不足用 768x448）")
    ap.add_argument("--frames", type=int, default=5,
                    help="每段短片帧数（只取中间帧，5 帧足够且最快；H3 需 17n+5）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", default="", help="只跑指定图，逗号分隔")
    ap.add_argument("--no-anchor", action="store_true", help="跳过人脸锚定提取")
    a = ap.parse_args()

    w, h = (int(x) for x in a.resolution.lower().split("x"))
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)

    rs.set_workspace("mmh3")
    eng = rs.build_engine(w, h, a.frames, 24, "mmh3")
    if not eng.is_ready():
        print("[err] ComfyUI 未就绪（8188）")
        raise SystemExit(1)

    want = [s.strip() for s in a.only.split(",") if s.strip()]
    todo = [(n, p) for n, p in SHOTS if not want or n in want]
    print(f"[cfg] {a.resolution} · {a.frames}帧 · 共 {len(todo)} 张 -> {OUT}", flush=True)

    for i, (name, prompt) in enumerate(todo, 1):
        png = os.path.join(OUT, f"{name}.png")
        if os.path.exists(png):
            print(f"[SKIP] {name} 已存在（删掉可重出）", flush=True)
            continue
        mp4 = os.path.join(TMP, f"{name}.mp4")
        print(f"[{i}/{len(todo)}] {name}: {prompt[:70]}...", flush=True)
        eng.generate(prompt, mp4, seed=a.seed)
        extract_frame(mp4, png)
        print(f"  -> {png}  ({os.path.getsize(png) // 1024}KB)", flush=True)

    if not a.no_anchor and os.path.exists(os.path.join(OUT, "mira_front.png")):
        dst = os.path.join(OUT, "mira_anchor.png")
        if make_face_anchor(os.path.join(OUT, "mira_front.png"), dst):
            print("  -> 人脸锚定", dst, flush=True)

    build_index()
    print("DONE ->", os.path.join(OUT, "index.html"), flush=True)


if __name__ == "__main__":
    main()
