"""媒体预览：白名单内媒体 + 白模续集自包含播放页。"""
from __future__ import annotations

import os

from flask import Blueprint, request, send_file

from ..state import MEDIA, WHITE_PAGES, json_resp

bp = Blueprint("media", __name__)


@bp.route("/api/media")
def api_media():
    name = request.args.get("name", "")
    path = MEDIA.get(name)
    if not path or not os.path.exists(path):
        return json_resp({"error": f"无媒体: {name}"}, status=404)
    return send_file(path, mimetype="video/mp4" if path.endswith(".mp4") else "image/png")


@bp.route("/white/<name>")
def api_white(name):
    path = WHITE_PAGES.get(name)
    if not path or not os.path.isfile(path):
        return json_resp({"error": f"无白模播放页: {name}"}, status=404)
    return send_file(path, mimetype="text/html")
