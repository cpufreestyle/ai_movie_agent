"""投稿元数据（WebUI 服务层）：脚本路径解析 + 生成薄封装。

刻意不在模块顶层 import `agent.metadata`（避免 Web 启动路径拖入项目重依赖），
按需惰性 import —— 与 services/anchor.py 的做法一致。
"""
from __future__ import annotations

import os

from ..state import WORKDIR


def script_path() -> str:
    """系列剧本路径（outputs/series_script.json）。"""
    return os.path.join(WORKDIR, "series_script.json")


def generate(ep: int, use_llm: bool = False) -> dict:
    """生成第 ep 集投稿元数据。use_llm=False 时走规则法（即时、确定性）。"""
    from agent import metadata as md
    config: dict = {}
    if use_llm:
        from ..state import load_config
        config = load_config()
    return md.generate(ep, script_path=script_path(), config=config, use_llm=use_llm)
