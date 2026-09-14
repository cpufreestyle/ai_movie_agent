"""页面 / 时间轴 / 概览配置 / 运行日志。"""
from __future__ import annotations

import os
import time

import yaml
from flask import Blueprint, Response, request

from ..state import (
    CONFIG_PATH,
    HERE,
    MEDIA,
    WORKDIR,
    _lock,
    _state,
    get_agent,
    json_resp,
    load_config,
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
        # 重置已缓存的 agent，使其使用新配置
        with _lock:
            _state["agent"] = None
            _state["agent_error"] = None
        return json_resp({"ok": True})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


# ---------------- 运行日志 ----------------
@bp.route("/api/logs")
def api_logs():
    with _lock:
        return json_resp({
            "running": _state["running"],
            "logs": "".join(_state["logs"]),
            "result": _state["result"],
        })
