"""发布：biliup 登录态 + B 站稿件管理（列表 / 改标题）。"""
from __future__ import annotations

import os

from flask import Blueprint, request

from ..state import WORKDIR, get_agent, json_resp, load_config

bp = Blueprint("bili", __name__)


@bp.route("/api/biliup")
def api_biliup():
    try:
        agent = get_agent()
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)
    ready = agent.publisher.is_ready()
    return json_resp({
        "ready": ready,
        "cookies_exist": os.path.exists(os.path.join(WORKDIR, "cookies.json")),
        "guide": agent.publisher.login_guide(),
    })


def _publisher_direct():
    """直接构造 Publisher（不依赖 MovieAgent 初始化，避免缺依赖拖垮稿件管理）。"""
    from agent.publisher import Publisher
    return Publisher(load_config(), WORKDIR)


@bp.route("/api/bili/videos")
def api_bili_videos():
    try:
        return json_resp(_publisher_direct().list_my_videos())
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@bp.route("/api/bili/video/<bvid>")
def api_bili_video_detail(bvid):
    try:
        return json_resp(_publisher_direct().get_video_detail(bvid))
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@bp.route("/api/bili/update", methods=["POST"])
def api_bili_update():
    body = request.get_json(force=True, silent=True) or {}
    bvid = str(body.get("bvid") or "").strip()
    title = body.get("title")
    if not bvid or not title or not str(title).strip():
        return json_resp({"ok": False, "error": "bvid 和 title 必填"}, status=400)
    try:
        return json_resp(_publisher_direct().update_video(
            bvid, title=str(title).strip(),
            desc=body.get("desc"), tag=body.get("tag")))
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


# 自动删除已放弃（2026-09-05）：B 站风控需人机验证（code=340022），无法脚本化。
# 删除请到创作中心 (https://member.bilibili.com) 手动操作。
