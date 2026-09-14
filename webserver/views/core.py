"""页面 / 时间轴 / 概览配置 / 运行日志。"""
from __future__ import annotations

import os
import time

from flask import Blueprint, Response, request

from ..state import (
    CONFIG_PATH,
    HERE,
    MEDIA,
    WORKDIR,
    _lock,
    _state,
    get_agent,
    invalidate_config_cache,
    json_resp,
    load_config,
    logbuf,
)

bp = Blueprint("core", __name__)


# ---------------- 页面 ----------------
@bp.route("/")
def index():
    html_path = os.path.join(HERE, "webui", "pipeline.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


# ---------------- 时间轴（WebUI） ----------------
from agent import timeline as _tl


@bp.route("/timeline")
def timeline_page():
    html_path = os.path.join(HERE, "webui", "timeline.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@bp.route("/api/timeline", methods=["GET"])
def api_timeline_get():
    return json_resp(_tl.load_timeline(WORKDIR))


@bp.route("/api/timeline", methods=["POST"])
def api_timeline_post():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("shots"), list):
        return json_resp({"ok": False, "error": "body 需含 shots 数组"}, status=400)
    body["updated"] = int(time.time())
    try:
        _tl.save_timeline(WORKDIR, body)
        return json_resp({"ok": True, "shots": len(body["shots"])})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@bp.route("/api/timeline/build", methods=["POST"])
def api_timeline_build():
    try:
        data = _tl.build_timeline(WORKDIR)
        _tl.save_timeline(WORKDIR, data)
        return json_resp({"ok": True, "shots": len(data["shots"])})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


# ---------------- 概览 / 配置 ----------------
@bp.route("/api/overview")
def overview():
    try:
        agent = get_agent()
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)
    st = agent.status()
    bili = agent.publisher
    st["concept_demo"] = os.path.exists(MEDIA["concept_demo"])
    st["concept_cover"] = os.path.exists(MEDIA["concept_cover"])
    st["film_exists"] = os.path.exists(MEDIA["film"])
    st["movie_final_exists"] = os.path.exists(MEDIA["movie_final"])
    st["biliup_ready"] = bili.is_ready()
    st["cookies_exist"] = os.path.exists(os.path.join(WORKDIR, "cookies.json"))
    return json_resp(st)


@bp.route("/api/config", methods=["GET"])
def get_config():
    return json_resp(load_config())


@bp.route("/api/config", methods=["POST"])
def save_config():
    import yaml
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return json_resp({"ok": False, "error": "body 必须是 JSON 对象"}, status=400)
    # 备份原配置
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as src:
                with open(CONFIG_PATH + ".bak", "w", encoding="utf-8") as f:
                    f.write(src.read())
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
        # 重置已缓存的 agent 与配置，使其使用新配置
        invalidate_config_cache()
        with _lock:
            _state["agent"] = None
            _state["agent_error"] = None
        return json_resp({"ok": True})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


# ---------------- 运行日志 ----------------
@bp.route("/api/logs")
def api_logs():
    """运行状态 + 日志。

    ?since=<cursor> 只取新增部分（前端轮询用），不传则返回当前保留的全部内容
    （老调用方行为不变）。响应里的 cursor 应原样回传作为下次的 since。
    """
    since = request.args.get("since", type=int)
    with _lock:
        running = bool(_state["running"])
        result = _state["result"]
    data = logbuf.read(since)
    return json_resp({
        "running": running,
        "logs": data["text"],
        "cursor": data["cursor"],
        "truncated": data["truncated"],
        "reset": data["reset"],
        "result": result,
    })
