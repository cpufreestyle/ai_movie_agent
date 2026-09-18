"""ComfyUI 模型清单：一律以服务端**实际扫描到**的权重为准，不做纸面假设。

起因（2026-09-19 实测踩坑）：
    本仓库给动漫内容默认推荐 RealESRGAN_x4plus_anime_6B.pth，但本机
    models/upscale_models 里**只有** 4x-UltraSharp.pth。结果超分阶段因权重缺失
    失败，被上层「阶段失败就跳过」的逻辑静默吞掉 —— 用户以为做了超分，实际只
    做了重编码。这类「纸面模型 vs 真实模型」的错位无法靠读磁盘发现（路径还会
    因 ComfyUI 配置而变），只有问服务端 /object_info 才知道。

故：任何要用权重的阶段，先查 /object_info 拿 COMBO 选项，缺失就回退到真实存在
的权重，并把回退原因明确报出来（不静默）。
"""
from __future__ import annotations

import json as _json
import re
import urllib.parse
import urllib.request

API_DEFAULT = "http://127.0.0.1:8188"
DEFAULT_RIFE = "rife49.pth"


def _open(url: str, timeout: float):
    """打开 URL，**绕过代理**。

    环境注入了 HTTP_PROXY（本机走 127.0.0.1:7897），urllib 默认会拿它去代理
    127.0.0.1:8188，表现为「ComfyUI 明明在线却查不到任何模型」（实测 rife_ckpts()
    返回 []）。故显式用空 ProxyHandler，与 comfyui_client 的做法一致。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url, timeout=timeout)


def object_info(api: str, node: str, timeout: float = 8.0) -> dict:
    """查 /object_info/<node>；失败返回 {}。"""
    url = f"{api.rstrip('/')}/object_info/{urllib.parse.quote(node)}"
    try:
        with _open(url, timeout) as r:
            return _json.loads(r.read().decode("utf-8")) or {}
    except Exception:
        return {}


def _combo_options(info: dict, node: str, field: str) -> list[str]:
    """取 COMBO 字段的候选值。

    ComfyUI 不同节点/插件的 COMBO 有**两种**形态（实测）：
        形态 A（多数官方节点）： ['COMBO', {'options': [...]}]
        形态 B（部分第三方插件）：[['a.pth', 'b.pth'], {'default': 'a.pth'}]
    只认形态 A 会在 B 上静默返回空 → 表现为「明明装了权重却查不到」。
    """
    try:
        entry = info[node]["input"]["required"][field]
    except Exception:
        return []
    if not isinstance(entry, list):
        return []
    for item in entry:                                   # 形态 B：直接的列表
        if isinstance(item, list):
            return [str(x) for x in item]
    for item in entry:                                   # 形态 A：dict 里的 options
        if isinstance(item, dict) and isinstance(item.get("options"), list):
            return [str(x) for x in item["options"]]
    return []


def upscale_models(api: str = API_DEFAULT, timeout: float = 8.0) -> list[str]:
    return _combo_options(object_info(api, "UpscaleModelLoader", timeout),
                          "UpscaleModelLoader", "model_name")


def rife_ckpts(api: str = API_DEFAULT, timeout: float = 8.0) -> list[str]:
    return _combo_options(object_info(api, "RIFE VFI", timeout), "RIFE VFI", "ckpt_name")


def pick_sr_model(kind: str = "real", api: str = API_DEFAULT, explicit: str = "",
                  available: list[str] | None = None) -> tuple[str, str]:
    """选超分权重。返回 (模型名, 说明)；说明非空即表示发生了回退/告警。"""
    from agent import encode as enc
    want = explicit or enc.sr_model_for(kind)
    avail = upscale_models(api, timeout=8.0) if available is None else available
    if not avail:
        return want, ""
    if want in avail:
        return want, ""
    for cand in enc.SR_MODELS.get(kind, []):
        if cand in avail:
            return cand, f"缺失 {want}，已回退到实际可用的 {cand}"
    return avail[0], f"缺失 {want}，已回退到实际可用的 {avail[0]}"


def _rife_rank(name: str) -> int:
    """rife47 -> 407, rife49 -> 409, rife417 -> 417, rife426 -> 426。非标准名 0。"""
    m = re.search(r"rife(\d)(\d{1,2})\.pth", name or "")
    if not m:
        return 0
    return int(m.group(1)) * 100 + int(m.group(2))


def pick_rife_ckpt(api: str = API_DEFAULT, explicit: str = "",
                   available: list[str] | None = None) -> tuple[str, str]:
    """选 RIFE 权重：默认取**版本号最大的**（新版本插值质量更好）。"""
    if explicit:
        return explicit, ""
    avail = rife_ckpts(api, timeout=8.0) if available is None else available
    if not avail:
        return DEFAULT_RIFE, ""
    ranked = [n for n in avail if _rife_rank(n) > 0]
    if not ranked:
        return DEFAULT_RIFE if DEFAULT_RIFE in avail else avail[0], ""
    best = max(ranked, key=_rife_rank)
    note = "" if best == DEFAULT_RIFE else f"本机有更新版 {best}（优于默认 {DEFAULT_RIFE}），已改用"
    return best, note
