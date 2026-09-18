"""投稿元数据：标题候选 / 简介 / 标签 / 动态 的生成接口（GET /api/metadata）。

默认走**规则法**（即时返回，不阻塞请求）；`?llm=1` 时才调本地 LLM（可能较慢）。
产出附 `check` 静态校验（标题/标签/动态是否合 B 站规范）。
"""
from __future__ import annotations

from flask import Blueprint, request

from ..services import metadata as svc
from ..state import json_resp

bp = Blueprint("metadata", __name__)


@bp.route("/api/metadata")
def api_metadata():
    """?ep=N [&llm=1] -> 第 N 集投稿元数据 JSON（含 check）。"""
    try:
        ep = int(request.args.get("ep", "1"))
    except (TypeError, ValueError):
        return json_resp({"error": "ep 必须是整数"}, status=400)
    use_llm = request.args.get("llm") in ("1", "true", "yes")
    try:
        meta = svc.generate(ep, use_llm=use_llm)
    except SystemExit as e:                     # 剧本里没有该集
        return json_resp({"error": str(e)}, status=404)
    except Exception as e:                      # noqa: BLE001 - 生成失败不该让页面 500 无信息
        return json_resp({"error": f"生成失败：{e}"}, status=500)
    from agent import metadata as md
    meta["check"] = md.validate(meta)
    return json_resp(meta)
