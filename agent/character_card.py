"""角色卡自动生成：从 series_bible 的人物小传自动产出角色卡（含出图提示词 + 可选肖像）。

出图需要视频/图像引擎（GPU），本地无 GPU 时只生成角色卡 JSON + 提示词，跳过出图（优雅降级）。
"""
from __future__ import annotations

import json
import os

from .style_presets import PRESETS


def _style_prompt(style):
    return PRESETS.get(style, {}).get("prompt", "")


def build_cards(bible, workdir=None, style="anime"):
    """从 bible['characters'] 生成角色卡列表。

    每张卡: {name, role, description, image_prompt, ref_image}(ref_image 初始 None)。
    bible['characters'] 元素可为字符串(仅名字)或带 name/role/description/visual 的字典。
    """
    chars = (bible or {}).get("characters", []) or []
    cards = []
    for c in chars:
        if isinstance(c, str):
            c = {"name": c}
        name = c.get("name") or "unnamed"
        role = c.get("role") or c.get("archetype") or ""
        desc = (c.get("description") or c.get("bio") or c.get("backstory") or "")
        visual = c.get("visual") or c.get("appearance") or ""
        img_prompt = ", ".join(x for x in [
            _style_prompt(style),
            f"character design sheet of {name}",
            visual,
            "full body, multiple views, consistent character design",
        ] if x)
        cards.append({
            "name": name,
            "role": role,
            "description": desc,
            "image_prompt": img_prompt,
            "ref_image": None,
        })
    return cards


def generate(bible, workdir, style="anime", engine=None):
    """产出角色卡；若 engine 就绪则尝试出肖像，否则 ref_image=None。

    返回 {cards, manifest, out_dir}。
    """
    cards = build_cards(bible, workdir=workdir, style=style)
    out_dir = os.path.join(workdir or ".", "character_cards")
    os.makedirs(out_dir, exist_ok=True)
    for card in cards:
        ref = None
        if engine is not None and getattr(engine, "is_ready", lambda: False)():
            try:
                out_path = os.path.join(out_dir, f"{card['name']}_card.png")
                res = engine.generate(card["image_prompt"], out_path, seed=0) \
                    if hasattr(engine, "generate") else None
                ref = res if isinstance(res, str) and os.path.exists(res) else out_path
                if not os.path.exists(ref):
                    ref = None
            except Exception:
                ref = None
        card["ref_image"] = ref
    manifest = os.path.join(out_dir, "character_cards.json")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"style": style, "cards": cards}, f, ensure_ascii=False, indent=2)
    return {"cards": cards, "manifest": manifest, "out_dir": out_dir}
