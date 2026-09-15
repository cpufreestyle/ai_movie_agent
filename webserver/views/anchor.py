"""锚定资产：三视图 / 场景图的一键生成与预览（gen_anchor_assets.py，H3 同源）。"""
from __future__ import annotations

import os
import subprocess
import sys

from flask import Blueprint, request, send_file

from ..services.anchor import (
    DEFAULT_RESOLUTION,
    RESOLUTIONS,
    anchor_dir,
    artifacts,
    comfy_api,
    comfy_ready,
    default_names,
    derived_items,
    generatable_names,
    has_index,
    safe_path,
    shot_groups,
)
from ..state import HERE, _state, json_resp, run_in_background

bp = Blueprint("anchor", __name__)

#: 生成脚本（仓库根目录）。与成片同源：H3 出图，人物/场景与后续出片同一分布。
SCRIPT = "gen_anchor_assets.py"


@bp.route("/api/anchor")
def api_anchor():
    """锚定资产现状：条目目录（含是否已存在）+ ComfyUI 就绪 + 产物图册。"""
    api = comfy_api()
    return json_resp({
        "dir": os.path.relpath(anchor_dir(), HERE).replace("\\", "/"),
        "script": SCRIPT,
        "api": api,
        "ready": comfy_ready(api),
        "resolutions": list(RESOLUTIONS),
        "resolution": DEFAULT_RESOLUTION,
        "defaults": default_names(),
        "groups": shot_groups(),
        "derived": derived_items(),
        "artifacts": artifacts(),
        "has_index": has_index(),
    })


@bp.route("/api/anchor/run", methods=["POST"])
def api_anchor_run():
    """一键生成：默认只出三视图；已存在的图默认跳过（勾「重新生成」可强制重出）。

    body: {"only": [名称...], "force": bool, "resolution": "1024x576"}
    名称白名单过滤 —— 只允许 gen_anchor_assets.SHOTS 里的条目，避免被拼出任意参数。
    """
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}

    known = set(generatable_names())
    only = [str(x) for x in (body.get("only") or []) if str(x) in known]
    if not only:
        only = default_names()          # 没选就按默认（三视图）
    res = str(body.get("resolution") or DEFAULT_RESOLUTION)
    if res not in RESOLUTIONS:
        return json_resp({"ok": False,
                          "error": f"不支持的分辨率：{res}（可选 {'/'.join(RESOLUTIONS)}）"},
                         status=400)
    force = bool(body.get("force"))

    argv = [sys.executable, "-u", SCRIPT,
            "--only", ",".join(only), "--resolution", res]
    if force:
        argv.append("--force")
    mode = "强制重出" if force else "补齐缺失"

    def _job():
        print(f"\n=== 锚定资产 · {mode} · {len(only)} 张 ===")
        print("[cmd] " + " ".join(argv))
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run(argv, cwd=HERE, capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
        if r.stdout:
            print(r.stdout)
        if r.stderr:
            print(r.stderr[-4000:])
        ok = r.returncode == 0
        if not ok:
            print(f"[err] {SCRIPT} 退出码 {r.returncode}"
                  "（ComfyUI 8188 未就绪时会直接退出）")
        made = [x["name"] for x in artifacts()]
        return {"ok": ok, "only": only, "force": force, "resolution": res,
                "files": made}

    run_in_background(_job)
    skip_note = "已存在的会被跳过" if not force else "已存在的也会重出"
    return json_resp({
        "ok": True, "only": only, "force": force, "resolution": res,
        "msg": f"已启动锚定资产生成（{mode}）：{len(only)} 张，{skip_note}；看下方日志",
    })


@bp.route("/api/anchor/file")
def api_anchor_file():
    """预览 anchor 目录内的产物（防路径穿越）。"""
    path = safe_path(request.args.get("name", ""))
    if not path:
        return json_resp({"error": "非法路径"}, status=400)
    if not os.path.isfile(path):
        return json_resp({"error": "文件不存在"}, status=404)
    return send_file(path, mimetype="image/png", conditional=True)


@bp.route("/api/anchor/index")
def api_anchor_index():
    """回传脚本生成的锚定图册 index.html（不存在则 404，界面据此隐藏入口）。"""
    if not has_index():
        return json_resp({"error": "锚定图册 index.html 不存在"}, status=404)
    return send_file(os.path.join(anchor_dir(), "index.html"), mimetype="text/html")
