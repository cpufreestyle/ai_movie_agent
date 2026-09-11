"""风格预设库：一组可复用的视觉风格预设（提示词后缀 + 负向 + 备注）。

不修改 config.yaml（避免与其它编辑会话冲突）。apply/render 直接返回拼好的提示词
字符串，可手写进 image_prompt 或导出文件。
"""
from __future__ import annotations

PRESETS = {
    "anime": {
        "label": "日式动漫（默认）",
        "prompt": "anime style, cel-shaded 2D animation, clean line art, vibrant colors, detailed background art, dramatic lighting, slow camera movement",
        "negative": "realistic, photo, 3d render, low quality, blurry, watermark",
        "notes": "项目默认风格；干净赛璐珞、鲜艳配色。",
    },
    "anime_film": {
        "label": "动漫电影感",
        "prompt": "anime film aesthetic, soft gradients, film grain, shallow depth of field, cinematic composition, gentle light leaks",
        "negative": "flat color, harsh edges, low quality",
        "notes": "比默认更电影化，柔光 + 浅景深。",
    },
    "cyber_anime": {
        "label": "赛博动漫",
        "prompt": "cyberpunk anime, neon lights, high contrast, rain-slick streets, chromatic aberration, volumetric fog, reflective puddles",
        "negative": "daylight, pastel, low contrast",
        "notes": "适配本项目赛博都市题材。",
    },
    "watercolor": {
        "label": "水彩手绘",
        "prompt": "hand-painted watercolor, paper texture, soft edges, visible brush strokes, muted pastel palette",
        "negative": "sharp lines, 3d, cgi",
        "notes": "文艺 / 治愈向。",
    },
    "pixel": {
        "label": "像素风",
        "prompt": "pixel art, limited color palette, dithering, crisp pixels, retro game aesthetic",
        "negative": "smooth gradients, realistic",
        "notes": "复古游戏感。",
    },
    "noir": {
        "label": "黑白黑色电影",
        "prompt": "black and white film noir, high contrast chiaroscuro, heavy fog, venetian shadows, 1940s aesthetic",
        "negative": "color, vibrant, low contrast",
        "notes": "悬疑 / 硬汉向。",
    },
    "ghibli": {
        "label": "吉卜力风",
        "prompt": "ghibli-inspired, lush natural backgrounds, warm soft lighting, hand-drawn feel, detailed foliage",
        "negative": "cg, 3d, harsh",
        "notes": "自然 / 治愈向。",
    },
    "ink": {
        "label": "水墨写意",
        "prompt": "sumi-e ink wash painting, monochrome, flowing brush, negative space, minimalist",
        "negative": "color, heavy outlines",
        "notes": "东方写意。",
    },
}


def list_presets():
    return sorted(PRESETS.keys())


def get(name):
    return PRESETS.get(name)


def render(name, base=""):
    """把预设拼进 base 提示词，返回完整正向提示词。"""
    p = PRESETS.get(name)
    if not p:
        raise KeyError(f"未知风格预设: {name}（可用: {list_presets()}）")
    base = (base or "").strip()
    return (base + ", " + p["prompt"]).strip(", ") if base else p["prompt"]


def negative_of(name):
    p = PRESETS.get(name)
    return p["negative"] if p else ""


def apply(name, base="", out=None):
    """返回 {label, prompt, negative}；out 给定时把 prompt 写出到文件。"""
    p = PRESETS.get(name)
    if not p:
        raise KeyError(f"未知风格预设: {name}")
    prompt = render(name, base)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(prompt + "\n")
    return {"label": p["label"], "prompt": prompt, "negative": p["negative"]}
