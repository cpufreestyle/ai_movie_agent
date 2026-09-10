"""ComfyUI 质量后处理（超分 + 锐化）—— LTX-2.5 / MiniMax H3 共用。

这段逻辑原先在 agent/ltx_engine.py 的 `_apply_post()` 与 agent/mmh3_engine.py 的
`_build_workflow()` 里各写了一遍，两次踩坑都因此漏改：

  1. 新节点 ID 撞上 ref_images 的 `"2%d" % i`（20~28），使 `ref_image_i` 指向后处理
     输出，形成依赖环（mmh3 已改用 30/31/32）；
  2. 把 ImageSharpen 的强度参数写成 `sharpen`，而它实际叫
     `sharpen_radius` / `sigma` / `alpha`（comfy_extras/nodes_post_processing.py），
     导致 /prompt 提交直接报错。

现收敛到一处，避免第三次。只增强图像链路，**音频输入保持原样**（避免音画不同步）；
帧插值(RIFE)会改变帧率，故不在此自动接入，作为离线增强单独提供。
"""
from __future__ import annotations

from typing import Callable

# ImageSharpen 的真实参数：sharpen_radius / sigma / alpha（没有 `sharpen`）。
# config 里的 post.sharpen 是 0~1 的"强度"，映射到 alpha。
DEFAULT_SHARPEN_RADIUS = 1
DEFAULT_SHARPEN_SIGMA = 1.0

# ComfyUI 自带的视频保存节点类型（两个引擎都要认这些）
VIDEO_SAVER_TYPES = ("VHS_VideoCombine", "SaveAnimatedWEBM", "VHS_SaveImageSequence")


def read_post_cfg(post: dict | None) -> tuple[str, float]:
    """从 config 的 post 段读出 (超分模型名, 锐化强度)；强度下限 0。

    post.upscale_model 留空 = 不做超分（不会报错）；post.sharpen <= 0 = 不做锐化。
    """
    post = post or {}
    upscale = (post.get("upscale_model") or "").strip()
    sharpen = max(0.0, float(post.get("sharpen", 0.0) or 0.0))
    return upscale, sharpen


def build_post_nodes(wf: dict, images_src: list, *, upscale: str = "",
                     sharpen: float = 0.0,
                     alloc: Callable[[str], str]) -> list:
    """在 wf 里插入超分/锐化节点，返回新的 images 源引用 `[node_id, slot]`。

    `alloc(name)` 负责分配不冲突的节点 ID，name ∈
    {"upscale_loader", "upscale_apply", "sharpen"}。

    只建节点、不改保存节点——保存节点晚于此处构建的调用方（如 mmh3）请用本函数，
    之后再自行把返回值接到保存节点的 images 输入上。
    """
    cur = images_src
    if upscale:
        loader = alloc("upscale_loader")
        wf[loader] = {"class_type": "UpscaleModelLoader",
                      "inputs": {"model_name": upscale}}
        apply_nd = alloc("upscale_apply")
        wf[apply_nd] = {"class_type": "ImageUpscaleWithModel",
                        "inputs": {"images": cur, "upscale_model": [loader, 0]}}
        cur = [apply_nd, 0]
    if sharpen > 0:
        sharp_nd = alloc("sharpen")
        wf[sharp_nd] = {"class_type": "ImageSharpen", "inputs": {
            "image": cur,
            "sharpen_radius": DEFAULT_SHARPEN_RADIUS,
            "sigma": DEFAULT_SHARPEN_SIGMA,
            "alpha": sharpen}}
        cur = [sharp_nd, 0]
    return cur


def apply_post(wf: dict, saver_id: str, *, upscale: str = "", sharpen: float = 0.0,
               alloc: Callable[[str], str]) -> list | None:
    """把超分/锐化插到 saver_id 的 images 输入之前，并改写该输入。

    返回新的 images 源引用；未启用或保存节点没有合法的 images 输入时返回 None
    （调用方据此跳过并保持工作流原样）。
    """
    if not upscale and sharpen <= 0:
        return None
    src = (wf.get(saver_id, {}) or {}).get("inputs", {}).get("images")
    if not isinstance(src, list) or len(src) != 2:
        return None
    cur = build_post_nodes(wf, src, upscale=upscale, sharpen=sharpen, alloc=alloc)
    wf[saver_id].setdefault("inputs", {})["images"] = cur
    return cur


def find_video_saver(wf: dict) -> str | None:
    """定位最终视频保存节点。

    用户工作流的节点 ID 是任意的（如 5508 / 5014_5506），只能按类型找。
    """
    for nid, n in wf.items():
        if isinstance(n, dict) and n.get("class_type") in VIDEO_SAVER_TYPES:
            return nid
    return None


def describe(upscale: str = "", sharpen: float = 0.0) -> str:
    """生成一行中文描述，供各引擎日志复用。"""
    parts = []
    if upscale:
        parts.append(f"超分({upscale})")
    if sharpen > 0:
        parts.append("锐化")
    return "+".join(parts)
