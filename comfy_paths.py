"""ComfyUI 路径解析（跨平台，环境变量可覆盖）。

项目原先在多个脚本里写死 `D:/ComfyUI/...`，换机器/换系统就会失效。本模块把
「ComfyUI 在哪」收敛成一处，供各脚本复用。

环境变量：
    COMFYUI_ROOT            ComfyUI 安装目录（含 main.py）
    COMFYUI_MODELS_DIR      models 目录
    COMFYUI_OUTPUT          ComfyUI 输出目录
    COMFYUI_CUSTOM_NODES    custom_nodes 目录

优先级：显式参数 > 环境变量 > 由 ComfyUI 根目录推导 > 候选路径探测 > 字面兜底
（字面兜底保留 D:/ComfyUI，仅为不改动老机器的既有行为）。
"""
from __future__ import annotations

import os
import sys

# 候选根目录：按顺序探测，命中「含 main.py」的目录即采用
_CANDIDATE_ROOTS = (
    "D:/ComfyUI",
    os.path.expanduser("~/ComfyUI"),
    "/workspace/ComfyUI",
    "./ComfyUI",
)


def comfyui_root(explicit: str = "") -> str:
    """定位 ComfyUI 安装目录；找不到返回空串。"""
    for c in (explicit, os.environ.get("COMFYUI_ROOT") or "") + _CANDIDATE_ROOTS:
        if c and os.path.exists(os.path.join(c, "main.py")):
            return os.path.abspath(c)
    return ""


def _first(*cands: str) -> str:
    for c in cands:
        if c:
            return os.path.abspath(c)
    return ""


def models_dir(explicit: str = "") -> str:
    """ComfyUI models 目录。"""
    root = comfyui_root()
    return _first(explicit, os.environ.get("COMFYUI_MODELS_DIR"),
                  os.path.join(root, "models") if root else "",
                  "D:/ComfyUI/models")


def output_dir(explicit: str = "") -> str:
    """ComfyUI 输出目录（工作流产物落盘处）。"""
    root = comfyui_root()
    return _first(explicit, os.environ.get("COMFYUI_OUTPUT"),
                  os.path.join(root, "output") if root else "",
                  "D:/ComfyUI/output")


def custom_nodes_dir(explicit: str = "") -> str:
    """ComfyUI custom_nodes 目录（官方示例工作流就在各节点包内）。"""
    root = comfyui_root()
    return _first(explicit, os.environ.get("COMFYUI_CUSTOM_NODES"),
                  os.path.join(root, "custom_nodes") if root else "",
                  "D:/ComfyUI/custom_nodes")


def python_exe(root: str = "") -> str:
    """优先用 ComfyUI 自带 venv 的解释器，找不到则退回当前解释器。"""
    root = root or comfyui_root()
    if root:
        for rel in ("venv/Scripts/python.exe", "venv/bin/python",
                    ".venv/Scripts/python.exe", ".venv/bin/python"):
            p = os.path.join(root, rel)
            if os.path.exists(p):
                return p
    return sys.executable


def ensure_repo_on_path() -> None:
    """把本文件所在目录（仓库根）加入模块搜索路径，便于脚本从任意 cwd 运行。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
