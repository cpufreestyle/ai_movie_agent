"""系列连贯出片（尾帧续写）路由：run_series.py 的 WebUI 入口。"""
from __future__ import annotations

import os
import subprocess
import sys

from flask import Blueprint, request

from ..services.series import (
    ANCHORS,
    ANCHOR_MODES,
    DEFAULT_ANCHOR_MODE,
    DEFAULT_ENGINE,
    ENGINES,
    EPISODES,
    build_argv,
    default_anchor,
    out_dir,
    series_films,
    validate,
)
from ..state import HERE, _state, json_resp, run_in_background

bp = Blueprint("series", __name__)


def _pairs(items) -> list[dict]:
    return [{"value": v, "label": label} for v, label in items]


@bp.route("/api/series")
def api_series():
    """系列出片的可选值与现有产物（供表单初始化 + 展示）。"""
    return json_resp({
        "episodes": _pairs(EPISODES),
        "engines": _pairs(ENGINES),
        "anchor_modes": _pairs(ANCHOR_MODES),
        "anchors": _pairs(ANCHORS),
        "ep": 0,
        "engine": DEFAULT_ENGINE,
        "anchor_mode": DEFAULT_ANCHOR_MODE,
        "anchor": default_anchor(),
        "i2v": False,
        "out_dir": os.path.relpath(out_dir(), HERE).replace("\\", "/"),
        "films": series_films(),
        "running": bool(_state["running"]),
    })


@bp.route("/api/series/run", methods=["POST"])
def api_series_run():
    """启动 run_series.py（尾帧续写 / 只重出指定镜 / 只拼接）。

    body: {ep, engine, i2v, anchor_mode, anchor, only, force, concat}
    """
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}

    errors = validate(body)
    if errors:
        return json_resp({"ok": False, "error": "；".join(errors), "errors": errors},
                         status=400)

    argv = build_argv(body)
    mode = ("只拼接第 %s 集" % body.get("concat")) if body.get("concat") else (
        ("只重出第 %s 镜" % body.get("only")) if str(body.get("only") or "").strip() else
        ("尾帧续写出片" if body.get("i2v") else "出片"))

    def _job():
        print(f"\n=== 系列出片 · {mode} ===")
        print("[cmd] " + " ".join(argv))
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, "-u", *argv], cwd=HERE,
                           capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
        if r.stdout:
            print(r.stdout)
        if r.stderr:
            print(r.stderr[-4000:])
        ok = r.returncode == 0
        if not ok:
            print(f"[err] {argv[0]} 退出码 {r.returncode}")
        return {"ok": ok, "argv": argv, "files": [x["name"] for x in series_films()]}

    run_in_background(_job)
    return json_resp({"ok": True, "msg": f"已启动系列出片（{mode}）；看下方日志",
                      "argv": argv})
