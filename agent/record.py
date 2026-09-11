"""逐镜生成参数全量落盘（可复现）。

现状：manifest 里只存每镜最终视频路径，**丢了 engine/model/seed/resolution/steps/
lora/post/negative** 等。要「复现某一镜」或做 A/B 对比（建议 #9）、回归测试，必须先
把这次生成用的全部参数记下来。

为不破坏现有 `manifest.json`（其它消费方假定 `man[key] = 路径字符串`），参数单独存到
同目录的 `gen_params.json`：
    { shot_key: {engine, resolution, num_frames, fps, steps, lora, negative,
                 two_pass, block_cache, seed, attempt, image, ref_images,
                 style_anchor, prompt, qa:{...}, generated_at} }
"""
from __future__ import annotations

import json
import os
import time


def _engine_name(eng) -> str:
    cls = eng.__class__.__name__
    if "MMH3" in cls:
        return "mmh3"
    if "LTX" in cls:
        return "ltx"
    return cls


def collect(eng, *, prompt: str, seed: int, attempt: int,
            image=None, ref_images=None, ref_video=None, style_anchor=None,
            qa_policy: dict | None = None) -> dict:
    """从引擎与调用上下文收集本次生成的全量参数。"""
    def _attr(name, default=None):
        return getattr(eng, name, default)

    p = {
        "engine": _engine_name(eng),
        "resolution": _attr("resolution"),
        "num_frames": _attr("num_frames"),
        "fps": _attr("fps"),
        "steps": _attr("steps"),
        "lora": _attr("lora"),
        "negative": _attr("negative"),
        "two_pass": _attr("two_pass"),
        "block_cache": _attr("block_cache"),
        "seed": seed,
        "attempt": attempt,
        "image": os.path.basename(image) if image else None,
        "ref_images": [os.path.basename(r) for r in (ref_images or [])],
        # 白模灰模动画（ref_video）也要落盘：否则这一镜的参考素材无法完整复现
        "ref_video": os.path.basename(ref_video) if ref_video else None,
        "style_anchor": os.path.basename(style_anchor) if style_anchor else None,
        "prompt": prompt,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if qa_policy:
        p["qa"] = {
            "enabled": qa_policy.get("enabled"),
            "max_rerolls": qa_policy.get("max_rerolls"),
            "min_sharpness": qa_policy.get("min_sharpness"),
            "min_motion": qa_policy.get("min_motion"),
        }
    # None 值不落盘，保持文件紧凑
    return {k: v for k, v in p.items() if v is not None}


def save(work_dir: str, key: str, params: dict) -> None:
    """把某镜参数并入同目录 gen_params.json（多个镜头累加，不互相覆盖）。"""
    path = os.path.join(work_dir, "gen_params.json")
    data: dict = {}
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8")) or {}
        except Exception:             # noqa: BLE001 - 旧文件损坏则重建
            data = {}
    data[key] = params
    json.dump(data, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def load(work_dir: str) -> dict:
    """读取整个 gen_params.json；文件不存在/损坏返回 {}。"""
    path = os.path.join(work_dir, "gen_params.json")
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8")) or {}
    except Exception:             # noqa: BLE001
        return {}
